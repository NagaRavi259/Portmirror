"""Forward lifecycle: DB and kernel change together, or not at all."""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from . import config, engine, quota
from .models import Forward, ForwardIn
from .store import Store

log = logging.getLogger("pm.service")


class Manager:
    def __init__(self, store: Store, notifier=None):
        self.store = store
        self.notifier = notifier
        self.lock = asyncio.Lock()
        self._cache: list[Forward] = store.forwards()
        self.on_change = lambda: None   # collector bumps its version here
        self.last_apply: Optional[str] = None
        # last window state applied per forward - the schedule only acts when a boundary is crossed,
        # so a manual change made inside a window holds until the next boundary, not every 5 s
        self._window_seen: dict[int, bool] = {}
        # data-quota state: current usage per forward, and which forwards the quota itself switched off
        self.quota_usage: dict[int, int] = {}
        # bytes in the current minute, not yet in the database - wired to the collector by main
        self.live_bytes = lambda fid: 0
        self._quota_disabled: set[int] = set()

    def forwards(self) -> list[Forward]:
        return self._cache

    def _refresh(self):
        self._cache = self.store.forwards()
        self.on_change()

    async def _apply(self):
        await engine.apply(self.store.forwards())
        self.last_apply = datetime.now(timezone.utc).isoformat()

    async def reapply(self, actor: str = "system", reason: str = "reapply"):
        async with self.lock:
            await self._apply()
            self._refresh()
        self.store.audit(actor, reason)

    # ---- CRUD -------------------------------------------------------------
    async def create(self, fin: ForwardIn, actor: str) -> Forward:
        async with self.lock:
            f = self.store.create(fin)
            try:
                await self._apply()
            except engine.NftError:
                self.store.delete(f.id)
                raise
            self._refresh()
        self.store.audit(actor, "forward.create", f"#{f.id} {f.name}", fin.model_dump(mode="json"))
        return f

    async def update(self, fid: int, fin: ForwardIn, actor: str, kill: bool = False) -> Forward:
        async with self.lock:
            old = self.store.forward(fid)
            if old is None:
                raise KeyError(fid)
            if not fin.enabled:
                self._quota_disabled.discard(fid)
            f = self.store.update(fid, fin)
            try:
                await self._apply()
            except engine.NftError:
                self.store.restore(old)
                raise
            self._refresh()
        killed = await engine.kill_forward_flows(old) if kill else 0
        self.store.audit(actor, "forward.update", f"#{fid} {f.name}",
                         {"before": _diffable(old), "after": _diffable(f), "killed": killed})
        return f

    async def delete(self, fid: int, actor: str, kill: bool = False) -> int:
        async with self.lock:
            old = self.store.forward(fid)
            if old is None:
                raise KeyError(fid)
            self.store.delete(fid)
            try:
                await self._apply()
            except engine.NftError:
                self.store.restore(old)
                raise
            self._refresh()
        killed = await engine.kill_forward_flows(old) if kill else 0
        self.store.audit(actor, "forward.delete", f"#{fid} {old.name}", {"kill": kill, "killed": killed})
        return killed

    async def toggle(self, fid: int, enabled: bool, actor: str, kill: bool = False) -> Forward:
        if actor != "system":
            self._quota_disabled.discard(fid)   # a person has taken control; a quota reset must not override them
        old = self.store.forward(fid)
        if old is None:
            raise KeyError(fid)
        fin = ForwardIn(**old.model_dump(exclude={"id", "created_at", "updated_at", "enabled"}), enabled=enabled)
        if enabled and old.expired():
            fin.expires_at = None      # re-enabling an expired forward clears the expiry
        async with self.lock:
            f = self.store.update(fid, fin)
            try:
                await self._apply()
            except engine.NftError:
                self.store.restore(old)
                raise
            self._refresh()
        killed = await engine.kill_forward_flows(old) if (kill and not enabled) else 0
        self.store.audit(actor, "forward.enable" if enabled else "forward.disable", f"#{fid} {f.name}",
                         {"killed": killed} if not enabled else None)
        return f

    async def import_forwards(self, items: list[ForwardIn], mode: str, actor: str) -> dict:
        # validate conflicts among the incoming set itself first
        pool = [] if mode == "replace" else [f for f in self.store.forwards()]
        for i, a in enumerate(items):
            for b in pool + items[:i]:
                if a.enabled and b.enabled and a.overlaps(b):
                    from .store import ConflictError
                    raise ConflictError(f"import item '{a.name}' conflicts with '{b.name}'")
        async with self.lock:
            before = self.store.forwards()
            if mode == "replace":
                self.store.replace_all(items)
            else:
                for f in items:
                    self.store.create(f)
            try:
                await self._apply()
            except engine.NftError:
                self.store.replace_all([])
                for f in before:
                    self.store.restore(f)
                raise
            self._refresh()
        self.store.audit(actor, "forward.import", mode, {"count": len(items)})
        return {"imported": len(items), "mode": mode}

    # ---- background ---------------------------------------------------------
    async def seed_if_empty(self):
        if self.store.forwards() or self.store.meta("seeded"):
            return
        if config.SEED_FILE.exists():
            items = [ForwardIn(**x) for x in json.loads(config.SEED_FILE.read_text())]
            for f in items:
                self.store.create(f)
            self.store.audit("system", "forward.seed", str(config.SEED_FILE), {"count": len(items)})
        self.store.set_meta("seeded", "1")
        self._refresh()

    async def housekeeping_loop(self):
        """Every 5s: disable expired forwards; repair kernel drift unless tests hold us off."""
        last_purge = 0.0
        while True:
            await asyncio.sleep(5)
            try:
                if config.AUDIT_RETENTION_S and time.time() - last_purge > 3600:
                    last_purge = time.time()
                    cutoff = datetime.fromtimestamp(time.time() - config.AUDIT_RETENTION_S, timezone.utc).isoformat()
                    n = self.store.purge_audit(cutoff)
                    if n:
                        log.info("audit retention: removed %d entries older than %s", n, cutoff)
                for f in self.store.forwards():
                    if f.enabled and f.expired():
                        await self.toggle(f.id, False, "system", kill=False)
                        self.store.audit("system", "forward.expire", f"#{f.id} {f.name}")
                        if self.notifier:
                            self.notifier.notify("forward_expired", "info", f"“{f.name}” turned off - its expiry time passed",
                                                 {"forward_id": f.id})
                for f in self.store.forwards():
                    if f.access_window is None or f.expired():
                        self._window_seen.pop(f.id, None)
                        continue
                    inside = f.access_window.contains(datetime.now())
                    if self._window_seen.get(f.id) != inside:
                        self._window_seen[f.id] = inside
                        if f.enabled != inside:
                            await self.toggle(f.id, inside, "system", kill=False)
                            self.store.audit("system", "forward.window_on" if inside else "forward.window_off",
                                             f"#{f.id} {f.name}")
                now = datetime.now()
                for f in self.store.forwards():
                    if f.quota is None:
                        self.quota_usage.pop(f.id, None)
                        self._quota_disabled.discard(f.id)
                        continue
                    if f.expired():
                        continue   # expiry owns this forward's state; a quota reset must not re-enable it
                    used = (self.store.usage_since(f.id, int(quota.period_start(now, f.quota.period).timestamp()))
                            + self.live_bytes(f.id))
                    self.quota_usage[f.id] = used
                    action = quota.quota_action(used, f.quota.bytes, f.enabled, f.id in self._quota_disabled)
                    if action == "disable":
                        await self.toggle(f.id, False, "system", kill=False)
                        newly = f.id not in self._quota_disabled
                        self._quota_disabled.add(f.id)
                        self.store.audit("system", "forward.quota_reached", f"#{f.id} {f.name}", {"used": used, "limit": f.quota.bytes})
                        if newly and self.notifier:
                            self.notifier.notify("quota_reached", "warning",
                                                 f"\u201c{f.name}\u201d reached its {f.quota.period} data quota and was turned off",
                                                 {"forward_id": f.id, "used": used, "limit": f.quota.bytes})
                    elif action == "enable":
                        await self.toggle(f.id, True, "system", kill=False)
                        self._quota_disabled.discard(f.id)
                        self.store.audit("system", "forward.quota_reset", f"#{f.id} {f.name}")
                if config.HOLD_FILE.exists():
                    continue
                # under the lock, so a create/update that is mid-apply isn't mistaken for drift
                async with self.lock:
                    ks = await engine.kernel_state()
                    want = engine.expected_prerouting_rules(self.store.forwards())
                    drift = not ks.exists or ks.prerouting_rules != want
                    if drift:
                        log.warning("kernel drift (table=%s rules=%s want=%s) - reapplying",
                                    ks.exists, ks.prerouting_rules, want)
                        await self._apply()
                        self._refresh()
                if drift:
                    self.store.audit("system", "kernel.drift_repaired")
                    if self.notifier:
                        self.notifier.notify("kernel_drift", "warning", "The firewall ruleset had drifted from what was configured and was repaired automatically")
            except Exception:
                log.exception("housekeeping failed")


def _diffable(f: Forward) -> dict:
    return f.model_dump(mode="json", exclude={"created_at", "updated_at"})
