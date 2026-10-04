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


# ---- device names -----------------------------------------------------------------
def test_device_names_set_list_rename_delete(store):
    assert store.device_names() == {}
    store.set_device_name("192.168.88.67", "Dad's laptop")
    store.set_device_name("192.168.88.23", "Kitchen tablet")
    assert store.device_names() == {"192.168.88.67": "Dad's laptop", "192.168.88.23": "Kitchen tablet"}

    store.set_device_name("192.168.88.67", "Dad's new laptop")   # rename: same IP, new name
    assert store.device_names()["192.168.88.67"] == "Dad's new laptop"
    assert len(store.device_names()) == 2, "renaming must update in place, not add a second row"

    assert store.delete_device_name("192.168.88.23") is True
    assert "192.168.88.23" not in store.device_names()
    assert store.delete_device_name("192.168.88.23") is False, "deleting an already-gone name reports nothing to delete"


# ---- notifications ------------------------------------------------------------------
class _FakeNotifier:
    def __init__(self):
        self.calls = []

    def notify(self, type_, severity, message, context=None):
        self.calls.append((type_, severity, message, context))


def test_notification_crud_and_state_transitions(store):
    assert store.notifications() == [] and store.unread_notification_count() == 0
    store.add_notification("target_down", "warning", "X is down", {"forward_id": 1})
    assert store.unread_notification_count() == 1
    row = store.notifications()[0]
    assert row["context"] == {"forward_id": 1} and row["state"] == "unread"

    nid = row["id"]
    assert store.set_notification_state(nid, "actioned") is True
    assert store.unread_notification_count() == 0
    assert store.notifications(state="actioned")[0]["id"] == nid
    assert store.set_notification_state(999999, "read") is False, "a nonexistent id reports nothing changed"


def test_mark_all_notifications_read(store):
    store.add_notification("target_down", "warning", "a")
    store.add_notification("target_up", "info", "b")
    assert store.unread_notification_count() == 2
    store.mark_all_notifications_read()
    assert store.unread_notification_count() == 0


def test_muted_type_produces_no_new_notification(store):
    store.mute_type("target_down")
    assert store.muted_types() == ["target_down"]
    store.add_notification("target_down", "warning", "should be dropped")
    assert store.notifications() == [], "a muted type must not even create a row"
    store.add_notification("target_up", "info", "a different type, unaffected by the mute")
    assert len(store.notifications()) == 1

    store.unmute_type("target_down")
    store.add_notification("target_down", "warning", "unmuted again")
    assert len(store.notifications()) == 2


def test_health_flip_only_on_a_real_change():
    from app.prober import health_flip
    up, down = {"state": "up"}, {"state": "down"}
    assert health_flip(None, up) is None, "the first-ever observation must never be treated as a flip"
    assert health_flip(None, down) is None
    assert health_flip(up, up) is None, "no change -> no flip"
    assert health_flip(up, down) == ("target_down", "warning")
    assert health_flip(down, up) == ("target_up", "info")


def test_prober_probe_notifies_through_real_wiring_only_on_flips(monkeypatch):
    """Exercises Prober.probe() itself end to end (not just the pure health_flip() helper), with the
    actual TCP connect stubbed out so the result - and so which tick is a "flip" - is deterministic."""
    import asyncio
    from app import prober as prober_mod

    outcomes = iter([True, True, False, False])   # up, up (no flip), down (flip), down (no flip)

    async def fake_open_connection(*a, **kw):
        if next(outcomes):
            class W:
                def close(self):
                    pass
            return None, W()
        raise ConnectionRefusedError

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
    notifier = _FakeNotifier()
    f = fwd(1)
    p = prober_mod.Prober(lambda: [f], notifier)

    asyncio.run(p.probe(f))   # 1st: up, prev is None -> no notification ever on first sight
    assert notifier.calls == []
    asyncio.run(p.probe(f))   # 2nd: still up -> no flip
    assert notifier.calls == []
    asyncio.run(p.probe(f))   # 3rd: down -> a real flip
    assert len(notifier.calls) == 1 and notifier.calls[0][0] == "target_down"
    asyncio.run(p.probe(f))   # 4th: still down -> no second notification
    assert len(notifier.calls) == 1


