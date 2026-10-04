"""Whether the dashboard can be reached over the VPN side (Tailscale on a Pi), switchable in Settings.

Allowed (the default): no rule, so a device on the VPN can open the dashboard, and login still applies.
Blocked: a drop rule for the dashboard port on the VPN interface, in its own table `inet pm_ui`. LAN
access and the forwards are unaffected either way. The manager owns this table, so the base ruleset
no longer carries a hard-coded rule for it.
"""
import json
import tempfile
from pathlib import Path

from . import config

TABLE = "pm_ui"
STATE_FILE = config.STATE_DIR / "access.json"


def load(path: Path = STATE_FILE) -> dict:
    try:
        return {"ui_over_vpn": bool(json.loads(path.read_text()).get("ui_over_vpn", True))}
    except (OSError, ValueError):
        return {"ui_over_vpn": True}


def save(state: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".access-")
    with open(fd, "w") as f:
        json.dump(state, f)
    Path(tmp).replace(path)


def rules_script(ui_over_vpn: bool, vpn_iface: str, ui_port: int) -> str:
    """nft input for the access setting. Always replaces our table, so applying it twice is harmless."""
    # `add` first so the delete never fails on a missing table (nft errors on deleting one that isn't there)
    script = f"add table inet {TABLE}\ndelete table inet {TABLE}\n"
    if not ui_over_vpn:
        script += (f"table inet {TABLE} {{\n"
                   f"    chain input {{\n"
                   f"        type filter hook input priority filter; policy accept;\n"
                   f"        iifname \"{vpn_iface}\" tcp dport {ui_port} counter drop\n"
                   f"    }}\n"
                   f"}}\n")
    return script
