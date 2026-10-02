"""Target health probes: TCP connect for TCP forwards, ICMP ping for UDP-only ones."""
import asyncio
import time
from typing import Callable

from . import config, engine
from .models import Forward


def health_flip(prev: dict | None, cur: dict) -> tuple[str, str] | None:
    """(type, severity) if this is a reportable state change, else None. Never fires on the first-ever
    observation (prev is None) - otherwise every forward would notify once on every manager restart,
    which is noise, not signal - and never fires when the state hasn't actually changed."""
    if prev is None or prev["state"] == cur["state"]:
        return None
    return ("target_down", "warning") if cur["state"] == "down" else ("target_up", "info")


class Prober:
    def __init__(self, get_forwards: Callable[[], list[Forward]], notifier=None):
        self.get_forwards = get_forwards
        self.status: dict[int, dict] = {}
        self.notifier = notifier

    async def run(self):
        while True:
            try:
                await self.probe_all()
            except Exception:
                pass
            await asyncio.sleep(config.PROBE_INTERVAL_S)

    async def probe_all(self):
        act = engine.active(self.get_forwards())
        ids = {f.id for f in act}
        for fid in list(self.status):
            if fid not in ids:
                del self.status[fid]
        await asyncio.gather(*(self.probe(f) for f in act))

    async def probe(self, f: Forward) -> dict:
        t0 = time.monotonic()
        if "tcp" in f.protocols:
            method = f"tcp {f.target_ip}:{f.target_port}"
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(str(f.target_ip), f.target_port), 2.0)
                w.close()
                ok, err = True, None
            except Exception as e:
                ok, err = False, type(e).__name__
        else:
            method = f"icmp {f.target_ip}"
            try:
                rc, _, _ = await engine.run("ping", "-c", "1", "-W", "1", str(f.target_ip), timeout=3)
                ok, err = rc == 0, None if rc == 0 else "no reply"
            except Exception as e:
                ok, err = False, type(e).__name__
        st = {"state": "up" if ok else "down", "latency_ms": round((time.monotonic() - t0) * 1000, 1) if ok else None,
              "checked_at": time.time(), "method": method, "error": err}
        prev = self.status.get(f.id)
        self.status[f.id] = st
        flip = health_flip(prev, st)
        if self.notifier and flip:
            type_, severity = flip
            verb = "unreachable" if type_ == "target_down" else "reachable again"
            self.notifier.notify(type_, severity, f"“{f.name}”'s target is {verb}", {"forward_id": f.id})
        return st