def test_login_notifies_once_at_the_failure_threshold_not_before_or_repeatedly(store):
    from app.auth import Auth
    from fastapi import HTTPException
    notifier = _FakeNotifier()
    a = Auth(store, notifier)
    for i in range(5):
        try:
            a.login("admin", "wrong", "10.0.0.5")
        except HTTPException:
            pass
    assert notifier.calls == [("login_failures", "error", "Repeated failed logins from 10.0.0.5",
                               {"ip": "10.0.0.5"})], "must fire exactly once, at the 3rd failure, not on every one"


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
    # Use a scratch table: this test must never touch the live `ip pm` table, which carries real forwards.
    # (Applying [] to `pm` here used to clear every forward for up to 5 s on each test run.)
    live_table, engine.T = engine.T, "pmtest_unit"

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
        engine.T = live_table
        subprocess.run(["nft", "delete", "table", "ip", "pmtest_unit"], capture_output=True)


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
    """A row left open by a process that stopped is closed by the first tick if its connection is gone."""
    from app.collector import Collector
    store.record_connection_opens([(1, "tcp", "1.2.3.4", 1, "10.0.0.12", 80, "2020-01-01T00:00:00+00:00")])
    c = Collector(store, lambda: [], type("P", (), {"status": {}})())
    assert store.connection_log(forward_id=1)[0]["ended_at"] is None   # not closed blindly at startup any more
    c._track_connection_log({}, time.time())                          # first tick: that connection isn't there
    rows = store.connection_log(forward_id=1)
    assert rows[0]["ended_at"] is not None


def test_collector_continues_a_live_session_across_a_restart(store):
    """Regression test: after a manager restart, a connection still live in conntrack must keep its
    existing row and start time. Previously every restart closed the row and opened a second one for
    the same session (seen on the Pi as RDP and Jellyfin sessions splitting at each manager start)."""
    from app.collector import Collector
    c = Collector(store, lambda: [], type("P", (), {"status": {}})())
    c._track_connection_log({1: [_flow(state="ESTABLISHED", bytes_in=100, bytes_out=200)]}, time.time())
    original = store.connection_log(forward_id=1)[0]

    restarted = Collector(store, lambda: [], type("P", (), {"status": {}})())   # a fresh process, same database
    restarted._track_connection_log({1: [_flow(state="ESTABLISHED", bytes_in=400, bytes_out=800)]}, time.time())
    rows = store.connection_log(forward_id=1)
    assert len(rows) == 1, "the same live session must not be split into a second row"
    assert rows[0]["ended_at"] is None and rows[0]["started_at"] == original["started_at"]

    restarted._track_connection_log({}, time.time())   # now it really ends
    rows = store.connection_log(forward_id=1)
    assert rows[0]["ended_at"] is not None and (rows[0]["bytes_in"], rows[0]["bytes_out"]) == (400, 800)


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


# ---- diag: threshold functions (pure, no kernel/process access needed) -----------
def test_conntrack_status_thresholds():
    from app import diag
    assert diag.conntrack_status(0, 0) == ("skip", "not reported by this kernel")
    assert diag.conntrack_status(100, 10000)[0] == "ok"
    assert diag.conntrack_status(8000, 10000)[0] == "warn"     # 80%
    assert diag.conntrack_status(9500, 10000)[0] == "fail"     # 95%
    assert diag.conntrack_status(7499, 10000)[0] == "ok"       # just under the 75% warn line
    assert diag.conntrack_status(7500, 10000)[0] == "warn"     # exactly on the warn line


def test_disk_status_thresholds():
    from app import diag
    assert diag.disk_status(0, 0) == ("skip", "not available")
    assert diag.disk_status(50, 100)[0] == "ok"        # 50% free
    assert diag.disk_status(10, 100)[0] == "warn"      # 10% free
    assert diag.disk_status(3, 100)[0] == "fail"       # 3% free


def test_memory_status_thresholds():
    from app import diag
    assert diag.memory_status(0, 0) == ("skip", "no limit reported")
    assert diag.memory_status(50, 100)[0] == "ok"      # 50% used
    assert diag.memory_status(90, 100)[0] == "warn"    # 90% used
    assert diag.memory_status(99, 100)[0] == "fail"    # 99% used


