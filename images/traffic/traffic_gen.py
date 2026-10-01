"""
traffic_gen.py - continuous, realistic mixed traffic through the gateway (demo / load).

Runs on the LAN side and uses the normal forwards on 192.168.88.8, spread over
several simulated client IPs. Load follows a slow wave with occasional bursts,
so the dashboard charts have something to show.

All rates are per second at level 1.0; TRAFFIC_LEVEL scales everything.
  TRAFFIC_LEVEL     1.0   global multiplier (0 = idle)
  BULK_MBPS         20    HTTP download throughput from A (/blob.bin), split over 2 streams
  HTTP_RPS          25    small HTTP GETs to A
  HTTPS_RPS         6     HTTPS GETs to B
  UDP_STREAM_MBPS   4     UDP "media streams" to the echo service, each way (persistent sockets)
  UDP_STREAMS       4     number of concurrent UDP streams
  UDP_PPS           10    one-shot UDP request/reply flows (a fresh socket each)
  UDP_BYTES         1000
  PG_QPS            3     Postgres queries (world dataset)
  WS_HOLD           12    long-lived WebSocket log-stream sessions
  VNC_HOLD          3     long-lived VNC sessions
  RDP_HOLD          2     long-lived RDP sessions (X.224 handshake, then held)
  CLIENTS           8     simulated client IPs 192.168.88.101.. (needs NET_ADMIN)
"""
import http.client
import math
import os
import random
import socket
import ssl
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg2
from websockets.sync.client import connect as ws_connect

GW = os.environ.get("GW", "192.168.88.8")
E = lambda k, d: float(os.environ.get(k, d))  # noqa: E731
LEVEL = E("TRAFFIC_LEVEL", 1.0)
BULK_MBPS, HTTP_RPS, HTTPS_RPS = E("BULK_MBPS", 20), E("HTTP_RPS", 25), E("HTTPS_RPS", 6)
UDP_PPS, UDP_BYTES, PG_QPS = E("UDP_PPS", 10), int(E("UDP_BYTES", 1000)), E("PG_QPS", 3)
UDP_STREAM_MBPS, UDP_STREAMS, RDP_HOLD = E("UDP_STREAM_MBPS", 4), int(E("UDP_STREAMS", 4)), int(E("RDP_HOLD", 2))
WS_HOLD, VNC_HOLD, CLIENTS = int(E("WS_HOLD", 12)), int(E("VNC_HOLD", 3)), int(E("CLIENTS", 8))

T0 = time.time()
stats = {k: 0 for k in ("http", "https", "udp", "pg", "bulk_bytes", "udp_stream_bytes", "ws_msgs", "errors")}
lock = threading.Lock()
burst_until = 0.0


def bump(k, n=1):
    with lock:
        stats[k] += n


def setup_clients() -> list[str]:
    ips = []
    for i in range(CLIENTS):
        ip = f"192.168.88.{101 + i}"
        r = subprocess.run(["ip", "addr", "add", f"{ip}/32", "dev", "eth0"], capture_output=True, text=True)
        if r.returncode == 0 or "File exists" in r.stderr:
            ips.append(ip)
    # Announce the extra IPs (gratuitous ARP). Without this, after a container
    # re-create the gateway keeps the old MAC for them for ~30-50s and replies vanish.
    for ip in ips:
        subprocess.run(["arping", "-U", "-c", "2", "-w", "2", "-I", "eth0", "-s", ip, ip], capture_output=True)
    return ips or [""]


IPS: list[str] = []


def src() -> tuple[str, int]:
    return (random.choice(IPS), 0)


def intensity() -> float:
    """Slow wave + a faster ripple + random bursts; always > 0 while LEVEL > 0."""
    t = time.time() - T0
    w = 0.55 + 0.30 * math.sin(2 * math.pi * t / 240) + 0.10 * math.sin(2 * math.pi * t / 37)
    if time.time() < burst_until:
        w *= 2.5
    return max(0.05, w) * LEVEL


def burst_ticker():
    """Once a second: ~1/150 chance to start a 15-30s burst (about one every 2-3 minutes)."""
    global burst_until
    while True:
        time.sleep(1)
        if time.time() > burst_until and random.random() < 1 / 150:
            burst_until = time.time() + random.uniform(15, 30)


def paced(rate_fn, work, pool):
    """Fire `work` at rate_fn() per second, with jitter."""
    while True:
        r = rate_fn()
        if r <= 0:
            time.sleep(1)
            continue
        pool.submit(work)
        time.sleep(random.expovariate(r))


# ---- workloads ----------------------------------------------------------------------
def http_get():
    try:
        c = http.client.HTTPConnection(GW, 80, timeout=5, source_address=src())
        c.request("GET", "/")
        c.getresponse().read()
        c.close()
        bump("http")
    except Exception:
        bump("errors")


CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def https_get():
    try:
        c = http.client.HTTPSConnection(GW, 443, timeout=5, context=CTX, source_address=src())
        c.request("GET", "/")
        c.getresponse().read()
        c.close()
        bump("https")
    except Exception:
        bump("errors")


def udp_ping():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(src())
        s.settimeout(1)
        s.sendto(os.urandom(UDP_BYTES), (GW, 9000))
        s.recv(65535)
        s.close()
        bump("udp")
    except Exception:
        bump("errors")


