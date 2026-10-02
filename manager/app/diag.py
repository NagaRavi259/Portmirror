"""The "Doctor": one battery of checks covering everything that was previously a manual
log-reading exercise - ruleset validity, route to the remote network, interface state,
conntrack/disk/memory headroom, target health, and (where applicable) service stability.

Each check is `{id, label, status, detail}` with status one of "ok" / "warn" / "fail" / "skip"
("skip" for a check that doesn't apply to this deployment, e.g. systemd restart counts under
Docker). Kept as plain, mostly-pure functions wherever a check doesn't need live kernel/process
state, so the thresholds themselves are unit-testable without a running gateway.
"""
import os
import shutil

from . import config, engine
from .collector import Collector


def _check(id_: str, label: str, status: str, detail: str) -> dict:
    return {"id": id_, "label": label, "status": status, "detail": detail}


def conntrack_status(count: int, max_: int) -> tuple[str, str]:
    if max_ <= 0:
        return "skip", "not reported by this kernel"
    pct = count / max_ * 100
    detail = f"{count:,} / {max_:,} ({pct:.1f}%)"
    if pct >= 90:
        return "fail", detail
    if pct >= 75:
        return "warn", detail
    return "ok", detail


def disk_status(free_bytes: int, total_bytes: int) -> tuple[str, str]:
    if total_bytes <= 0:
        return "skip", "not available"
    pct_free = free_bytes / total_bytes * 100
    detail = f"{pct_free:.1f}% free"
    if pct_free < 5:
        return "fail", detail
    if pct_free < 15:
        return "warn", detail
    return "ok", detail


def memory_status(used_bytes: int, limit_bytes: int) -> tuple[str, str]:
    if limit_bytes <= 0:
        return "skip", "no limit reported"
    pct = used_bytes / limit_bytes * 100
    detail = f"{pct:.1f}% used"
    if pct >= 95:
        return "fail", detail
    if pct >= 85:
        return "warn", detail
    return "ok", detail


def iface_status(state: str) -> tuple[str, str]:
    if state == "up":
        return "ok", "up"
    if state == "missing":
        return "fail", "interface does not exist"
    return "fail", state


def target_health_status(statuses: list[dict | None]) -> tuple[str, str]:
    known = [s for s in statuses if s is not None]
    if not known:
        return "skip", "no enabled forwards to probe"
    down = [s for s in known if s.get("state") != "up"]
    if not down:
        return "ok", f"{len(known)}/{len(known)} targets reachable"
    return "fail", f"{len(down)}/{len(known)} target(s) unreachable"


async def _nft_syntax(forwards) -> dict:
    try:
        script = engine.build_script(forwards, await engine.kernel_state())
        rc, _, err = await engine.run("nft", "-c", "-f", "-", stdin=script)
        status, detail = ("ok", "valid") if rc == 0 else ("fail", engine._clean(err) or "rejected")
    except Exception as e:  # noqa: BLE001
        status, detail = "fail", f"{type(e).__name__}: {e}"
    return _check("nft_syntax", "Firewall ruleset syntax", status, detail)


async def _kernel_table(forwards) -> dict:
    ks = await engine.kernel_state()
    if ks.exists:
        return _check("kernel_table", "Kernel table present", "ok", f"{len(ks.chains)} chain(s) loaded")
    status = "warn" if not engine.active(forwards) else "fail"
    return _check("kernel_table", "Kernel table present", status, "table ip pm does not exist yet")


async def _vpn_route() -> dict:
    target = str(config.VPN_NET.network_address + 1)
    rc, out, err = await engine.run("ip", "route", "get", target)
    if rc == 0 and "unreachable" not in out:
        return _check("vpn_route", "Route to the VPN network", "ok", out.splitlines()[0].strip() if out else "reachable")
    return _check("vpn_route", "Route to the VPN network", "fail", (err or out or "no route").strip().splitlines()[0])


def _iface_check(id_: str, label: str, name: str) -> dict:
    base = f"/sys/class/net/{name}"
    state = Collector._iface_state(base) if os.path.exists(base) else "missing"
    status, detail = iface_status(state)
    return _check(id_, label, status, f"{name}: {detail}")


def _conntrack(snapshot: dict) -> dict:
    g = snapshot.get("global", {})
    status, detail = conntrack_status(g.get("conntrack_count", 0), g.get("conntrack_max", 0))
    return _check("conntrack", "Connection-tracking table usage", status, detail)


def _memory(snapshot: dict) -> dict:
    sysinfo = snapshot.get("global", {}).get("system", {})
    status, detail = memory_status(sysinfo.get("mem_bytes", 0), sysinfo.get("mem_limit", 0))
    return _check("memory", "Memory headroom", status, detail)


def _disk() -> dict:
    try:
        du = shutil.disk_usage(config.STATE_DIR)
        status, detail = disk_status(du.free, du.total)
    except OSError as e:
        status, detail = "skip", str(e)
    return _check("disk", "Disk space", status, detail)


def _target_health(prober) -> dict:
    status, detail = target_health_status(list(prober.status.values()))
    return _check("target_health", "Target health", status, detail)


async def _service_restarts() -> dict:
    if not shutil.which("systemctl"):
        return _check("restarts", "Service stability", "skip", "not applicable (not a systemd deployment)")
    parts = []
    worst = "ok"
    for unit in ("portmirror-base.service", "portmirror-manager.service"):
        rc, out, _ = await engine.run("systemctl", "show", "-p", "NRestarts", "--value", unit)
        n = int(out.strip()) if rc == 0 and out.strip().isdigit() else None
        if n is None:
            parts.append(f"{unit}: unknown")
            worst = "warn" if worst == "ok" else worst
        else:
            parts.append(f"{unit}: {n} restart(s)")
            if n > 0:
                worst = "warn" if worst == "ok" else worst
    return _check("restarts", "Service stability", worst, ", ".join(parts))


async def run(manager, collector, prober) -> list[dict]:
    forwards = manager.forwards()
    checks = [
        await _nft_syntax(forwards),
        await _kernel_table(forwards),
        await _vpn_route(),
        _iface_check("lan_iface", "LAN interface", config.LAN_IF),
        _iface_check("vpn_iface", "VPN interface", config.VPN_IF),
        _conntrack(collector.snapshot),
        _disk(),
        _memory(collector.snapshot),
        _target_health(prober),
        await _service_restarts(),
    ]
    return checks