def test_iface_status():
    from app import diag
    assert diag.iface_status("up") == ("ok", "up")
    assert diag.iface_status("missing")[0] == "fail"
    assert diag.iface_status("down")[0] == "fail"
    assert diag.iface_status("unknown")[0] == "fail"   # no silent pass on an unreadable state


def test_vpn_route_status_requires_the_vpn_interface():
    from app import diag
    good = "10.0.0.1 dev vpn0 src 10.0.0.88 uid 0"
    assert diag.vpn_route_status(0, good, "", "vpn0")[0] == "ok"
    # vpn0 down: the default route catches the VPN network via LAN - must not pass
    via_lan = "10.0.0.1 via 192.168.88.1 dev lan0 src 192.168.88.8 uid 0"
    status, detail = diag.vpn_route_status(0, via_lan, "", "vpn0")
    assert status == "fail" and "not vpn0" in detail
    status, detail = diag.vpn_route_status(1, "", "RTNETLINK answers: Network is unreachable", "vpn0")
    assert status == "fail" and "unreachable" in detail


def test_rules_match_status_flags_any_mismatch():
    from app import diag
    assert diag.rules_match_status(14, 14)[0] == "ok"
    assert diag.rules_match_status(0, 14)[0] == "fail"       # table flushed behind our back
    assert diag.rules_match_status(13, 14)[0] == "fail"      # one rule lost
    assert diag.rules_match_status(15, 14)[0] == "fail"      # stray rule added
    assert "kernel has 0" in diag.rules_match_status(0, 14)[1]


def test_target_health_status():
    from app import diag
    assert diag.target_health_status([]) == ("skip", "no enabled forwards to probe")
    assert diag.target_health_status([None, None]) == ("skip", "no enabled forwards to probe")
    assert diag.target_health_status([{"state": "up"}, {"state": "up"}])[0] == "ok"
    status, detail = diag.target_health_status([{"state": "up"}, {"state": "down"}])
    assert status == "fail" and "1/2" in detail


def test_diag_run_produces_every_expected_check(store):
    """Integration-ish: run() against a real (if minimal) manager/collector/prober, not mocks for
    every field - confirms the check list is complete and every check returns a well-formed result,
    without needing a real kernel table or real interfaces to exist in this test environment."""
    import asyncio
    from app import diag
    from app.collector import Collector
    from app.prober import Prober
    from app.service import Manager

    manager = Manager(store)
    prober = Prober(manager.forwards)
    collector = Collector(store, manager.forwards, prober)

    checks = asyncio.run(diag.run(manager, collector, prober))
    ids = {c["id"] for c in checks}
    assert ids == {"nft_syntax", "kernel_table", "kernel_matches", "vpn_route", "lan_iface", "vpn_iface",
                   "conntrack", "conntrack_acct", "disk", "memory", "target_health", "restarts"}
    for c in checks:
        assert c["status"] in ("ok", "warn", "fail", "skip"), c
        assert c["label"] and c["detail"]


# ---- access windows ----------------------------------------------------------------
def _at(weekday_mon0: int, hh: int, mm: int = 0) -> datetime:
    base = datetime(2026, 10, 5, tzinfo=None)   # a Monday
    return base + timedelta(days=weekday_mon0, hours=hh, minutes=mm)


def test_access_window_same_day_range():
    from app.models import AccessWindow
    w = AccessWindow(days=[0, 1, 2, 3, 4], start="09:00", end="22:00")
    assert w.contains(_at(0, 9, 0)) is True          # start is inclusive
    assert w.contains(_at(0, 21, 59)) is True
    assert w.contains(_at(0, 22, 0)) is False        # end is exclusive
    assert w.contains(_at(0, 8, 59)) is False
    assert w.contains(_at(5, 12, 0)) is False        # Saturday is not in the day list


