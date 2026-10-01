#!/usr/bin/env python3
"""
Integration tests for the portmirror manager - real traffic through pm-E.
Runs on the Docker host. Talks to the API on the published port and drives
traffic from the client containers with `docker exec`.

Usage: python3 tests/manager_tests.py [results_dir]
"""
import csv
import http.client
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API = os.environ.get("PM_API", "http://127.0.0.1:8088")
GW = "192.168.88.8"
T = "10.0.0.20"          # multi-echo test target, tcp+udp 7000-7010
OUTDIR = sys.argv[1] if len(sys.argv) > 1 else "./results"
os.makedirs(OUTDIR, exist_ok=True)
RESULTS = os.path.join(OUTDIR, "results_manager.csv")
rows = []


def sh(*cmd, timeout=60, input=None) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input)
    return p.stdout.strip()


TOKEN = sh("docker", "exec", "pm-E", "pmctl", "token", "manager-tests").splitlines()[-1]


def api(method, path, body=None, token=TOKEN, timeout=30):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, http.client.HTTPException):
        return 0, None          # manager not answering (e.g. restarting)


def log(tid, cat, desc, ok, detail=""):
    r = "PASS" if ok else "FAIL"
    rows.append([tid, cat, desc, r, str(detail)[:600]])
    print(f"[{tid:<22}] {cat:<10} {desc:<70} {r}", flush=True)


