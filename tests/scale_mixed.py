"""
scale_mixed.py - sustained mixed-protocol load through the gateway (SCALE-02).

N worker threads each loop over HTTP / HTTPS / Postgres / UDP / WebSocket
requests for DURATION seconds. Prints one JSON summary line.

Usage: python3 scale_mixed.py <gateway> <workers> <duration_s>
       python3 scale_mixed.py direct <workers> <duration_s>
  "direct" targets the backend IPs themselves (run from a container on
  vpn_net) - the control run that tells gateway loss from backend limits.
"""
import json
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request

import websocket

GW = sys.argv[1] if len(sys.argv) > 1 else "192.168.88.8"
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 100
DURATION = float(sys.argv[3]) if len(sys.argv) > 3 else 60

if GW == "direct":
    H = {"http": "10.0.0.12", "https": "10.0.0.13", "pg": "10.0.0.15", "udp": "10.0.0.15", "ws": "10.0.0.17"}
else:
    H = dict.fromkeys(["http", "https", "pg", "udp", "ws"], GW)

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def http():
    return urllib.request.urlopen(f"http://{H['http']}:80/", timeout=5).status == 200


def https():
    return urllib.request.urlopen(f"https://{H['https']}:443/", timeout=5, context=CTX).status == 200


def pg():
    out = subprocess.run(
        ["psql", "-h", H["pg"], "-U", "postgres", "-d", "world", "-tAc", "SELECT count(*) FROM city"],
        env={"PGPASSWORD": "testpass123", "PGCONNECT_TIMEOUT": "5"},
        capture_output=True, text=True, timeout=10)
    return out.stdout.strip() == "4079"


def udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(b"scale", (H["udp"], 9000))
        return s.recvfrom(4096)[0] == b"echo:scale"
    finally:
        s.close()


def ws():
    c = websocket.create_connection(f"ws://{H['ws']}:8765/", timeout=5)
    try:
        return "log_id" in json.loads(c.recv())
    finally:
        c.close()


CHECKS = [("http", http), ("https", https), ("pg", pg), ("udp", udp), ("ws", ws)]
stats = {name: {"ok": 0, "fail": 0} for name, _ in CHECKS}
errors = {}
lock = threading.Lock()
deadline = time.time() + DURATION


def worker(offset):
    i = offset
    while time.time() < deadline:
        name, fn = CHECKS[i % len(CHECKS)]
        i += 1
        err = None
        try:
            ok = fn()
        except Exception as e:
            ok, err = False, f"{name}:{type(e).__name__}:{str(e)[:60]}"
        with lock:
            stats[name]["ok" if ok else "fail"] += 1
            if err:
                errors[err] = errors.get(err, 0) + 1


threads = [threading.Thread(target=worker, args=(n,)) for n in range(WORKERS)]
for t in threads:
    t.start()
for t in threads:
    t.join()

ok = sum(v["ok"] for v in stats.values())
fail = sum(v["fail"] for v in stats.values())
print(json.dumps({"workers": WORKERS, "duration": DURATION, "ok": ok, "fail": fail, "per_service": stats, "errors": errors}))