def test_access_window_wraps_midnight_and_attributes_early_hours_to_yesterday():
    from app.models import AccessWindow
    w = AccessWindow(days=[4], start="22:00", end="06:00")   # Friday night into Saturday morning
    assert w.contains(_at(4, 23, 0)) is True      # Friday 23:00 - the window's own day
    assert w.contains(_at(5, 3, 0)) is True       # Saturday 03:00 - still the Friday window
    assert w.contains(_at(5, 7, 0)) is False      # Saturday 07:00 - past the end
    assert w.contains(_at(6, 3, 0)) is False      # Sunday 03:00 - Saturday isn't in the day list


def test_access_window_validation():
    from app.models import AccessWindow
    with pytest.raises(ValidationError):
        AccessWindow(days=[], start="09:00", end="10:00")
    with pytest.raises(ValidationError):
        AccessWindow(days=[7], start="09:00", end="10:00")
    with pytest.raises(ValidationError):
        AccessWindow(days=[0], start="25:00", end="10:00")
    with pytest.raises(ValidationError):
        AccessWindow(days=[0], start="09:00", end="09:00")


def test_forward_round_trips_its_access_window_through_the_store(store):
    from app.models import AccessWindow
    f = store.create(ForwardIn(name="w", listen_port=7777, target_ip="10.0.0.12", target_port=80,
                               access_window=AccessWindow(days=[0, 6], start="09:00", end="22:00")))
    got = store.forward(f.id)
    assert got.access_window is not None and got.access_window.days == [0, 6]
    plain = store.create(ForwardIn(name="p", listen_port=7778, target_ip="10.0.0.12", target_port=80))
    assert store.forward(plain.id).access_window is None


# ---- CSV export --------------------------------------------------------------------
def test_csv_neutralises_formula_injection_but_not_numbers():
    from app import export
    assert export.cell("=HYPERLINK(\"http://x\")").startswith("'=")
    assert export.cell("+1+1").startswith("'+")
    assert export.cell("-2").startswith("'-")          # a text cell that begins with '-' is still neutralised
    assert export.cell("@SUM(A1)").startswith("'@")
    assert export.cell(-2) == "-2"                     # a real negative number is left alone
    assert export.cell(0.5) == "0.5"
    assert export.cell("plain") == "plain"


def test_csv_quotes_commas_quotes_and_newlines_correctly():
    import csv, io
    from app import export
    text = export.to_csv(["a", "b"], [["one, two", "say \"hi\""], ["line1\nline2", None]])
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["a", "b"]
    assert rows[1] == ["one, two", "say \"hi\""]
    assert rows[2] == ["line1\nline2", ""]


def test_audit_csv_serialises_structured_detail():
    from app import export
    text = export.audit_csv([{"ts": "2026-10-03T00:00:00+00:00", "actor": "admin", "action": "forward.update",
                              "target": "#1 x", "detail": {"before": {"rate_limit": 5}, "after": {"rate_limit": 9}}}])
    lines = text.strip().split("\n")
    assert lines[0] == "time_utc,actor,action,target,detail"
    assert "rate_limit" in lines[1] and lines[1].startswith("2026-10-03T00:00:00+00:00,admin,")


def test_history_csv_uses_iso_times():
    from app import export
    text = export.history_csv([{"t": 0, "new_per_s": 1.5, "in_bps": 800, "out_bps": 0, "live": 2}], 300)
    assert text.splitlines()[1].startswith("1970-01-01T00:00:00+00:00,300,")


# ---- data quotas --------------------------------------------------------------------
def test_quota_period_starts():
    from app import quota
    now = datetime(2026, 10, 7, 15, 30)   # a Wednesday
    assert quota.period_start(now, "day") == datetime(2026, 10, 7, 0, 0)
    assert quota.period_start(now, "week") == datetime(2026, 10, 5, 0, 0)   # Monday
    assert quota.period_start(now, "month") == datetime(2026, 10, 1, 0, 0)
    monday = datetime(2026, 10, 5, 0, 30)
    assert quota.period_start(monday, "week") == datetime(2026, 10, 5, 0, 0)


