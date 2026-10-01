"""SQLite persistence - the source of truth for forwards, users, audit, history."""
import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from .models import Forward, ForwardIn

SCHEMA = """
CREATE TABLE IF NOT EXISTS forwards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, username TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL, last_used TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL,
    action TEXT NOT NULL, target TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS rollups (
    ts INTEGER NOT NULL, fid INTEGER NOT NULL,
    new INTEGER NOT NULL, bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
    pkts_in INTEGER NOT NULL, pkts_out INTEGER NOT NULL, live_max INTEGER NOT NULL,
    PRIMARY KEY (ts, fid)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS rollups_fid_ts ON rollups (fid, ts);
CREATE INDEX IF NOT EXISTS audit_ts ON audit (ts);

-- One row per connection actually observed by the kernel (not an admin action - see `audit`
-- above for that). Opened the moment a flow is first seen, closed when it disappears from
-- conntrack. While open, bytes/pkts stay at 0 - they're only known for certain, from conntrack's
-- own cumulative counters, at close time; a manager crash mid-connection means that connection's
-- transfer is under-reported (closed with whatever was last recorded, which may be 0). This
-- trade-off keeps the write volume low: two writes per connection's whole lifetime, batched
-- per second, regardless of how long it lasts.
CREATE TABLE IF NOT EXISTS connection_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fid INTEGER NOT NULL,
    proto TEXT NOT NULL,
    client_ip TEXT NOT NULL,
    client_port INTEGER NOT NULL,
    target_ip TEXT NOT NULL,
    target_port INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    bytes_in INTEGER NOT NULL DEFAULT 0,
    bytes_out INTEGER NOT NULL DEFAULT 0,
    pkts_in INTEGER NOT NULL DEFAULT 0,
    pkts_out INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS connlog_fid ON connection_log (fid, started_at);
CREATE INDEX IF NOT EXISTS connlog_client ON connection_log (client_ip, started_at);
CREATE INDEX IF NOT EXISTS connlog_started ON connection_log (started_at);
CREATE INDEX IF NOT EXISTS connlog_open ON connection_log (fid, proto, client_ip, client_port, started_at)
    WHERE ended_at IS NULL;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class ConflictError(ValueError):
    pass


class Store:
    def __init__(self, path):
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def _q(self, sql, args=()):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _x(self, sql, args=()):
        with self._lock:
            cur = self._db.execute(sql, args)
            return cur.lastrowid

    # ---- forwards -------------------------------------------------------
    @staticmethod
    def _row(r) -> Forward:
        return Forward(id=r["id"], created_at=r["created_at"], updated_at=r["updated_at"], **json.loads(r["data"]))

    def forwards(self) -> list[Forward]:
        return sorted((self._row(r) for r in self._q("SELECT * FROM forwards")),
                      key=lambda f: (f.listen_port, f.id))

    def forward(self, fid: int) -> Optional[Forward]:
        rows = self._q("SELECT * FROM forwards WHERE id=?", (fid,))
        return self._row(rows[0]) if rows else None

    def check_conflicts(self, f: ForwardIn, exclude_id: Optional[int] = None):
        if not f.enabled:
            return
        for other in self.forwards():
            if other.id != exclude_id and other.enabled and f.overlaps(other):
                ports = f"{other.listen_port}" + (f"-{other.listen_port_end}" if other.listen_port_end else "")
                raise ConflictError(
                    f"port conflict with forward #{other.id} '{other.name}' ({other.protocol} {ports})")

    def create(self, f: ForwardIn) -> Forward:
        self.check_conflicts(f)
        now = _now()
        fid = self._x("INSERT INTO forwards (data, created_at, updated_at) VALUES (?,?,?)",
                      (f.model_dump_json(), now, now))
        return self.forward(fid)

    def update(self, fid: int, f: ForwardIn) -> Forward:
        self.check_conflicts(f, exclude_id=fid)
        self._x("UPDATE forwards SET data=?, updated_at=? WHERE id=?", (f.model_dump_json(), _now(), fid))
        return self.forward(fid)

    def delete(self, fid: int):
        self._x("DELETE FROM forwards WHERE id=?", (fid,))

    def restore(self, f: Forward):
        """Put a forward back exactly as it was (rollback after a failed kernel apply)."""
        data = ForwardIn(**f.model_dump(exclude={"id", "created_at", "updated_at"})).model_dump_json()
        self._x("INSERT OR REPLACE INTO forwards (id, data, created_at, updated_at) VALUES (?,?,?,?)",
                (f.id, data, f.created_at.isoformat(), f.updated_at.isoformat()))

    def replace_all(self, items: list[ForwardIn]):
        with self._lock:
            self._db.execute("BEGIN")
            try:
                self._db.execute("DELETE FROM forwards")
                now = _now()
                for f in items:
                    self._db.execute("INSERT INTO forwards (data, created_at, updated_at) VALUES (?,?,?)",
                                     (f.model_dump_json(), now, now))
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    # ---- users / sessions / tokens --------------------------------------
    def user_hash(self, username: str) -> Optional[str]:
        rows = self._q("SELECT pw_hash FROM users WHERE username=?", (username,))
        return rows[0]["pw_hash"] if rows else None

    def has_users(self) -> bool:
        return bool(self._q("SELECT 1 FROM users LIMIT 1"))

    def set_user(self, username: str, pw_hash: str):
        self._x("INSERT INTO users (username, pw_hash) VALUES (?,?) "
                "ON CONFLICT(username) DO UPDATE SET pw_hash=excluded.pw_hash", (username, pw_hash))

    def add_session(self, sid: str, username: str, ttl: float):
        self._x("INSERT INTO sessions (id, username, expires) VALUES (?,?,?)", (sha256(sid), username, time.time() + ttl))

    def session_user(self, sid: str) -> Optional[str]:
        rows = self._q("SELECT username, expires FROM sessions WHERE id=?", (sha256(sid),))
        if rows and rows[0]["expires"] > time.time():
            return rows[0]["username"]
        return None

    def drop_session(self, sid: str):
        self._x("DELETE FROM sessions WHERE id=?", (sha256(sid),))

    def drop_user_sessions(self, username: str):
        self._x("DELETE FROM sessions WHERE username=?", (username,))

    def purge_sessions(self):
        self._x("DELETE FROM sessions WHERE expires < ?", (time.time(),))

    def add_token(self, name: str, token: str) -> int:
        return self._x("INSERT INTO tokens (name, token_hash, created_at) VALUES (?,?,?)", (name, sha256(token), _now()))

    def token_name(self, token: str) -> Optional[str]:
        rows = self._q("SELECT id, name FROM tokens WHERE token_hash=?", (sha256(token),))
        if not rows:
            return None
        self._x("UPDATE tokens SET last_used=? WHERE id=?", (_now(), rows[0]["id"]))
        return rows[0]["name"]

    def tokens(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT id, name, created_at, last_used FROM tokens ORDER BY id")]

    def delete_token(self, tid: int) -> bool:
        before = len(self._q("SELECT 1 FROM tokens WHERE id=?", (tid,)))
        self._x("DELETE FROM tokens WHERE id=?", (tid,))
        return bool(before)

    # ---- audit ------------------------------------------------------------
    def audit(self, actor: str, action: str, target: str = "", detail=None):
        self._x("INSERT INTO audit (ts, actor, action, target, detail) VALUES (?,?,?,?,?)",
                (_now(), actor, action, target, json.dumps(detail, default=str) if detail is not None else None))

    def audit_log(self, limit: int = 200, before_id: Optional[int] = None) -> list[dict]:
        if before_id:
            rows = self._q("SELECT * FROM audit WHERE id < ? ORDER BY id DESC LIMIT ?", (before_id, limit))
        else:
            rows = self._q("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d["detail"]) if d["detail"] else None
            out.append(d)
        return out

    # ---- history rollups ----------------------------------------------------
    def write_rollups(self, rows: list[tuple]):
        with self._lock:
            self._db.executemany(
                "INSERT OR REPLACE INTO rollups (ts, fid, new, bytes_in, bytes_out, pkts_in, pkts_out, live_max) "
                "VALUES (?,?,?,?,?,?,?,?)", rows)

    def rollups(self, fid: int, since: int, bucket: int) -> list[dict]:
        rows = self._q(
            "SELECT (ts / ?) * ? AS t, SUM(new) new, SUM(bytes_in) bytes_in, SUM(bytes_out) bytes_out, "
            "SUM(pkts_in) pkts_in, SUM(pkts_out) pkts_out, MAX(live_max) live_max "
            "FROM rollups WHERE fid=? AND ts>=? GROUP BY t ORDER BY t", (bucket, bucket, fid, since))
        return [dict(r) for r in rows]

    def purge_rollups(self, older_than: int):
        self._x("DELETE FROM rollups WHERE ts < ?", (older_than,))

    def purge_audit(self, older_than_iso: str) -> int:
        before = self._q("SELECT COUNT(*) n FROM audit WHERE ts < ?", (older_than_iso,))[0]["n"]
        self._x("DELETE FROM audit WHERE ts < ?", (older_than_iso,))
        return before

    def storage_stats(self) -> dict:
        r = self._q("SELECT (SELECT COUNT(*) FROM rollups) rollups, (SELECT MIN(ts) FROM rollups) oldest_rollup, "
                    "(SELECT COUNT(*) FROM audit) audit, (SELECT MIN(ts) FROM audit) oldest_audit, "
                    "(SELECT COUNT(*) FROM connection_log) connections, "
                    "(SELECT MIN(started_at) FROM connection_log) oldest_connection")[0]
        return dict(r)

    # ---- connection log (real traffic, not admin actions - see `audit` above) -----------
    def record_connection_opens(self, rows: list[tuple]):
        """rows: (fid, proto, client_ip, client_port, target_ip, target_port, started_at)"""
        if not rows:
            return
        with self._lock:
            self._db.executemany(
                "INSERT INTO connection_log (fid, proto, client_ip, client_port, target_ip, target_port, started_at) "
                "VALUES (?,?,?,?,?,?,?)", rows)

    def record_connection_closes(self, rows: list[tuple]):
        """rows: (bytes_in, bytes_out, pkts_in, pkts_out, ended_at, fid, proto, client_ip, client_port, started_at)
        Matches the still-open row with the same identity + start time - there can be at most one,
        since a new flow with the exact same (forward, protocol, client ip, client port) can only
        become "new" again after the previous one has already closed."""
        if not rows:
            return
        with self._lock:
            self._db.executemany(
                "UPDATE connection_log SET bytes_in=?, bytes_out=?, pkts_in=?, pkts_out=?, ended_at=? "
                "WHERE fid=? AND proto=? AND client_ip=? AND client_port=? AND started_at=? AND ended_at IS NULL",
                rows)

    def close_orphaned_connections(self, ended_at_iso: str) -> int:
        """On startup: any row still open belongs to a process life that ended without closing it
        (a crash or an unclean stop) - close it now so it doesn't stay "open" forever. Its byte
        counts stay whatever they were last recorded as (see the schema comment)."""
        n = self._q("SELECT COUNT(*) n FROM connection_log WHERE ended_at IS NULL")[0]["n"]
        if n:
            self._x("UPDATE connection_log SET ended_at=? WHERE ended_at IS NULL", (ended_at_iso,))
        return n

    def connection_log(self, forward_id: Optional[int] = None, client_ip: Optional[str] = None,
                       since: Optional[str] = None, limit: int = 200, before_id: Optional[int] = None) -> list[dict]:
        where, args = [], []
        if forward_id is not None:
            where.append("fid=?"); args.append(forward_id)
        if client_ip:
            where.append("client_ip=?"); args.append(client_ip)
        if since:
            where.append("started_at>=?"); args.append(since)
        if before_id:
            where.append("id<?"); args.append(before_id)
        sql = "SELECT * FROM connection_log"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self._q(sql, args)]

    def connection_log_summary(self, group_by: str = "client", since: Optional[str] = None,
                               limit: int = 50) -> list[dict]:
        col = "client_ip" if group_by == "client" else "fid"
        where, args = "", []
        if since:
            where = "WHERE started_at>=?"
            args.append(since)
        args.append(limit)
        rows = self._q(
            f"SELECT {col} AS key, COUNT(*) sessions, SUM(bytes_in) bytes_in, SUM(bytes_out) bytes_out, "
            f"SUM(pkts_in) pkts_in, SUM(pkts_out) pkts_out, MAX(started_at) last_seen, "
            f"SUM(CASE WHEN ended_at IS NULL THEN 1 ELSE 0 END) live "
            f"FROM connection_log {where} GROUP BY {col} ORDER BY (bytes_in + bytes_out) DESC LIMIT ?", args)
        return [dict(r) for r in rows]

    def purge_connection_log(self, older_than_iso: str) -> int:
        before = self._q("SELECT COUNT(*) n FROM connection_log WHERE started_at < ? AND ended_at IS NOT NULL",
                         (older_than_iso,))[0]["n"]
        self._x("DELETE FROM connection_log WHERE started_at < ? AND ended_at IS NOT NULL", (older_than_iso,))
        return before

    # ---- meta ---------------------------------------------------------------
    def meta(self, key: str) -> Optional[str]:
        rows = self._q("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0]["value"] if rows else None

    def set_meta(self, key: str, value: str):
        self._x("INSERT INTO meta (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
