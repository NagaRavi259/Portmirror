"""Unit tests. Run inside the gateway image: `docker exec pm-E sh -c 'cd /opt/pm && pytest -q tests'`.
The nft -c checks need CAP_NET_ADMIN (they only dry-run against the kernel, nothing is changed)."""
import asyncio
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

os.environ.setdefault("PM_STATE_DIR", "/tmp/pm-unit")
os.makedirs(os.environ["PM_STATE_DIR"], exist_ok=True)  # main.py does this at startup; this module bypasses main.py

from app import config, engine  # noqa: E402
from app.models import Forward, ForwardIn  # noqa: E402
from app.store import ConflictError, Store  # noqa: E402

NOW = datetime.now(timezone.utc)


def fwd(fid=1, **kw) -> Forward:
    base = dict(name=f"f{fid}", protocol="tcp", listen_port=80, target_ip="10.0.0.12", target_port=80)
    base.update(kw)
    return Forward(id=fid, created_at=NOW, updated_at=NOW, **base)


# ---- validation ---------------------------------------------------------------
@pytest.mark.parametrize("kw,msg", [
    (dict(target_ip="192.168.88.50"), "inside the VPN network"),
    (dict(target_ip="10.0.0.88"), "can't be the gateway"),
    (dict(target_ip="10.0.0.255"), "can't be the gateway"),
    (dict(listen_port=8088), "reserved for the manager UI"),
    (dict(listen_port=8000, listen_port_end=8100), "reserved for the manager UI"),
    (dict(listen_port=100, listen_port_end=90), "must be >= listen_port"),
    (dict(listen_port=1000, listen_port_end=3000), "port range too large"),
    (dict(listen_port=100, listen_port_end=200, target_port=65500), "runs past 65535"),
    (dict(allowed_sources=[]), "can't be empty"),
    (dict(listen_port=0), "greater than or equal to 1"),
    (dict(name=""), "at least 1 character"),
    (dict(bandwidth_limit_kbps=0), "greater than or equal to 1"),
])
def test_rejects(kw, msg):
    base = dict(name="x", protocol="tcp", listen_port=80, target_ip="10.0.0.12", target_port=80)
    base.update(kw)
    with pytest.raises(ValidationError, match=msg):
        ForwardIn(**base)


def test_udp_may_use_reserved_tcp_port():
    ForwardIn(name="x", protocol="udp", listen_port=8088, target_ip="10.0.0.12", target_port=8088)


def test_lenient_sources_and_range_normalisation():
    f = ForwardIn(name="x", listen_port=80, listen_port_end=80, target_ip="10.0.0.12", target_port=80,
                  allowed_sources=["192.168.88.5", "192.168.88.77/24"])
    assert f.listen_port_end is None
    assert [str(s) for s in f.allowed_sources] == ["192.168.88.5/32", "192.168.88.0/24"]


def test_overlap_rules():
    a = fwd(1, listen_port=5000, listen_port_end=5010)
    assert a.overlaps(fwd(2, listen_port=5010))
    assert not a.overlaps(fwd(2, listen_port=5011))
    assert not a.overlaps(fwd(2, protocol="udp", listen_port=5005))
    assert a.overlaps(fwd(2, protocol="both", listen_port=5005))


def test_expiry():
    assert fwd(expires_at=NOW - timedelta(seconds=1)).expired()
    assert not fwd(expires_at=NOW + timedelta(hours=1)).expired()
    assert engine.active([fwd(1, expires_at=NOW - timedelta(seconds=1)), fwd(2, listen_port=81)])[0].id == 2


# ---- rule generation -------------------------------------------------------------
def test_single_port_rules():
    r = engine.prerouting_rules(fwd(7))
    assert r == ['iifname "lan0" ip saddr @f7_src tcp dport 80 counter name "f7_new" dnat to 10.0.0.12:80']


def test_both_protocols_and_limits():
    r = engine.prerouting_rules(fwd(3, protocol="both", listen_port=9000, target_port=9001, rate_limit=20, max_conns=5))
    assert len(r) == 6
    assert sum("tcp dport 9000" in x for x in r) == 3 and sum("udp dport 9000" in x for x in r) == 3
    assert any("limit rate over 20/second" in x and x.endswith("drop") for x in r)
    assert any("ct count over 5" in x for x in r)
    assert set(engine.counter_names(fwd(3, rate_limit=1, max_conns=1))) == {"f3_new", "f3_in", "f3_out", "f3_rl", "f3_cl"}