def test_quota_action_only_when_it_changes_something():
    from app import quota
    assert quota.quota_action(usage=100, limit=100, enabled=True, disabled_by_quota=False) == "disable"
    assert quota.quota_action(usage=99, limit=100, enabled=True, disabled_by_quota=False) is None
    assert quota.quota_action(usage=500, limit=100, enabled=False, disabled_by_quota=True) is None     # already off
    # back under the limit (period rolled over / quota raised): re-enable only what the quota switched off
    assert quota.quota_action(usage=10, limit=100, enabled=False, disabled_by_quota=True) == "enable"
    assert quota.quota_action(usage=10, limit=100, enabled=False, disabled_by_quota=False) is None, \
        "a forward the user disabled by hand must not be switched back on by a quota reset"


def test_quota_model_validation():
    from app.models import Quota
    assert Quota(bytes=1, period="day").period == "day"
    with pytest.raises(ValidationError):
        Quota(bytes=0, period="day")
    with pytest.raises(ValidationError):
        Quota(bytes=10, period="year")


def test_store_usage_sums_bytes_in_and_out_since_a_cutoff(store):
    f = store.create(ForwardIn(name="q", listen_port=7600, target_ip="10.0.0.12", target_port=80))
    store.write_rollups([(1000, f.id, 1, 100, 200, 1, 1, 0), (2000, f.id, 1, 300, 400, 1, 1, 0),
                         (3000, f.id, 1, 9999, 9999, 1, 1, 0)])
    assert store.usage_since(f.id, 1500) == (300 + 400) + (9999 + 9999)
    assert store.usage_since(f.id, 0) == 100 + 200 + 300 + 400 + 9999 + 9999
    assert store.usage_since(f.id, 99999) == 0


def test_collector_reports_current_minute_bytes_without_creating_state(store):
    from app.collector import Collector
    c = Collector(store, lambda: [], type("P", (), {"status": {}})())
    assert c.current_minute_bytes(9) == 0 and 9 not in c.acc, "reading must not create an accumulator"
    c.acc[9][1] = 300
    c.acc[9][2] = 450
    assert c.current_minute_bytes(9) == 750



# ---- HTTPS switch: confirm within a time limit or revert ------------------------------------
def test_tls_switch_is_pending_and_remembers_the_previous_scheme():
    from datetime import datetime, timedelta, timezone
    from app import tls
    t0 = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    s = tls.begin_switch({"enabled": False, "pending": None}, True, t0)
    assert s["enabled"] is True
    assert s["pending"]["previous"] is False
    assert datetime.fromisoformat(s["pending"]["deadline"]) == t0 + timedelta(seconds=60)
    assert tls.seconds_left(s, t0 + timedelta(seconds=20)) == 40


def test_tls_confirm_keeps_the_new_scheme_and_clears_pending():
    from datetime import datetime, timezone
    from app import tls
    t0 = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    s = tls.confirm(tls.begin_switch({"enabled": False, "pending": None}, True, t0))
    assert s == {"enabled": True, "pending": None}
    assert tls.overdue(s, t0 + __import__("datetime").timedelta(hours=1)) is False


def test_tls_unconfirmed_switch_reverts_only_after_the_limit():
    from datetime import datetime, timedelta, timezone
    from app import tls
    t0 = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    s = tls.begin_switch({"enabled": False, "pending": None}, True, t0)
    assert tls.overdue(s, t0 + timedelta(seconds=59)) is False      # still within the minute
    assert tls.overdue(s, t0 + timedelta(seconds=60)) is True       # the limit is reached
    assert tls.revert(s) == {"enabled": False, "pending": None}     # back to what it was


def test_tls_revert_of_an_https_to_http_change_goes_back_to_https():
    from datetime import datetime, timezone
    from app import tls
    t0 = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    s = tls.begin_switch({"enabled": True, "pending": None}, False, t0)
    assert tls.revert(s) == {"enabled": True, "pending": None}


def test_tls_flags_need_both_enabled_and_a_stored_certificate(tmp_path, monkeypatch):
    from app import tls
    monkeypatch.setattr(tls, "TLS_DIR", tmp_path)
    monkeypatch.setattr(tls, "CERT_FILE", tmp_path / "cert.pem")
    monkeypatch.setattr(tls, "KEY_FILE", tmp_path / "key.pem")
    assert tls.uvicorn_flags({"enabled": True, "pending": None}) == []   # enabled but no certificate yet
    (tmp_path / "cert.pem").write_text("x"); (tmp_path / "key.pem").write_text("x")
    assert tls.uvicorn_flags({"enabled": True, "pending": None}) == [
        "--ssl-certfile", str(tmp_path / "cert.pem"), "--ssl-keyfile", str(tmp_path / "key.pem")]
    assert tls.uvicorn_flags({"enabled": False, "pending": None}) == []