def pg_loop():
    conn = None
    while True:
        rate = PG_QPS * intensity()
        try:
            if conn is None:
                conn = psycopg2.connect(host=GW, port=5432, user="postgres", password="testpass123",
                                        dbname="world", connect_timeout=5)
                conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(random.choice([
                    "SELECT name, population FROM city ORDER BY random() LIMIT 50",
                    "SELECT countrycode, count(*) FROM city GROUP BY countrycode",
                    "SELECT * FROM city WHERE population > %s LIMIT 200" % random.randint(100000, 5000000),
                ]))
                cur.fetchall()
            bump("pg")
        except Exception:
            bump("errors")
            conn = None
            time.sleep(2)
        time.sleep(random.expovariate(max(rate, 0.05)))


def bulk_stream(share: float):
    """Download /blob.bin from A at a paced rate; reconnect at EOF."""
    while True:
        try:
            c = http.client.HTTPConnection(GW, 80, timeout=10, source_address=src())
            c.request("GET", "/blob.bin")
            r = c.getresponse()
            while True:
                t0 = time.monotonic()
                chunk = r.read(65536)
                if not chunk:
                    break
                bump("bulk_bytes", len(chunk))
                bps = BULK_MBPS * 1e6 * share * intensity()
                if bps > 0:
                    time.sleep(max(0.0, len(chunk) * 8 / bps - (time.monotonic() - t0)))
            c.close()
        except Exception:
            bump("errors")
            time.sleep(2)


def ws_hold():
    while True:
        try:
            s = socket.create_connection((GW, 8765), timeout=5, source_address=src())
            with ws_connect(f"ws://{GW}:8765/", sock=s, open_timeout=5) as ws:
                for _ in ws:
                    bump("ws_msgs")
                    if random.random() < 0.0005:     # occasionally reconnect, like real clients
                        break
        except Exception:
            bump("errors")
            time.sleep(3)


def udp_stream():
    """One persistent UDP socket sending 1200-byte packets at a paced rate; replies are drained."""
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(src())
            s.connect((GW, 9000))
            s.settimeout(0.5)

            def drain():
                while True:
                    try:
                        s.recv(65535)
                    except socket.timeout:
                        continue
                    except OSError:
                        return
            threading.Thread(target=drain, daemon=True).start()
            payload = os.urandom(1200)
            end = time.time() + random.uniform(120, 600)      # a "call" lasts 2-10 minutes
            while time.time() < end:
                bps = UDP_STREAM_MBPS * 1e6 / max(1, UDP_STREAMS) * intensity()
                s.send(payload)
                bump("udp_stream_bytes", len(payload))
                time.sleep(len(payload) * 8 / bps if bps > 0 else 1)
            s.close()
        except Exception:
            bump("errors")
            time.sleep(2)


def rdp_hold():
    while True:
        try:
            s = socket.create_connection((GW, 3389), timeout=5, source_address=src())
            s.sendall(bytes.fromhex("0300000b06e00000000000"))     # X.224 Connection Request
            s.recv(64)
            s.settimeout(None)
            while s.recv(4096):
                pass
        except Exception:
            bump("errors")
        time.sleep(random.uniform(3, 8))


def vnc_hold():
    while True:
        try:
            s = socket.create_connection((GW, 5900), timeout=5, source_address=src())
            s.recv(12)
            s.sendall(b"RFB 003.008\n")
            s.settimeout(None)
            while s.recv(4096):
                pass
        except Exception:
            bump("errors")
        time.sleep(random.uniform(2, 6))


def reporter():
    last = dict(stats)
    while True:
        time.sleep(10)
        cur = dict(stats)
        d = {k: cur[k] - last[k] for k in cur}
        last = cur
        print(f"[{time.strftime('%H:%M:%S')}] intensity={intensity():.2f} "
              f"bulk={d['bulk_bytes'] * 8 / 10 / 1e6:.1f}Mbps http={d['http'] / 10:.1f}/s https={d['https'] / 10:.1f}/s "
              f"udp={d['udp'] / 10:.0f}/s udp_streams={d['udp_stream_bytes'] * 8 / 10 / 1e6:.1f}Mbps pg={d['pg'] / 10:.1f}/s ws_msgs={d['ws_msgs']} errors={d['errors']}", flush=True)


def main():
    global IPS
    IPS = setup_clients()
    print(f"traffic_gen -> {GW}  level={LEVEL}  clients={IPS}", flush=True)
    if LEVEL <= 0:
        threading.Event().wait()
    pool = ThreadPoolExecutor(max_workers=64)
    jobs = [
        lambda: paced(lambda: HTTP_RPS * intensity(), http_get, pool),
        lambda: paced(lambda: HTTPS_RPS * intensity(), https_get, pool),
        lambda: paced(lambda: UDP_PPS * intensity(), udp_ping, pool),
        pg_loop, reporter, burst_ticker,
        lambda: bulk_stream(0.6), lambda: bulk_stream(0.4),
    ] + [ws_hold] * WS_HOLD + [vnc_hold] * VNC_HOLD + [rdp_hold] * RDP_HOLD + [udp_stream] * UDP_STREAMS
    for j in jobs:
        threading.Thread(target=j, daemon=True).start()
    threading.Event().wait()


if __name__ == "__main__":
    main()
