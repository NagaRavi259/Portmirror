"""Runtime settings, all overridable via environment variables."""
import ipaddress
import os
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


LAN_IF = _env("PM_LAN_IF", "lan0")
VPN_IF = _env("PM_VPN_IF", "vpn0")
LAN_NET = ipaddress.ip_network(_env("PM_LAN_NET", "192.168.88.0/24"))
VPN_NET = ipaddress.ip_network(_env("PM_VPN_NET", "10.0.0.0/24"))
GW_LAN_IP = ipaddress.ip_address(_env("PM_GW_LAN_IP", "192.168.88.8"))
GW_VPN_IP = ipaddress.ip_address(_env("PM_GW_VPN_IP", "10.0.0.88"))

UI_PORT = int(_env("PM_UI_PORT", "8088"))
RESERVED_TCP_PORTS = {UI_PORT}

STATE_DIR = Path(_env("PM_STATE_DIR", "/state"))
DB_PATH = STATE_DIR / "pm.db"
BOOT_RULES = STATE_DIR / "pm.nft"
ADMIN_PASSWORD_FILE = STATE_DIR / "admin-password.txt"
INTERNAL_TOKEN_FILE = STATE_DIR / "internal.token"
SEED_FILE = Path(_env("PM_SEED_FILE", "/etc/portmirror/seed.json"))
HOLD_FILE = Path(_env("PM_HOLD_FILE", "/run/pm/hold"))   # present => don't self-heal (tests)
UI_DIR = Path(_env("PM_UI_DIR", "/opt/pm/ui"))

TABLE = "pm"
MAX_RANGE = 1024
SESSION_TTL_S = 12 * 3600


def _retention_s(name: str):
    """Days from env -> seconds; unset, empty or 0 means keep forever (None)."""
    raw = os.environ.get(name, "").strip()
    days = float(raw) if raw else 0
    if days < 0:
        raise ValueError(f"{name} must be >= 0 (0 = keep forever)")
    return int(days * 86400) or None


HISTORY_RETENTION_S = _retention_s("PM_HISTORY_RETENTION_DAYS")   # traffic history rollups
AUDIT_RETENTION_S = _retention_s("PM_AUDIT_RETENTION_DAYS")       # audit log entries
RING_SECONDS = 3600
PROBE_INTERVAL_S = 10
