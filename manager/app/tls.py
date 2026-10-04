"""HTTPS for the dashboard.

The operator uploads a certificate and key, then switches the dashboard to HTTPS. The switch is not
trusted until it's confirmed: the manager restarts on the new scheme and waits CONFIRM_SECONDS for a
"Keep this change" from the operator. If none arrives, the watchdog reverts to the previous scheme,
the same way Windows reverts a display change nobody confirmed.

The state lives in STATE_DIR/tls.json. The entrypoint asks this module for the uvicorn TLS flags on
each (re)start, so a restart is the only thing that changes the listener.
"""
import json
import os
import ssl
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

CONFIRM_SECONDS = 60
TLS_DIR = config.STATE_DIR / "tls"
STATE_FILE = config.STATE_DIR / "tls.json"
CERT_FILE = TLS_DIR / "cert.pem"
KEY_FILE = TLS_DIR / "key.pem"
CA_FILE = TLS_DIR / "ca.pem"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_state(path: Path = STATE_FILE) -> dict:
    try:
        s = json.loads(path.read_text())
        return {"enabled": bool(s.get("enabled")), "pending": s.get("pending")}
    except (OSError, ValueError):
        return {"enabled": False, "pending": None}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tls-")
    with os.fdopen(fd, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def begin_switch(state: dict, enable: bool, now: datetime) -> dict:
    """Move to the new scheme, remembering the old one so a missing confirmation can undo it."""
    deadline = now + timedelta(seconds=CONFIRM_SECONDS)
    return {"enabled": enable, "pending": {"previous": state["enabled"], "deadline": deadline.isoformat()}}


def confirm(state: dict) -> dict:
    return {"enabled": state["enabled"], "pending": None}


def revert(state: dict) -> dict:
    p = state.get("pending") or {}
    return {"enabled": bool(p.get("previous", False)), "pending": None}


def overdue(state: dict, now: datetime) -> bool:
    p = state.get("pending")
    return bool(p) and now >= datetime.fromisoformat(p["deadline"])


def seconds_left(state: dict, now: datetime) -> int:
    p = state.get("pending")
    return 0 if not p else max(0, int((datetime.fromisoformat(p["deadline"]) - now).total_seconds()))


def has_certificate() -> bool:
    return CERT_FILE.exists() and KEY_FILE.exists()


def validate_pair(cert_pem: str, key_pem: str) -> None:
    """Raises ValueError unless the certificate and key are a matching, loadable pair."""
    if "BEGIN CERTIFICATE" not in cert_pem or "PRIVATE KEY" not in key_pem:
        raise ValueError("expected a PEM certificate and a PEM private key")
    with tempfile.TemporaryDirectory() as d:
        c, k = Path(d, "c.pem"), Path(d, "k.pem")
        c.write_text(cert_pem)
        k.write_text(key_pem)
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(c, k)
        except ssl.SSLError as e:
            raise ValueError(f"certificate and key do not match or cannot be loaded: {e.reason or e}") from e


def store_certificate(cert_pem: str, key_pem: str, ca_pem: str | None = None) -> None:
    validate_pair(cert_pem, key_pem)
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    for path, text, mode in ((CERT_FILE, cert_pem, 0o644), (KEY_FILE, key_pem, 0o600)):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    if ca_pem:
        if "BEGIN CERTIFICATE" not in ca_pem:
            raise ValueError("the CA file must be a PEM certificate")
        CA_FILE.write_text(ca_pem)


def uvicorn_flags(state: dict) -> list[str]:
    if state.get("enabled") and has_certificate():
        return ["--ssl-certfile", str(CERT_FILE), "--ssl-keyfile", str(KEY_FILE)]
    return []


if __name__ == "__main__":
    # called by the entrypoint before each manager start: prints the uvicorn TLS flags, one per line
    if len(sys.argv) > 1 and sys.argv[1] == "flags":
        for flag in uvicorn_flags(load_state()):
            print(flag)