def test_range_rules():
    same = engine.prerouting_rules(fwd(4, listen_port=6000, listen_port_end=6002, target_port=6000))[0]
    assert same.endswith("dport 6000-6002 counter name \"f4_new\" dnat to 10.0.0.12")
    shifted = engine.prerouting_rules(fwd(5, listen_port=6000, listen_port_end=6002, target_port=7000))[0]
    assert "dnat to 10.0.0.12 : tcp dport map { 6000 : 7000, 6001 : 7001, 6002 : 7002 }" in shifted


def test_script_removes_stale_and_keeps_disabled_counters():
    fs = [fwd(1), fwd(2, listen_port=81, enabled=False)]
    ks = engine.KernelState(exists=True, chains={"prerouting", "forward", "f1", "f9"},
                            sets={"f1_src", "f9_src"}, counters={"f1_new", "f9_new", "f2_new"})
    s = engine.build_script(fs, ks)
    assert "delete chain ip pm f9" in s and "delete set ip pm f9_src" in s and "delete counter ip pm f9_new" in s
    assert "delete counter ip pm f2_new" not in s          # disabled forward keeps its totals
    assert "add chain ip pm f2" not in s                   # ...but has no rules
    assert "tcp . 80 : jump f1" in s and "jump f2" not in s


def test_empty_script_has_no_vmap_but_kill_rules():
    s = engine.build_script([], engine.KernelState())
    assert "vmap" not in s
    assert "@kill4 reject with tcp reset" in s and "@kill4 drop" in s


def test_kill_set_is_never_deleted_or_flushed():
    s = engine.build_script([], engine.KernelState(exists=True, sets={"kill4", "f3_src"}))
    assert "delete set ip pm f3_src" in s
    assert "kill4" not in " ".join(l for l in s.splitlines() if l.startswith(("delete", "flush set")))


def test_expected_rule_count():
    fs = [fwd(1), fwd(2, listen_port=90, protocol="both", rate_limit=5), fwd(3, listen_port=99, enabled=False)]
    assert engine.expected_prerouting_rules(fs) == 1 + 4


# ---- bandwidth shaping: nft marking + the pure tc-target function -----------------
def test_bandwidth_limited_forward_gets_marked_unlimited_does_not():
    s = engine.build_script([fwd(1, bandwidth_limit_kbps=500), fwd(2, listen_port=81)], engine.KernelState())
    assert "add rule ip pm f1 meta mark set 1" in s
    assert "meta mark set 2" not in s


def test_htb_burst_scales_with_rate_and_has_a_floor():
    assert engine._htb_burst_bytes(5000) == int(5000 * 1000 / 8 * 0.05)
    assert engine._htb_burst_bytes(20000) == int(20000 * 1000 / 8 * 0.05)
    assert engine._htb_burst_bytes(1) == 4096          # floor keeps very low rates from an unusably tiny bucket


def test_want_bandwidth_ignores_disabled_expired_and_unset():
    fs = [fwd(1, bandwidth_limit_kbps=1000), fwd(2, listen_port=81),
         fwd(3, listen_port=82, bandwidth_limit_kbps=2000, enabled=False),
         fwd(4, listen_port=83, bandwidth_limit_kbps=3000, expires_at=NOW - timedelta(seconds=1))]
    assert engine.want_bandwidth(fs) == {1: 1000}


