"""1 Hz metrics: nft counters + conntrack -> per-forward rates, live flows, ring buffer, minute rollups."""
import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import config, engine
from .models import Forward

log = logging.getLogger("pm.collector")

TCP_LIVE_STATES = {"SYN_SENT", "SYN_RECV", "ESTABLISHED", "FIN_WAIT", "CLOSE_WAIT", "LAST_ACK"}


def _read(path: str, default: str = "") -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def _int(path: str) -> int:
    try:
        return int(_read(path, "0"))
    except ValueError:
        return 0


def _host_mem_total() -> int:
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    return 0


def is_live(fl: engine.Flow) -> bool:
    return fl.state in TCP_LIVE_STATES if fl.proto == "tcp" else True


LIVE_WINDOW_S = 300   # the "Live" chart: last 5 minutes at 1 s resolution

# range -> (span seconds or None for everything, bucket seconds); ~120-365 points each
HISTORY_RANGES = {
    "24h": (86400, 300),
    "7d": (7 * 86400, 3600),
    "30d": (30 * 86400, 6 * 3600),
    "1y": (365 * 86400, 86400),
    "all": (None, 86400),
}


class Collector:
    def __init__(self, store, get_forwards: Callable[[], list[Forward]], prober):
        self.store = store
        self.get_forwards = get_forwards
        self.prober = prober
        self.prev: dict[str, tuple[int, int]] = {}
        self.prev_ts = 0.0
        self.prev_if: dict[str, tuple[int, int]] = {}
        self.prev_cpu = (0, 0.0)
        self.ring: dict[int, deque] = defaultdict(lambda: deque(maxlen=config.RING_SECONDS))
        self.flows_by_fid: dict[int, list[engine.Flow]] = {}
        self.snapshot: dict = {"ts": 0, "forwards": {}, "global": {}}
        self.tick_event = asyncio.Event()
        self.minute = int(time.time() // 60)
        self.acc: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])  # new,bin,bout,pin,pout,live_max
        self.started = time.time()
        self.version = 0   # bumped by the manager when forwards change
        self.open_sessions: dict[tuple, dict] = {}   # (fid, proto, src, sport) -> connection_log bookkeeping
        self.closed_keys: set[tuple] = set()   # already logged as closed but still lingering in conntrack (e.g. TIME_WAIT)
        # Rows left open by a previous process are adopted by the first tick if their connection is still
        # there - a restart must not split one live session into two rows. Any not seen by that tick ended
        # while the manager was down, and are closed then with their last recorded byte counts.
        self._adopt: dict[tuple, dict] = {(r["fid"], r["proto"], r["client_ip"], r["client_port"]): r
                                          for r in self.store.open_connections()}
        self.last_conn_purge = 0.0

    # ------------------------------------------------------------------
    async def run(self):
        while True:
            t0 = time.monotonic()
            try:
                await self.tick()
            except Exception:
                log.exception("collector tick failed")
            await asyncio.sleep(max(0.05, 1.0 - (time.monotonic() - t0)))

    def _delta(self, name: str, counters: dict) -> tuple[int, int]:
        cur = counters.get(name, (0, 0))
        prev = self.prev.get(name)
        if prev is None:
            return 0, 0
        dp, db = cur[0] - prev[0], cur[1] - prev[1]
        if dp < 0 or db < 0:          # counter reset (gateway restart / forward recreated)
            return cur
        return dp, db

    async def tick(self):
        now = time.time()
        counters, flows = await asyncio.gather(engine.read_counters(), engine.list_flows())
        dt = (now - self.prev_ts) if self.prev_ts else 1.0
        forwards = self.get_forwards()

        # index DNAT'd flows by forward
        idx = {}
        for f in engine.active(forwards):
            for proto in f.protocols:
                for p in f.listen_ports:
                    idx[(proto, p)] = f
        by_fid: dict[int, list[engine.Flow]] = defaultdict(list)
        for fl in flows:
            f = idx.get((fl.proto, fl.dport))
            if f and fl.dst == str(config.GW_LAN_IP) and fl.reply_src == str(f.target_ip):
                by_fid[f.id].append(fl)
        self.flows_by_fid = by_fid
        self._track_connection_log(by_fid, now)

        fw_stats, g = {}, defaultdict(float)
        for f in forwards:
            n = f"f{f.id}"
            d_new, _ = self._delta(f"{n}_new", counters)
            dpi, dbi = self._delta(f"{n}_in", counters)
            dpo, dbo = self._delta(f"{n}_out", counters)
            fl = by_fid.get(f.id, [])
            live = sum(1 for x in fl if is_live(x))
            clients = defaultdict(lambda: [0, 0])
            for x in fl:
                clients[x.src][0] += 1
                clients[x.src][1] += x.bytes_in + x.bytes_out
            top = sorted(({"ip": ip, "conns": c, "bytes": b} for ip, (c, b) in clients.items()),
                         key=lambda d: (-d["conns"], -d["bytes"]))[:5]
            st = {
                "id": f.id,
                "new_total": counters.get(f"{n}_new", (0, 0))[0],
                "bytes_in_total": counters.get(f"{n}_in", (0, 0))[1],
                "bytes_out_total": counters.get(f"{n}_out", (0, 0))[1],
                "pkts_in_total": counters.get(f"{n}_in", (0, 0))[0],
                "pkts_out_total": counters.get(f"{n}_out", (0, 0))[0],
                "rate_limited_total": counters.get(f"{n}_rl", (0, 0))[0],
                "conn_limited_total": counters.get(f"{n}_cl", (0, 0))[0],
                "new_per_s": round(d_new / dt, 2),
                "in_bps": round(dbi * 8 / dt),
                "out_bps": round(dbo * 8 / dt),
                "live": live,
                "flows": len(fl),
                "top_clients": top,
                "health": self.prober.status.get(f.id),
            }
            fw_stats[f.id] = st
            self.ring[f.id].append((int(now), st["new_per_s"], st["in_bps"], st["out_bps"], live))
            a = self.acc[f.id]
            a[0] += d_new; a[1] += dbi; a[2] += dbo; a[3] += dpi; a[4] += dpo; a[5] = max(a[5], live)
            g["new_per_s"] += st["new_per_s"]; g["in_bps"] += st["in_bps"]; g["out_bps"] += st["out_bps"]
            g["live"] += live
            g["d_new"] += d_new; g["dbi"] += dbi; g["dbo"] += dbo; g["dpi"] += dpi; g["dpo"] += dpo

        ga = self.acc[0]
        ga[0] += int(g["d_new"]); ga[1] += int(g["dbi"]); ga[2] += int(g["dbo"])
        ga[3] += int(g["dpi"]); ga[4] += int(g["dpo"]); ga[5] = max(ga[5], int(g["live"]))
        self.ring[0].append((int(now), round(g["new_per_s"], 2), int(g["in_bps"]), int(g["out_bps"]), int(g["live"])))

        glob = {
            "live": int(g["live"]),
            "new_per_s": round(g["new_per_s"], 2),
            "in_bps": int(g["in_bps"]),
            "out_bps": int(g["out_bps"]),
            "forwards_total": len(forwards),
            "forwards_active": len(engine.active(forwards)),
            "conntrack_count": _int("/proc/sys/net/netfilter/nf_conntrack_count"),
            "conntrack_max": _int("/proc/sys/net/netfilter/nf_conntrack_max"),
            "interfaces": self._interfaces(now, dt),
            "system": self._system(now),
            "uptime_s": int(now - self.started),
            "kernel_table": bool(counters),
            "version": self.version,
        }
        self.prev, self.prev_ts = counters, now
        self.snapshot = {"ts": now, "forwards": fw_stats, "global": glob}
        self._rollup(now)
        self.tick_event.set()
        self.tick_event = asyncio.Event()

    # ------------------------------------------------------------------
    @staticmethod
    def _iface_state(base: str) -> str:
        """operstate, with a fallback for interface types that never report it.

        TUN-style interfaces (Tailscale, and some VPN clients) don't implement
        carrier detection, so their operstate sits at "unknown" forever, even
        while fully up and passing real traffic - confirmed directly (this
        exact case: Tailscale's tailscale0 reports operstate=unknown but
        carrier=1 throughout). `carrier` is the more reliable signal for those;
        real NICs and veth pairs already report a correct operstate, so this
        fallback only ever fires when operstate itself couldn't say.
        """
        op = _read(f"{base}/operstate", "unknown")
        if op != "unknown":
            return op
        carrier = _read(f"{base}/carrier", "").strip()
        return {"1": "up", "0": "down"}.get(carrier, "unknown")

    def _interfaces(self, now: float, dt: float) -> dict:
        out = {}
        for role, name in (("lan", config.LAN_IF), ("vpn", config.VPN_IF)):
            base = f"/sys/class/net/{name}"
            if not os.path.exists(base):
                out[role] = {"name": name, "state": "missing", "rx_bps": 0, "tx_bps": 0}
                continue
            rx, tx = _int(f"{base}/statistics/rx_bytes"), _int(f"{base}/statistics/tx_bytes")
            prx, ptx = self.prev_if.get(name, (rx, tx))
            self.prev_if[name] = (rx, tx)
            out[role] = {"name": name, "state": self._iface_state(base),
                         "rx_bps": max(0, round((rx - prx) * 8 / dt)), "tx_bps": max(0, round((tx - ptx) * 8 / dt)),
                         "rx_total": rx, "tx_total": tx}
        return out

    def _system(self, now: float) -> dict:
        usage = 0
        for line in _read("/sys/fs/cgroup/cpu.stat").splitlines():
            if line.startswith("usage_usec"):
                usage = int(line.split()[1])
        pu, pt = self.prev_cpu
        cpu = (usage - pu) / 1e6 / (now - pt) / (os.cpu_count() or 1) * 100 if pt else 0.0
        self.prev_cpu = (usage, now)
        mem = _int("/sys/fs/cgroup/memory.current")
        mmax = _read("/sys/fs/cgroup/memory.max", "max")
        return {"cpu_pct": round(max(cpu, 0.0), 1), "mem_bytes": mem,
                "mem_limit": int(mmax) if mmax.isdigit() else _host_mem_total(),
                "load": os.getloadavg()[0]}

    def current_minute_bytes(self, fid: int) -> int:
        """Bytes (in + out) seen so far this minute, not yet written to the database. Read with .get so
        checking a forward never creates an accumulator for it."""
        a = self.acc.get(fid)
        return a[1] + a[2] if a else 0

    def _rollup(self, now: float):
        minute = int(now // 60)
        if minute == self.minute:
            return
        ts = self.minute * 60
        rows = [(ts, fid, *vals) for fid, vals in self.acc.items()]
        self.acc.clear()
        self.minute = minute
        try:
            self.store.write_rollups(rows)
            if minute % 60 == 0:
                if config.HISTORY_RETENTION_S:
                    self.store.purge_rollups(int(now) - config.HISTORY_RETENTION_S)
                if config.CONNECTION_LOG_RETENTION_S:
                    cutoff = datetime.fromtimestamp(now - config.CONNECTION_LOG_RETENTION_S, timezone.utc).isoformat()
                    self.store.purge_connection_log(cutoff)
        except Exception:
            log.exception("rollup write failed")

    def _track_connection_log(self, by_fid: dict, now: float):
        """Open a connection-log row the first time a flow is seen *in any state*, and close it the
        moment it's no longer live - using the same `is_live()` test as the dashboard's own
        live-connection count, not mere presence in conntrack. A finished TCP connection lingers in
        conntrack through TIME_WAIT for a couple of minutes by default; without the live-state check,
        a session would be reported as still "open" (with its real duration badly inflated) for that
        whole lingering period after the actual transfer already finished.

        Opening must still key off *any* appearance, not just a live one: on a fast local network, a
        short request can go from not-existing to ESTABLISHED to TIME_WAIT entirely between two 1 s
        polls, so the first (and only) observation of it may already be non-live. Keying the open off
        `is_live()` too would silently drop every connection that happens to finish within one polling
        interval - a real bug caught by testing against actual traffic, not synthetic flows. Such a
        connection is opened and closed within the very same tick instead, using its own already-final
        byte counts (conntrack keeps a flow's counters accurate right up until the entry is actually
        removed, even once non-live, and only a flow that vanishes without any such observation falls
        back to the last counts seen while it was live).

        A connection stays in conntrack, lingering in TIME_WAIT, for a while after it's closed and
        logged - it would otherwise look "new" again on every following tick for as long as it
        lingers, producing a duplicate open+close row each second (caught, again, by watching real
        traffic rather than trusting synthetic single-tick test fixtures). `closed_keys` remembers
        which already-logged flows are still merely lingering so they're not reopened, and is pruned
        of any key that actually vanishes from conntrack, so a later, genuinely new connection reusing
        the same client port is still tracked correctly.

        Writes are batched once per tick, regardless of how many connections opened or closed, to keep
        this cheap even under heavy churn."""
        now_iso = datetime.now(timezone.utc).isoformat()
        all_flows = {(fid, fl.proto, fl.src, fl.sport): fl for fid, flows in by_fid.items() for fl in flows}

        opens, closes = [], []
        for key, fl in all_flows.items():
            fid, proto, src, sport = key
            if key in self.closed_keys:
                continue   # already logged as closed; just lingering in conntrack (e.g. TIME_WAIT)
            existing = self.open_sessions.get(key)
            if existing is None and key in self._adopt:
                row = self._adopt.pop(key)   # the same connection, seen again after a restart: keep its row
                self.open_sessions[key] = {"started_at": row["started_at"], "bytes_in": fl.bytes_in,
                                           "bytes_out": fl.bytes_out, "pkts_in": fl.pkts_in, "pkts_out": fl.pkts_out}
                continue
            if existing is None:
                self.open_sessions[key] = {"started_at": now_iso, "bytes_in": fl.bytes_in,
                                           "bytes_out": fl.bytes_out, "pkts_in": fl.pkts_in, "pkts_out": fl.pkts_out}
                opens.append((fid, proto, src, sport, fl.reply_src, fl.reply_sport, now_iso))
            else:
                existing["bytes_in"], existing["bytes_out"] = fl.bytes_in, fl.bytes_out
                existing["pkts_in"], existing["pkts_out"] = fl.pkts_in, fl.pkts_out
        for key in list(self.open_sessions):
            fl = all_flows.get(key)
            if fl is not None and is_live(fl):
                continue   # still genuinely open
            s = self.open_sessions.pop(key)
            fid, proto, src, sport = key
            bi, bo, pi, po = (fl.bytes_in, fl.bytes_out, fl.pkts_in, fl.pkts_out) if fl else \
                (s["bytes_in"], s["bytes_out"], s["pkts_in"], s["pkts_out"])
            closes.append((bi, bo, pi, po, now_iso, fid, proto, src, sport, s["started_at"]))
            self.closed_keys.add(key)
        self.closed_keys &= all_flows.keys()   # drop keys that have fully vanished from conntrack
        if self._adopt:   # rows from before the restart whose connection is gone: they ended while we were down
            for key, r in self._adopt.items():
                closes.append((r["bytes_in"], r["bytes_out"], r["pkts_in"], r["pkts_out"], now_iso,
                               key[0], key[1], key[2], key[3], r["started_at"]))
            self._adopt.clear()
        try:
            self.store.record_connection_opens(opens)
            self.store.record_connection_closes(closes)
        except Exception:
            log.exception("connection-log write failed")

    # ------------------------------------------------------------------
    def history(self, fid: int, rng: str) -> dict:
        if rng == "live":
            pts = list(self.ring.get(fid, ()))[-LIVE_WINDOW_S:]
            return {"range": rng, "step_s": 1, "window_s": LIVE_WINDOW_S, "points": [
                {"t": p[0], "new_per_s": p[1], "in_bps": p[2], "out_bps": p[3], "live": p[4]} for p in pts]}
        if rng == "1h":
            pts = list(self.ring.get(fid, ()))
            step = 5
            buckets = []
            for i in range(0, len(pts), step):
                chunk = pts[i:i + step]
                buckets.append({"t": chunk[-1][0],
                                "new_per_s": round(sum(p[1] for p in chunk) / len(chunk), 2),
                                "in_bps": sum(p[2] for p in chunk) // len(chunk),
                                "out_bps": sum(p[3] for p in chunk) // len(chunk),
                                "live": max(p[4] for p in chunk)})
            return {"range": rng, "step_s": step, "points": buckets}
        span, bucket = HISTORY_RANGES.get(rng, HISTORY_RANGES["24h"])
        rows = self.store.rollups(fid, int(time.time()) - span if span else 0, bucket)
        pts = [{"t": r["t"], "new_per_s": round(r["new"] / bucket, 3),
                "in_bps": r["bytes_in"] * 8 // bucket, "out_bps": r["bytes_out"] * 8 // bucket,
                "live": r["live_max"]} for r in rows]
        return {"range": rng, "step_s": bucket, "points": pts}

    def sparkline(self, fid: int, n: int = 60) -> list[list]:
        pts = list(self.ring.get(fid, ()))[-n:]
        return [[p[0], p[2] + p[3], p[4]] for p in pts]