def test_tls_rejects_non_pem_and_mismatched_pairs(tmp_path):
    import shutil, subprocess
    import pytest
    from app import tls
    with pytest.raises(ValueError):
        tls.validate_pair("not a cert", "not a key")
    if not shutil.which("openssl"):
        pytest.skip("needs openssl to make a certificate pair")
    def make(name):
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                        "-keyout", str(tmp_path / f"{name}.key"), "-out", str(tmp_path / f"{name}.pem"),
                        "-subj", "/CN=test"], check=True, capture_output=True)
        return (tmp_path / f"{name}.pem").read_text(), (tmp_path / f"{name}.key").read_text()
    cert_a, key_a = make("a")
    cert_b, key_b = make("b")
    tls.validate_pair(cert_a, key_a)                       # a matching pair loads
    with pytest.raises(ValueError):
        tls.validate_pair(cert_a, key_b)                   # the key belongs to a different certificate


# ---- dashboard access over the VPN side ------------------------------------------------------------
def test_access_rules_block_only_when_disallowed():
    from app import access
    blocked = access.rules_script(False, "tailscale0", 8088)
    assert "iifname \"tailscale0\" tcp dport 8088 counter drop" in blocked
    assert blocked.startswith("add table inet pm_ui\ndelete table inet pm_ui\n")   # idempotent on any state
    allowed = access.rules_script(True, "tailscale0", 8088)
    assert "drop" not in allowed and "chain" not in allowed                       # allowed = no rule at all
    assert allowed.startswith("add table inet pm_ui\ndelete table inet pm_ui\n")


def test_access_setting_defaults_to_allowed_and_round_trips(tmp_path):
    from app import access
    p = tmp_path / "access.json"
    assert access.load(p) == {"ui_over_vpn": True}                  # default: option 2, reachable over the VPN
    access.save({"ui_over_vpn": False}, p)
    assert access.load(p) == {"ui_over_vpn": False}


def test_acct_status_warns_when_byte_counters_are_off():
    from app import diag
    assert diag.acct_status("1\n")[0] == "ok"
    status, detail = diag.acct_status("0\n")
    assert status == "warn" and "sysctl -w net.netfilter.nf_conntrack_acct=1" in detail
    assert diag.acct_status(None)[0] == "skip"


def test_connections_csv_rows_and_open_status():
    from app import export
    rows = [
        {"fid": 1, "started_at": "2026-10-03T10:00:00+00:00", "ended_at": None, "proto": "tcp",
         "client_ip": "192.168.88.9", "client_port": 65451, "target_ip": "192.168.0.112", "target_port": 3389,
         "bytes_in": 0, "bytes_out": 0, "pkts_in": 0, "pkts_out": 0},
        {"fid": 2, "started_at": "2026-10-03T09:00:00+00:00", "ended_at": "2026-10-03T09:05:00+00:00",
         "proto": "udp", "client_ip": "192.168.88.7", "client_port": 5000, "target_ip": "10.0.0.14", "target_port": 9000,
         "bytes_in": 120, "bytes_out": 300, "pkts_in": 2, "pkts_out": 3},
    ]
    text = export.connections_csv(rows, {1: "=RDP 112", 2: "DNS"})
    lines = text.splitlines()
    assert lines[0] == "started_utc,ended_utc,status,forward,protocol,client_ip,client_port,target_ip,target_port,bytes_in,bytes_out,packets_in,packets_out"
    assert lines[1].startswith("2026-10-03T10:00:00+00:00,,open,'=RDP 112,tcp,192.168.88.9,65451,")   # open: empty end; formula neutralised
    assert lines[2] == "2026-10-03T09:00:00+00:00,2026-10-03T09:05:00+00:00,closed,DNS,udp,192.168.88.7,5000,10.0.0.14,9000,120,300,2,3"