def wait_until(fn, timeout, interval=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    return fn()


# ---- traffic helpers (run inside the client containers) ------------------------
def curl(client, port, mt=2):
    return sh("docker", "exec", f"pm-{client}", "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
              "--connect-timeout", str(mt), "--max-time", str(mt + 1), f"http://{GW}:{port}/")


def tcp_echo(client, port, payload="x", t=2):
    return sh("docker", "exec", f"pm-{client}", "python3", "-c", f"""
import socket
try:
    s = socket.create_connection(('{GW}', {port}), timeout={t}); s.settimeout({t})
    s.sendall(b'{payload}'); print(s.recv(4096).decode())
except Exception as e:
    print('ERR', type(e).__name__)""")


def udp_echo(client, port, payload="x", t=2):
    return sh("docker", "exec", f"pm-{client}", "python3", "-c", f"""
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout({t})
s.sendto(b'{payload}', ('{GW}', {port}))
try:
    print(s.recv(4096).decode())
except Exception as e:
    print('ERR', type(e).__name__)""")


class Probe:
    """Holds N TCP connections open inside a client (tests/conn_probe.py)."""

    def __init__(self, client, port, n):
        self.p = subprocess.Popen(["docker", "exec", "-i", f"pm-{client}", "python3", "/opt/tests/conn_probe.py",
                                   GW, str(port), str(n)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.opened = int(self.p.stdout.readline().split()[1])

    def cmd(self, c):
        self.p.stdin.write(c + "\n")
        self.p.stdin.flush()
        return self.p.stdout.readline().strip()

    def alive(self):
        return int(self.cmd("ping").split()[1])

    def ports(self):
        return [int(x) for x in self.cmd("ports").split()[1].split(",") if x]

    def quit(self):
        try:
            self.cmd("quit")
        except Exception:
            pass
        self.p.wait(timeout=10)


# ---- forward helpers ----------------------------------------------------------------
def fwd(name, listen, target_ip, target_port, **kw):
    body = {"name": f"mt-{name}", "protocol": "tcp", "listen_port": listen, "target_ip": target_ip,
            "target_port": target_port}
    body.update(kw)
    return body


def create(body):
    code, res = api("POST", "/api/forwards", body)
    if code != 201:
        raise RuntimeError(f"create failed {code}: {res}")
    return res


def stats(fid):
    _, s = api("GET", "/api/stats")
    return s["forwards"].get(str(fid)) or {}


def by_name(name):
    _, fs = api("GET", "/api/forwards")
    return next((f for f in fs if f["name"] == name), None)


def prerouting_rules():
    out = sh("docker", "exec", "pm-E", "nft", "-j", "list", "chain", "ip", "pm", "prerouting")
    try:
        return sum(1 for i in json.loads(out)["nftables"] if "rule" in i)
    except ValueError:
        return -1


def audit_has(action, since_id=0):
    _, a = api("GET", "/api/audit?limit=300")
    return [x for x in a if x["action"] == action and x["id"] > since_id]


def last_audit_id():
    _, a = api("GET", "/api/audit?limit=1")
    return a[0]["id"] if a else 0


def cleanup():
    _, fs = api("GET", "/api/forwards")
    for f in fs or []:
        if f["name"].startswith("mt-"):
            api("DELETE", f"/api/forwards/{f['id']}?kill=true")


# ==============================================================================
def main():
    cleanup()
    print("=" * 78 + "\n Manager integration tests  ->  " + API + "\n" + "=" * 78)
    a0 = last_audit_id()

    # ---- rule changes -------------------------------------------------------------
    t0 = time.time()
    web = create(fwd("web", 18080, "10.0.0.12", 80))
    t_api = time.time() - t0
    first_ok = wait_until(lambda: curl("F", 18080) == "200", 5, 0.05)
    t_live = time.time() - t0
    log("M-ADD", "RULES", "New forward 18080->A:80 serves traffic within 1s of the API call",
        bool(first_ok) and t_live < 1.5, f"api={t_api*1000:.0f}ms first_200_after={t_live*1000:.0f}ms")

    ed = create(fwd("edit", 17100, T, 7000))
    r1 = tcp_echo("F", 17100)
    b = fwd("edit", 17100, T, 7001)
    code, _ = api("PUT", f"/api/forwards/{ed['id']}", b)
    r2 = tcp_echo("F", 17100)
    log("M-EDIT-TARGET", "RULES", "Editing target port 7000->7001 re-routes new connections",
        r1.startswith("port:7000:") and r2.startswith("port:7001:") and code == 200, f"before={r1!r} after={r2!r}")

    api("POST", f"/api/forwards/{ed['id']}/toggle", {"enabled": False})
    off = tcp_echo("F", 17100)
    api("POST", f"/api/forwards/{ed['id']}/toggle", {"enabled": True})
    on = tcp_echo("F", 17100)
    log("M-TOGGLE", "RULES", "Disable stops new connections, enable restores them",
        off.startswith("ERR") and on.startswith("port:7001:"), f"off={off!r} on={on!r}")

    both = create(fwd("both", 17200, T, 7002, protocol="both"))
    rt, ru = tcp_echo("F", 17200, "t"), udp_echo("F", 17200, "u")
    log("M-BOTH", "RULES", "TCP+UDP forward: both protocols reach target port 7002",
        rt == "port:7002:t" and ru == "port:7002:u", f"tcp={rt!r} udp={ru!r}")

    rng = create(fwd("range", 17300, T, 7003, protocol="both", listen_port_end=17305))
    a, bb, c = tcp_echo("F", 17300), tcp_echo("F", 17304), udp_echo("F", 17305)
    log("M-RANGE", "RULES", "Range 17300-17305 -> 7003-7008 maps 1:1 (tcp + udp)",
        a.startswith("port:7003:") and bb.startswith("port:7007:") and c.startswith("port:7008:"),
        f"17300={a!r} 17304={bb!r} udp17305={c!r}")

    src = create(fwd("src", 17400, T, 7000, allowed_sources=["192.168.88.23/32"]))
    g, f = tcp_echo("G", 17400), tcp_echo("F", 17400)
    log("M-SOURCE-CIDR", "RULES", "allowed_sources=G only: G connects, F is refused",
        g.startswith("port:7000:") and f.startswith("ERR"), f"G={g!r} F={f!r}")

    rl = create(fwd("rate", 17500, "10.0.0.12", 80, rate_limit=5))
    out = sh("docker", "exec", "pm-F", "bash", "-c",
             f"for i in $(seq 1 40); do curl -s -o /dev/null -w '%{{http_code}}\\n' --connect-timeout 1 --max-time 2 "
             f"http://{GW}:17500/ & done; wait")
    ok = out.split().count("200")
    time.sleep(1.5)
    rlc = stats(rl["id"]).get("rate_limited_total", 0)
    log("M-RATE-LIMIT", "RULES", "rate_limit=5/s: a burst of 40 is throttled, drops counted",
        0 < ok < 40 and rlc > 0, f"ok={ok}/40 rate_limited_total={rlc}")

    mc = create(fwd("maxconn", 17600, T, 7000, max_conns=3))
    p = Probe("F", 17600, 5)
    opened = p.opened
    p.quit()
    time.sleep(1)
    after = tcp_echo("F", 17600)
    log("M-MAX-CONNS", "RULES", "max_conns=3: only 3 of 5 held connections open; new one OK after close",
        opened == 3 and after.startswith("port:7000:"), f"opened={opened}/5 after_close={after!r}")

    exp_at = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    ex = create(fwd("expire", 17700, T, 7000, expires_at=exp_at))
    before = tcp_echo("F", 17700)
    disabled = wait_until(lambda: not (by_name("mt-expire") or {}).get("enabled", True), 20, 1)
    later = tcp_echo("F", 17700)
    log("M-EXPIRE", "RULES", "Forward with expires_at=+5s works, then auto-disables",
        before.startswith("port:7000:") and bool(disabled) and later.startswith("ERR")
        and bool(audit_has("forward.expire", a0)), f"before={before!r} disabled={bool(disabled)} after={later!r}")

    # ---- delete: drain vs kill ------------------------------------------------------
    d = create(fwd("drain", 17800, T, 7000))
    p = Probe("F", 17800, 2)
    api("DELETE", f"/api/forwards/{d['id']}?kill=false")
    alive, new = p.alive(), tcp_echo("F", 17800)
    p.quit()
    log("M-DELETE-DRAIN", "DELETE", "Delete (drain): open connections keep working, new ones fail",
        alive == 2 and new.startswith("ERR"), f"alive={alive}/2 new={new!r}")

    k = create(fwd("kill", 17801, T, 7000))
    p = Probe("F", 17801, 2)
    code, res = api("DELETE", f"/api/forwards/{k['id']}?kill=true")
    alive = p.alive()
    p.quit()
    log("M-DELETE-KILL", "DELETE", "Delete (kill): open connections are cut immediately",
        alive == 0 and res.get("killed") == 2, f"alive={alive}/2 killed={res.get('killed')}")

    k1 = create(fwd("killone", 17802, T, 7000))
    p = Probe("F", 17802, 2)
    ports = p.ports()
    code, _ = api("POST", "/api/connections/kill", {"protocol": "tcp", "src": "192.168.88.67", "sport": ports[0],
                                                     "dport": 17802})
    alive = p.alive()
    p.quit()
    log("M-KILL-ONE", "DELETE", "Killing one connection from the live list leaves the other alive",
        code == 200 and alive == 1, f"code={code} alive={alive}/2")

    # ---- cut scales: hundreds of recent flows must not make delete-with-cut slow ----------
    ks = create(fwd("killscale", 17810, T, 7000))
    churn = ("import socket\n"
             "for _ in range(300):\n"
             f"    s = socket.create_connection(('{GW}', 17810), timeout=3); s.sendall(b'x'); s.recv(64); s.close()\n")
    sh("docker", "exec", "pm-F", "python3", "-c", churn, timeout=120)
    p = Probe("F", 17810, 20)
    t0 = time.time()
    code, res = api("DELETE", f"/api/forwards/{ks['id']}?kill=true")
    took = time.time() - t0
    alive = p.alive()
    p.quit()
    log("M-KILL-SCALE", "DELETE", "Delete+cut with 300 recent + 20 open flows: fast, all 20 open ones cut",
        code == 200 and took < 3 and alive == 0 and res.get("killed") == 20,
        f"took={took:.2f}s killed={res.get('killed')} alive_after={alive}/20")

    # ---- isolation: editing one forward never touches the others' live connections --------
    five = [create(fwd(f"iso{i}", 17901 + i, T, 7000 + i)) for i in range(5)]
    probes = [Probe("F", 17901 + i, 2) for i in range(5)]
    base = [p.alive() for p in probes]
    steps = []

    def snapshot(label, skip=()):
        steps.append((label, [p.alive() if i not in skip else None for i, p in enumerate(probes)]))

    b = fwd("iso2", 17903, T, 7009)                              # 1. change forward #3's target
    api("PUT", f"/api/forwards/{five[2]['id']}", b)
    snapshot("edit #3 target")
    b = fwd("iso1-renamed", 17902, T, 7001, rate_limit=500, max_conns=100)   # 2. rename + limits on #2
    api("PUT", f"/api/forwards/{five[1]['id']}", b)
    snapshot("edit #2 name+limits")
    api("POST", f"/api/forwards/{five[3]['id']}/toggle", {"enabled": False, "kill": False})   # 3. disable #4 (drain)
    snapshot("disable #4 drain")
    api("DELETE", f"/api/forwards/{five[4]['id']}?kill=true")    # 4. delete #5 with cut
    snapshot("delete #5 cut", skip=(4,))
    cut5 = probes[4].alive()
    new3 = tcp_echo("F", 17903)
    for p in probes:
        p.quit()
    others_ok = all(a == 2 for _, row in steps for a in row if a is not None)
    detail = f"baseline={base} " + " | ".join(f"{l}: {row}" for l, row in steps) + f" | #5 after cut={cut5} | new conn on edited #3 -> {new3!r}"
    log("M-EDIT-ISOLATION", "ISOLATION",
        "5 forwards x 2 held conns: edit/rename/disable/delete one - all other held conns survive",
        base == [2] * 5 and others_ok and cut5 == 0 and new3.startswith("port:7009:"), detail)

    # ---- retention: history older than the old 7-day window is kept and viewable ----------
    old_ts = (int(time.time()) - 40 * 86400) // 60 * 60
    sh("docker", "exec", "pm-E", "python3", "-c",
       f"import sqlite3; c=sqlite3.connect('/state/pm.db'); c.execute('INSERT OR REPLACE INTO rollups VALUES ({old_ts},0,42,1000,2000,10,20,3)'); c.commit()")
    _, sysinfo = api("GET", "/api/system")
    _, h_all = api("GET", "/api/history?range=all")
    _, h_1y = api("GET", "/api/history?range=1y")
    _, h_7d = api("GET", "/api/history?range=7d")
    day = old_ts // 86400 * 86400
    in_all = any(p["t"] == day for p in h_all["points"])
    in_1y = any(p["t"] == day for p in h_1y["points"])
    in_7d = any(p["t"] <= old_ts for p in h_7d["points"])
    sh("docker", "exec", "pm-E", "python3", "-c",
       f"import sqlite3; c=sqlite3.connect('/state/pm.db'); c.execute('DELETE FROM rollups WHERE ts={old_ts} AND fid=0'); c.commit()")
    log("M-RETENTION", "HISTORY", "Default retention is forever; 40-day-old history shows in 'all'/'1y', not in '7d'",
        sysinfo["history_retention_days"] is None and sysinfo["audit_retention_days"] is None and in_all and in_1y and not in_7d,
        f"history_retention={sysinfo['history_retention_days']} audit_retention={sysinfo['audit_retention_days']} "
        f"in_all={in_all} in_1y={in_1y} in_7d={in_7d} storage={sysinfo['storage']}")

    # ---- rejected input -----------------------------------------------------------------
    n0 = prerouting_rules()
    c1, r1 = api("POST", "/api/forwards", fwd("dup", 18080, "10.0.0.12", 80))
    c2, _ = api("POST", "/api/forwards", fwd("reserved", 8088, "10.0.0.12", 80))
    c3, _ = api("POST", "/api/forwards", fwd("badtarget", 18200, "192.168.88.50", 80))
    c4, _ = api("POST", "/api/forwards", fwd("badrange", 18300, "10.0.0.12", 80, listen_port_end=18200))
    n1 = prerouting_rules()
    log("M-REJECT", "VALIDATION", "Conflict 409; reserved port, bad target, bad range 422; kernel untouched",
        (c1, c2, c3, c4) == (409, 422, 422, 422) and n0 == n1, f"codes={c1},{c2},{c3},{c4} rules={n0}->{n1}")

    probe_f = create(fwd("idprobe", 18400, "10.0.0.12", 80))
    api("DELETE", f"/api/forwards/{probe_f['id']}")
    nxt = probe_f["id"] + 1
    sh("docker", "exec", "pm-E", "nft", "add", "set", "ip", "pm", f"f{nxt}_src", "{ type ether_addr; }")
    n0 = prerouting_rules()
    code, res = api("POST", "/api/forwards", fwd("atomic", 18500, "10.0.0.12", 80))
    n1 = prerouting_rules()
    absent = by_name("mt-atomic") is None
    still = curl("F", 18080)
    sh("docker", "exec", "pm-E", "nft", "delete", "set", "ip", "pm", f"f{nxt}_src")
    log("M-ATOMIC-ROLLBACK", "VALIDATION", "nft rejects an apply: API 500, DB rolled back, old rules intact",
        code == 500 and absent and n0 == n1 and still == "200",
        f"code={code} db_rolled_back={absent} rules={n0}->{n1} existing_fwd={still} err={(res or {}).get('detail','')[:120]}")

    # ---- counters -------------------------------------------------------------------------
    time.sleep(1.5)
    s0 = stats(web["id"])["new_total"]
    for _ in range(25):
        curl("F", 18080)
    time.sleep(2.5)
    s1 = stats(web["id"])["new_total"]
    log("M-COUNT-NEW", "METRICS", "25 requests -> new-connection counter +25 exactly", s1 - s0 == 25, f"{s0}->{s1}")

    st0 = stats(ed["id"])
    sh("docker", "exec", "pm-F", "python3", "-c", f"""
import socket
s = socket.create_connection(('{GW}', 17100), timeout=5); s.settimeout(5)
s.sendall(b'X' * 200000); s.shutdown(socket.SHUT_WR)
got = 0
while True:
    d = s.recv(65536)
    if not d: break
    got += len(d)
print(got)""", timeout=30)
    time.sleep(2.5)
    st1 = stats(ed["id"])
    din, dout = st1["bytes_in_total"] - st0["bytes_in_total"], st1["bytes_out_total"] - st0["bytes_out_total"]
    log("M-COUNT-BYTES", "METRICS", "200 KB echoed -> bytes in/out each within +0..10% of payload",
        200000 <= din <= 220000 and 200000 <= dout <= 230000, f"bytes_in=+{din} bytes_out=+{dout}")

    p = Probe("F", 17100, 5)
    live5 = wait_until(lambda: stats(ed["id"]).get("live") == 5, 5)
    p.quit()
    live0 = wait_until(lambda: stats(ed["id"]).get("live") == 0, 5)
    log("M-COUNT-LIVE", "METRICS", "5 held connections -> live=5; closed -> live=0",
        bool(live5) and bool(live0), f"live5={bool(live5)} live0={bool(live0)}")

    u0 = stats(both["id"])["new_total"]
    for i in range(10):
        udp_echo("F", 17200, f"u{i}")
    time.sleep(2.5)
    u1 = stats(both["id"])["new_total"]
    log("M-COUNT-UDP", "METRICS", "10 UDP datagrams from 10 sockets -> +10 flows", u1 - u0 == 10, f"{u0}->{u1}")

    code, h = api("GET", f"/api/forwards/{web['id']}/history?range=1h")
    code2, h2 = api("GET", "/api/history?range=24h")
    log("M-HISTORY", "METRICS", "1h history has live points; 24h rollups endpoint answers",
        code == 200 and len(h["points"]) > 0 and code2 == 200 and isinstance(h2["points"], list),
        f"1h_points={len(h['points'])} 24h_points={len(h2['points'])}")

    ws = sh("docker", "exec", "pm-F", "python3", "-c", f"""
import websocket, json
ws = websocket.create_connection('ws://{GW}:8088/api/ws?token={TOKEN}', timeout=5)
a = json.loads(ws.recv()); b = json.loads(ws.recv())
print(a['type'], len(a['forwards']), round(b['ts'] - a['ts'], 1))""")
    parts = ws.split()
    log("M-WS-STREAM", "METRICS", "WebSocket pushes a snapshot of every forward about once per second",
        len(parts) == 3 and parts[0] == "snapshot" and 0.5 <= float(parts[2]) <= 2.0, ws)

    # ---- health ---------------------------------------------------------------------------
    a_fwd = by_name("A - HTTP")
    sh("docker", "stop", "pm-A")
    down = wait_until(lambda: ((by_name("A - HTTP") or {}).get("health") or {}).get("state") == "down", 25, 1)
    sh("docker", "start", "pm-A")
    up = wait_until(lambda: ((by_name("A - HTTP") or {}).get("health") or {}).get("state") == "up", 25, 1)
    log("M-HEALTH", "HEALTH", "Stopping backend A marks its forward down; starting it marks it up",
        bool(down) and bool(up), f"down={bool(down)} up={bool(up)} fwd=#{a_fwd['id']}")

    # ---- resilience -----------------------------------------------------------------------
    results = []
    stop = threading.Event()

    def hammer():
        while not stop.is_set():
            results.append(curl("F", 18080))

    th = threading.Thread(target=hammer, daemon=True)
    th.start()
    try:
        time.sleep(0.5)
        sh("docker", "exec", "pm-E", "pkill", "-9", "-f", "uvicorn")
        back = wait_until(lambda: api("GET", "/api/health", token=None)[0] == 200, 20, 0.5)
        time.sleep(1)
    finally:
        stop.set()
        th.join(timeout=10)
    log("M-MANAGER-CRASH", "RESILIENCE", "Manager killed -9: forwarding never stops; supervisor restarts it",
        bool(back) and results and all(r == "200" for r in results),
        f"requests_during={len(results)} non200={sum(r != '200' for r in results)} back={bool(back)}")

    sh("docker", "restart", "pm-E", timeout=120)
    back = wait_until(lambda: api("GET", "/api/health", token=None)[0] == 200, 40, 1)
    w, r = curl("F", 18080), tcp_echo("F", 17304)
    log("M-GATEWAY-RESTART", "RESILIENCE", "pm-E restarted: dynamic forwards restored from /state",
        bool(back) and w == "200" and r.startswith("port:7007:"), f"web={w} range={r!r}")

    a1 = last_audit_id()
    sh("docker", "exec", "pm-E", "nft", "delete", "table", "ip", "pm")
    gone = curl("F", 18080, mt=1)
    healed = wait_until(lambda: curl("F", 18080, mt=1) == "200", 15, 1)
    log("M-DRIFT-REPAIR", "RESILIENCE", "Table deleted behind the manager's back: repaired within 10s",
        gone != "200" and bool(healed) and bool(audit_has("kernel.drift_repaired", a1)),
        f"right_after_delete={gone} healed={bool(healed)}")

    load = {}

    def run_load():
        load["out"] = sh("docker", "exec", "pm-F", "python3", "/opt/tests/scale_mixed.py", GW, "30", "60", timeout=120)

    lt = threading.Thread(target=run_load, daemon=True)
    lt.start()
    ops_ok = ops = 0
    t_end = time.time() + 55
    i = 0
    while time.time() < t_end and ops < 200:
        code, res = api("POST", "/api/forwards", fwd(f"churn{i}", 19000 + (i % 50), T, 7000 + (i % 10)))
        ops += 1
        if code == 201:
            ops_ok += 1
            c, _ = api("DELETE", f"/api/forwards/{res['id']}")
            ops += 1
            ops_ok += c == 200
        i += 1
    lt.join()
    try:
        L = json.loads(load["out"].splitlines()[-1])
    except (KeyError, ValueError, IndexError):
        L = {"ok": 0, "fail": -1}
    log("M-ZERO-DISRUPTION", "RESILIENCE", "60s mixed load on existing forwards while forwards churn: 0 failures",
        L["fail"] == 0 and L["ok"] > 0 and ops_ok == ops and ops >= 100,
        f"load_ok={L['ok']} load_fail={L['fail']} api_ops={ops_ok}/{ops} errors={L.get('errors')}")

    # ---- ops: auth, audit, export/import -------------------------------------------------
    c1, _ = api("GET", "/api/forwards", token=None)
    c2, _ = api("GET", "/api/forwards", token="pm_wrong")
    wsno = sh("docker", "exec", "pm-F", "python3", "-c", f"""
import websocket
try:
    ws = websocket.create_connection('ws://{GW}:8088/api/ws', timeout=5); ws.recv(); print('OPEN')
except websocket.WebSocketBadStatusException as e:
    print('REJECTED', e.status_code)
except Exception as e:
    print('CLOSED', getattr(e, 'args', [''])[0] if e.args else type(e).__name__)""")
    log("M-AUTH", "OPS", "No/invalid credentials: REST 401, WebSocket refused",
        c1 == 401 and c2 == 401 and not wsno.startswith("OPEN"), f"no_token={c1} bad_token={c2} ws={wsno!r}")

    creates = audit_has("forward.create", a0)
    actors = {x["actor"] for x in creates}
    log("M-AUDIT", "OPS", "Every change is in the audit log with the acting token",
        len(creates) >= 10 and actors == {"token:manager-tests"}, f"creates={len(creates)} actors={actors}")

    code, exp = api("GET", "/api/export")
    before = sorted((f["name"], f["protocol"], f["listen_port"]) for f in exp["forwards"])
    c_imp, _ = api("POST", "/api/import", {"forwards": exp["forwards"], "mode": "replace"})
    _, exp2 = api("GET", "/api/export")
    after = sorted((f["name"], f["protocol"], f["listen_port"]) for f in exp2["forwards"])
    c_conf, _ = api("POST", "/api/import", {"forwards": [exp["forwards"][0]], "mode": "merge"})
    w = curl("F", 18080)
    log("M-EXPORT-IMPORT", "OPS", "Export -> replace-import round-trips; conflicting merge is refused (409)",
        c_imp == 200 and before == after and c_conf == 409 and w == "200",
        f"forwards={len(before)} identical={before == after} merge_conflict={c_conf} web_after={w}")

    thr = sh("docker", "exec", "pm-G", "bash", "-c",
             f"for i in 1 2 3 4 5 6; do curl -s -o /dev/null -w '%{{http_code}} ' -H 'Content-Type: application/json' "
             f"-d '{{\"username\":\"admin\",\"password\":\"wrong\"}}' http://{GW}:8088/api/auth/login; done")
    log("M-LOGIN-THROTTLE", "OPS", "5 bad passwords -> 401s, the 6th attempt -> 429", thr.split() == ["401"] * 5 + ["429"],
        thr)

    # ---- cleanup ----------------------------------------------------------------------------
    cleanup()
    _, fs = api("GET", "/api/forwards")
    names = sorted(f["name"] for f in fs)
    log("M-CLEANUP", "OPS", "Test forwards removed; the 7 seeded forwards remain",
        len(fs) == 7 and not any(n.startswith("mt-") for n in names), f"forwards={len(fs)}")

    with open(RESULTS, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["test_id", "category", "description", "result", "detail"])
        w.writerows(rows)
    passed = sum(r[3] == "PASS" for r in rows)
    print("=" * 78 + f"\n Manager: Passed: {passed}   Failed: {len(rows) - passed}   Total: {len(rows)}\n"
          f" Results: {RESULTS}\n" + "=" * 78)
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        cleanup()
