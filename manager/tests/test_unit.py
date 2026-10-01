"""Unit tests. Run inside the gateway image: `docker exec pm-E sh -c 'cd /opt/pm && pytest -q tests'`.
The nft -c checks need CAP_NET_ADMIN (they only dry-run against the kernel, nothing is changed)."""
import asyncio
import os
import shutil
import subprocess
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