# ---- generated scripts are valid nftables (dry-run against the real kernel) -------
def _nft_ok() -> bool:
    if not shutil.which("nft"):
        return False
    return subprocess.run(["nft", "list", "tables"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _nft_ok(), reason="needs nft + CAP_NET_ADMIN")
@pytest.mark.parametrize("fs", [
    [],
    [fwd(1)],
    [fwd(1), fwd(2, protocol="udp", listen_port=9000, target_ip="10.0.0.15", target_port=9000)],
    [fwd(3, protocol="both", listen_port=6000, listen_port_end=6010, target_port=7000, rate_limit=50, max_conns=10,
         allowed_sources=["192.168.88.0/25", "192.168.88.200"])],
    [fwd(4, listen_port=20000, listen_port_end=21023, target_port=20000)],
    [fwd(6, bandwidth_limit_kbps=500)],
])
def test_nft_accepts(fs):
    for script in (engine.build_script(fs, engine.KernelState()), engine.build_boot_script(fs)):
        p = subprocess.run(["nft", "-c", "-f", "-"], input=script, text=True, capture_output=True)
        assert p.returncode == 0, p.stderr + "\n" + script


# ---- conntrack parsing ------------------------------------------------------------
CT = """tcp      6 431999 ESTABLISHED src=192.168.88.67 dst=192.168.88.8 sport=5000 dport=80 packets=5 bytes=300 src=10.0.0.12 dst=10.0.0.88 sport=80 dport=5000 packets=4 bytes=5000 [ASSURED] mark=0 use=1
udp      17 28 src=192.168.88.23 dst=192.168.88.8 sport=41000 dport=9000 packets=1 bytes=33 src=10.0.0.15 dst=10.0.0.88 sport=9000 dport=41000 packets=1 bytes=38 mark=0 use=1
udp      17 25 src=192.168.88.23 dst=192.168.88.8 sport=41001 dport=9000 [UNREPLIED] src=10.0.0.15 dst=10.0.0.88 sport=9000 dport=41001 mark=0 use=1
conntrack v1.4.8 (conntrack-tools): 3 flow entries have been shown.
"""


def test_parse_conntrack():
    fl = engine.parse_conntrack(CT)
    assert len(fl) == 3
    t = fl[0]
    assert (t.proto, t.state, t.src, t.sport, t.dport, t.reply_src) == ("tcp", "ESTABLISHED", "192.168.88.67", 5000, 80, "10.0.0.12")
    assert (t.bytes_in, t.bytes_out, t.pkts_in, t.pkts_out) == (300, 5000, 5, 4)
    assert fl[1].state == "ACTIVE" and fl[1].bytes_out == 38
    assert fl[2].state == "UNREPLIED"
    assert engine.flow_matches(t, fwd(1))
    assert not engine.flow_matches(t, fwd(1, target_ip="10.0.0.13"))


# ---- store --------------------------------------------------------------------
@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


def _in(**kw):
    base = dict(name="x", protocol="tcp", listen_port=80, target_ip="10.0.0.12", target_port=80)
    base.update(kw)
    return ForwardIn(**base)


def test_store_conflicts(store):
    a = store.create(_in())
    with pytest.raises(ConflictError, match=f"#{a.id}"):
        store.create(_in(name="dup"))
    store.create(_in(name="udp twin", protocol="udp"))          # different protocol: fine
    store.create(_in(name="disabled twin", enabled=False))      # disabled: fine
    store.update(a.id, _in(name="renamed"))                     # updating itself: fine


def test_store_restore_roundtrip(store):
    a = store.create(_in(description="keep me"))
    store.delete(a.id)
    store.restore(a)
    b = store.forward(a.id)
    assert b.description == "keep me" and b.created_at == a.created_at


def test_audit_and_tokens(store):
    store.audit("admin", "forward.create", "#1", {"x": 1})
    assert store.audit_log()[0]["detail"] == {"x": 1}
    store.add_token("ci", "pm_secret")
    assert store.token_name("pm_secret") == "ci" and store.token_name("nope") is None


# ---- retention ------------------------------------------------------------------
def test_retention_parsing(monkeypatch):
    for raw, want in (("", None), ("0", None), ("30", 30 * 86400), ("0.5", 43200)):
        monkeypatch.setenv("PM_X_DAYS", raw)
        assert config._retention_s("PM_X_DAYS") == want
    monkeypatch.delenv("PM_X_DAYS")
    assert config._retention_s("PM_X_DAYS") is None
    monkeypatch.setenv("PM_X_DAYS", "-1")
    with pytest.raises(ValueError):
        config._retention_s("PM_X_DAYS")


def test_default_retention_is_forever():
    assert "PM_HISTORY_RETENTION_DAYS" not in os.environ or os.environ["PM_HISTORY_RETENTION_DAYS"] in ("", "0")
    assert config.HISTORY_RETENTION_S is None and config.AUDIT_RETENTION_S is None


def test_audit_purge_only_removes_older(store):
    store._x("INSERT INTO audit (ts, actor, action) VALUES ('2020-01-01T00:00:00+00:00', 'a', 'old')")
    store.audit("a", "new")
    assert store.purge_audit("2021-01-01T00:00:00+00:00") == 1
    assert [x["action"] for x in store.audit_log()] == ["new"]


def test_collector_never_purges_history_when_infinite(store, monkeypatch):
    from app.collector import Collector
    monkeypatch.setattr(config, "HISTORY_RETENTION_S", None)
    old = int(NOW.timestamp()) - 400 * 86400
    store.write_rollups([(old, 0, 1, 2, 3, 4, 5, 6)])
    c = Collector(store, lambda: [], type("P", (), {"status": {}})())
    top = (int(NOW.timestamp()) // 3600) * 3600   # purges run on the top-of-hour rollup
    c.minute = 0
    c._rollup(top)
    assert store.rollups(0, 0, 86400)[0]["new"] == 1
    assert c.history(0, "all")["points"][0]["t"] == (old // 86400) * 86400
    monkeypatch.setattr(config, "HISTORY_RETENTION_S", 86400)
    c.minute = 0
    c._rollup(top)
    assert store.rollups(0, 0, 86400) == []


# ---- stdin-via-tempfile (uvloop/nft compat) ---------------------------------------
# Regression tests for a real bug found deploying outside Docker: under uvloop
# (uvicorn[standard]'s default loop), a subprocess's stdin pipe is a socketpair,
# and some nftables builds (1.0.9; not Alpine's 1.1.6) refuse to read `-f -` from
# one ("Not a regular file: /dev/stdin") - so every single apply failed. `run()`
# now writes `stdin` to a temp file and swaps a trailing "-" for its path, which
# doesn't depend on what kind of fd stdin is, on any nft version, any loop.
def test_run_stdin_uses_a_real_seekable_file_not_a_pipe():
    rc, out, err = asyncio.run(engine.run(
        "python3", "-c",
        "import os,stat,sys\n"
        "st = os.stat(sys.argv[1])\n"
        "assert stat.S_ISREG(st.st_mode), 'not a regular file'\n"
        "f = open(sys.argv[1]); f.seek(0, 2); print(f.tell())",
        "-", stdin="hello world\n"))
    assert rc == 0, err
    assert out.strip() == str(len("hello world\n"))


def test_run_cleans_up_the_temp_file_after():
    rc, out, err = asyncio.run(engine.run("python3", "-c", "import sys; print(sys.argv[1])", "-", stdin="x"))
    assert rc == 0, err
    assert not os.path.exists(out.strip()), f"temp file was not removed: {out.strip()}"


@pytest.mark.skipif(not _nft_ok(), reason="needs nft + CAP_NET_ADMIN")
def test_apply_works_under_uvloop():
    """The actual regression, reproduced exactly: apply() must succeed when the
    running loop is uvloop - what both the Docker image and a native (non-Docker)
    deployment actually use, since both install uvicorn[standard]."""
    uvloop = pytest.importorskip("uvloop")

    async def go():
        await engine.apply([fwd(9001, listen_port=25999)])
        ks = await engine.kernel_state()
        assert ks.exists and ks.prerouting_rules == 1
        await engine.apply([])

    old_policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    try:
        asyncio.run(go())
    finally:
        asyncio.set_event_loop_policy(old_policy)


# ---- interface state (operstate vs. carrier fallback) -----------------------------
# Regression test for a real bug: Tailscale's interface is a TUN device, and TUN
# devices never implement carrier-change signalling, so their operstate sits at
# "unknown" forever - even fully up and passing real traffic (confirmed directly:
# a live tailscale0 reports operstate=unknown, carrier=1). Trusting operstate alone
# made the dashboard show the VPN link as down/unknown (a red status dot) the whole
# time it was actually working fine. `_iface_state` must fall back to `carrier` only
# when operstate itself has nothing to say - real NICs/veth pairs (already exercised
# via the Docker stack) report a real operstate and must be unaffected.
def _write_iface(tmp_path, name, operstate=None, carrier=None):
    d = tmp_path / name
    d.mkdir()
    if operstate is not None:
        (d / "operstate").write_text(operstate)
    if carrier is not None:
        (d / "carrier").write_text(carrier)
    return str(d)


def test_iface_state_prefers_a_real_operstate(tmp_path):
    from app.collector import Collector
    assert Collector._iface_state(_write_iface(tmp_path, "eth0", operstate="up", carrier="1")) == "up"
    assert Collector._iface_state(_write_iface(tmp_path, "eth1", operstate="down", carrier="1")) == "down"


def test_iface_state_falls_back_to_carrier_when_operstate_is_unknown(tmp_path):
    from app.collector import Collector
    assert Collector._iface_state(_write_iface(tmp_path, "tailscale0", operstate="unknown", carrier="1")) == "up"
    assert Collector._iface_state(_write_iface(tmp_path, "tun1", operstate="unknown", carrier="0")) == "down"
    assert Collector._iface_state(_write_iface(tmp_path, "tun2", operstate="unknown")) == "unknown"


# ---- pmctl: a root-only file must fail cleanly, not with a raw traceback ----------
# Regression test for a real bug found on the user's own Pi: internal.token (and
# ADMIN_PASSWORD_FILE) are intentionally 0600 root-only (so another logged-in user
# on the box can't read the admin-equivalent token) - but pmctl never told the
# caller that, so `pmctl status` run without sudo crashed with a raw
# PermissionError traceback instead of a clear message.
def test_cli_permission_error_is_reported_cleanly(monkeypatch, capsys):
    from app import cli

    def deny(*a, **k):
        raise PermissionError(13, "Permission denied", "/var/lib/portmirror/internal.token")
    monkeypatch.setattr(cli.config, "INTERNAL_TOKEN_FILE", type("P", (), {"read_text": deny})())
    rc = cli.main(["status"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "permission denied" in err.lower() and "sudo" in err.lower()
    assert "Traceback" not in err


# ---- connection log (real traffic, separate from the audit/admin-action log) -------
def test_connection_log_open_close_and_query(store):
    store.record_connection_opens([(1, "tcp", "192.168.88.67", 5000, "10.0.0.12", 80, "2026-01-01T00:00:00+00:00")])
    rows = store.connection_log(forward_id=1)
    assert len(rows) == 1 and rows[0]["ended_at"] is None and rows[0]["bytes_in"] == 0

    store.record_connection_closes([(300, 5000, 4, 5, "2026-01-01T00:00:05+00:00",
                                     1, "tcp", "192.168.88.67", 5000, "2026-01-01T00:00:00+00:00")])
    rows = store.connection_log(forward_id=1)
    assert rows[0]["ended_at"] == "2026-01-01T00:00:05+00:00"
    assert (rows[0]["bytes_in"], rows[0]["bytes_out"]) == (300, 5000)


def test_connection_log_filters_by_client_and_forward(store):
    store.record_connection_opens([
        (1, "tcp", "192.168.88.67", 1, "10.0.0.12", 80, "2026-01-01T00:00:00+00:00"),
        (2, "tcp", "192.168.88.23", 2, "10.0.0.13", 443, "2026-01-01T00:00:00+00:00"),
    ])
    assert len(store.connection_log(forward_id=1)) == 1
    assert len(store.connection_log(client_ip="192.168.88.23")) == 1
    assert len(store.connection_log()) == 2


def test_close_orphaned_connections_only_touches_open_rows(store):
    store.record_connection_opens([(1, "tcp", "192.168.88.67", 1, "10.0.0.12", 80, "2026-01-01T00:00:00+00:00")])
    store.record_connection_opens([(1, "tcp", "192.168.88.67", 2, "10.0.0.12", 80, "2026-01-01T00:00:00+00:00")])
    store.record_connection_closes([(10, 20, 1, 1, "2026-01-01T00:00:01+00:00",
                                     1, "tcp", "192.168.88.67", 1, "2026-01-01T00:00:00+00:00")])
    n = store.close_orphaned_connections("2026-01-01T00:05:00+00:00")
    assert n == 1   # only the still-open one (sport=2); the already-closed one is untouched
    rows = {r["client_port"]: r for r in store.connection_log(forward_id=1)}
    assert rows[1]["ended_at"] == "2026-01-01T00:00:01+00:00"   # unchanged
    assert rows[2]["ended_at"] == "2026-01-01T00:05:00+00:00"   # closed by the orphan sweep


def test_connection_log_summary_groups_and_sorts_by_total_bytes(store):
    store.record_connection_opens([
        (1, "tcp", "192.168.88.67", 1, "10.0.0.12", 80, "2026-01-01T00:00:00+00:00"),
        (1, "tcp", "192.168.88.67", 2, "10.0.0.12", 80, "2026-01-01T00:00:00+00:00"),
        (1, "tcp", "192.168.88.23", 3, "10.0.0.12", 80, "2026-01-01T00:00:00+00:00"),
    ])
    store.record_connection_closes([
        (100, 100, 1, 1, "t", 1, "tcp", "192.168.88.67", 1, "2026-01-01T00:00:00+00:00"),
        (100, 100, 1, 1, "t", 1, "tcp", "192.168.88.67", 2, "2026-01-01T00:00:00+00:00"),
        (10000, 10000, 1, 1, "t", 1, "tcp", "192.168.88.23", 3, "2026-01-01T00:00:00+00:00"),
    ])
    by_client = store.connection_log_summary(group_by="client")
    assert by_client[0]["key"] == "192.168.88.23" and by_client[0]["sessions"] == 1   # most bytes first
    assert by_client[1]["key"] == "192.168.88.67" and by_client[1]["sessions"] == 2


def test_purge_connection_log_keeps_open_rows_regardless_of_age(store):
    store.record_connection_opens([(1, "tcp", "1.2.3.4", 1, "10.0.0.12", 80, "2020-01-01T00:00:00+00:00")])
    assert store.purge_connection_log("2026-01-01T00:00:00+00:00") == 0   # still open - never purged
    store.record_connection_closes([(1, 1, 1, 1, "2020-01-01T00:00:01+00:00",
                                     1, "tcp", "1.2.3.4", 1, "2020-01-01T00:00:00+00:00")])
    assert store.purge_connection_log("2026-01-01T00:00:00+00:00") == 1
    assert store.connection_log() == []


def test_connection_log_retention_defaults_to_30_days_not_forever(monkeypatch):
    monkeypatch.delenv("PM_CONNECTION_LOG_RETENTION_DAYS", raising=False)
    import importlib
    from app import config as config_module
    importlib.reload(config_module)
    assert config_module.CONNECTION_LOG_RETENTION_S == 30 * 86400
    monkeypatch.setenv("PM_CONNECTION_LOG_RETENTION_DAYS", "0")
    importlib.reload(config_module)
    assert config_module.CONNECTION_LOG_RETENTION_S is None
    monkeypatch.delenv("PM_CONNECTION_LOG_RETENTION_DAYS", raising=False)
    importlib.reload(config_module)   # restore for any later test relying on module state


# ---- collector: connection-log tracking (open on first sight, close on disappearance) ----
def _flow(proto="tcp", src="192.168.88.67", sport=5000, reply_src="10.0.0.12", reply_sport=80,
         bytes_in=0, bytes_out=0, pkts_in=0, pkts_out=0, state="ESTABLISHED"):
    return engine.Flow(proto=proto, state=state, ttl=60, src=src, dst="192.168.88.8", sport=sport,
                       dport=80, reply_src=reply_src, reply_sport=reply_sport,
                       pkts_in=pkts_in, bytes_in=bytes_in, pkts_out=pkts_out, bytes_out=bytes_out)


def test_collector_tracks_a_connection_from_open_to_close(store):
    from app.collector import Collector
    c = Collector(store, lambda: [], type("P", (), {"status": {}})())

    c._track_connection_log({1: [_flow(bytes_in=100, bytes_out=200)]}, time.time())
    rows = store.connection_log(forward_id=1)
    assert len(rows) == 1 and rows[0]["ended_at"] is None

    c._track_connection_log({1: [_flow(bytes_in=500, bytes_out=900)]}, time.time())  # still open, bytes growing
    assert len(store.connection_log(forward_id=1)) == 1   # no duplicate row

    c._track_connection_log({}, time.time())   # the flow is gone - conntrack entry expired
    rows = store.connection_log(forward_id=1)
    assert rows[0]["ended_at"] is not None
    assert (rows[0]["bytes_in"], rows[0]["bytes_out"]) == (500, 900)   # the last values seen before it closed


def test_collector_tracks_two_concurrent_clients_independently(store):
    from app.collector import Collector
    c = Collector(store, lambda: [], type("P", (), {"status": {}})())
    c._track_connection_log({1: [_flow(src="192.168.88.67", sport=1), _flow(src="192.168.88.23", sport=2)]}, time.time())
    assert len(store.connection_log(forward_id=1)) == 2
    c._track_connection_log({1: [_flow(src="192.168.88.67", sport=1)]}, time.time())   # only .23 closed
    rows = {r["client_ip"]: r for r in store.connection_log(forward_id=1)}
    assert rows["192.168.88.67"]["ended_at"] is None
    assert rows["192.168.88.23"]["ended_at"] is not None


def test_collector_closes_orphaned_sessions_on_startup(store):
    from app.collector import Collector
    store.record_connection_opens([(1, "tcp", "1.2.3.4", 1, "10.0.0.12", 80, "2020-01-01T00:00:00+00:00")])
    Collector(store, lambda: [], type("P", (), {"status": {}})())   # constructing it runs the sweep
    rows = store.connection_log(forward_id=1)
    assert rows[0]["ended_at"] is not None


def test_collector_closes_session_on_time_wait_not_waiting_for_conntrack_removal(store):
    """Regression test: a finished TCP connection lingers in conntrack through TIME_WAIT for
    minutes after the real transfer is over. The session must close the moment the flow leaves
    the live states (matching the dashboard's own is_live()), not whenever conntrack eventually
    forgets about it - and the TIME_WAIT entry's own byte counts (still accurate at that point)
    must be used, not stale ones from the last live observation."""
    from app.collector import Collector
    c = Collector(store, lambda: [], type("P", (), {"status": {}})())

    c._track_connection_log({1: [_flow(state="ESTABLISHED", bytes_in=100, bytes_out=200)]}, time.time())
    assert store.connection_log(forward_id=1)[0]["ended_at"] is None

    # still present in conntrack, but TIME_WAIT now - and conntrack recorded a bit more data first
    c._track_connection_log({1: [_flow(state="TIME_WAIT", bytes_in=150, bytes_out=250)]}, time.time())
    rows = store.connection_log(forward_id=1)
    assert rows[0]["ended_at"] is not None, "session must close as soon as the flow leaves the live states"
    assert (rows[0]["bytes_in"], rows[0]["bytes_out"]) == (150, 250), "must use TIME_WAIT's own accurate counts"


def test_collector_logs_a_connection_that_finishes_within_one_tick(store):
    """Regression test: on a fast LAN, a short request can go from not-existing to ESTABLISHED to
    TIME_WAIT entirely between two 1s polls, so the very first (and only) observation of it is
    already non-live. It must still be logged - opened and closed in that same tick, using its own
    already-final byte counts - rather than being silently dropped because it was never seen live."""
    from app.collector import Collector
    c = Collector(store, lambda: [], type("P", (), {"status": {}})())

    c._track_connection_log({1: [_flow(state="TIME_WAIT", bytes_in=80, bytes_out=120)]}, time.time())
    rows = store.connection_log(forward_id=1)
    assert len(rows) == 1, "a flow first seen already past-live must still produce a row"
    assert rows[0]["ended_at"] is not None, "it must be closed immediately, not left open"
    assert (rows[0]["bytes_in"], rows[0]["bytes_out"]) == (80, 120)


def test_collector_does_not_relog_a_flow_lingering_in_time_wait(store):
    """Regression test: after a connection closes, its conntrack entry lingers in TIME_WAIT for a
    while. Without remembering that it was already logged, the same flow would look "new" again on
    every following tick for as long as it lingers, producing a duplicate open+close row each second."""
    from app.collector import Collector
    c = Collector(store, lambda: [], type("P", (), {"status": {}})())

    c._track_connection_log({1: [_flow(state="TIME_WAIT", bytes_in=80, bytes_out=120)]}, time.time())
    assert len(store.connection_log(forward_id=1)) == 1

    # same flow, still lingering in TIME_WAIT a second later - must not produce another row
    c._track_connection_log({1: [_flow(state="TIME_WAIT", bytes_in=80, bytes_out=120)]}, time.time())
    c._track_connection_log({1: [_flow(state="TIME_WAIT", bytes_in=80, bytes_out=120)]}, time.time())
    assert len(store.connection_log(forward_id=1)) == 1, "a lingering TIME_WAIT entry must not be logged twice"

    # once it actually vanishes from conntrack, a later flow reusing the same client port is new
    c._track_connection_log({}, time.time())
    c._track_connection_log({1: [_flow(state="ESTABLISHED", bytes_in=10, bytes_out=20)]}, time.time())
    assert len(store.connection_log(forward_id=1)) == 2, "a genuinely new connection must still be tracked"
