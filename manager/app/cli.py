"""pmctl - talk to the local manager with the internal token.

  pmctl status | forwards | reapply | token <name> | password | diag
  pmctl api <METHOD> <path> [json-body]
  pmctl update [check | --yes]   check GitHub for a newer release, or install one
"""
import json
import sys
import urllib.error
import urllib.request

from . import config


def call(method: str, path: str, body=None):
    token = config.INTERNAL_TOKEN_FILE.read_text().strip()
    req = urllib.request.Request(f"http://127.0.0.1:{config.UI_PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    try:
        return _dispatch(argv)
    except PermissionError as e:
        print(f"pmctl: permission denied reading {e.filename} - run this as root (e.g. with sudo)", file=sys.stderr)
        return 1


def _dispatch(argv: list[str]) -> int:
    cmd = argv[0]
    if cmd == "status":
        code, body = call("GET", "/api/stats")
        g = body.get("global", {}) if isinstance(body, dict) else {}
        print(json.dumps({k: g.get(k) for k in ("forwards_active", "forwards_total", "live", "conntrack_count",
                                                  "kernel_table", "uptime_s")}, indent=2))
    elif cmd == "forwards":
        code, body = call("GET", "/api/forwards")
        for f in body:
            ports = f"{f['listen_port']}" + (f"-{f['listen_port_end']}" if f["listen_port_end"] else "")
            print(f"#{f['id']:<4} {'on ' if f['enabled'] else 'off'} {f['protocol']:<4} {ports:<12} -> "
                  f"{f['target_ip']}:{f['target_port']:<6} {f['name']}")
    elif cmd == "reapply":
        code, body = call("POST", "/api/admin/reapply")
        print(body)
    elif cmd == "token" and len(argv) == 2:
        code, body = call("POST", "/api/tokens", {"name": argv[1]})
        print(body["token"] if code == 201 else body)
    elif cmd == "password":
        print(config.ADMIN_PASSWORD_FILE.read_text().strip() if config.ADMIN_PASSWORD_FILE.exists()
              else "initial password already changed")
        return 0
    elif cmd == "diag":
        code, body = call("GET", "/api/diag")
        for c in body.get("checks", []):
            print(f"[{c['status'].upper():<4}] {c['label']:<32} {c['detail']}")
        return 0 if body.get("ok") else 1
    elif cmd == "api" and len(argv) >= 3:
        code, body = call(argv[1].upper(), argv[2], json.loads(argv[3]) if len(argv) > 3 else None)
        print(json.dumps(body, indent=2))
    else:
        print(__doc__)
        return 1
    return 0 if code < 400 else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
