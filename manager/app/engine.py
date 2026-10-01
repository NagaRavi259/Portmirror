"""
nftables engine: forwards (DB) -> one atomic nft transaction on table `ip pm`.

Kernel layout owned by the manager:

  table ip pm
    counter f<id>_new / f<id>_in / f<id>_out [/ f<id>_rl / f<id>_cl]   (kept for every forward, even disabled)
    set     f<id>_src                        allowed source CIDRs      (enabled forwards)
    chain   prerouting  nat hook, dstnat     rate/conn-limit drops + DNAT, one group per forward+proto
    chain   forward     filter hook, filter-1   vmap {proto . original dport : jump f<id>}
    chain   f<id>       ct direction original -> f<id>_in ; reply -> f<id>_out
    set     kill4       killed flows (client . proto . sport . listen port, 60s timeout): their next
                        packet gets a TCP reset / is dropped, so a kill really ends the connection
                        even while the forward stays enabled (deleting conntrack alone doesn't:
                        the next packet would be re-DNAT'd and the flow would resume)

Counter objects are named and live outside the chains, so flushing and
re-adding rules in a transaction never resets them, and never touches
existing conntrack entries (live connections keep flowing).
"""
import asyncio
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import config
from .models import Forward

log = logging.getLogger("pm.engine")
T = config.TABLE
KILL_KEY = "ct original ip saddr . meta l4proto . ct original proto-src . ct original proto-dst"


class NftError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# pure generation
# --------------------------------------------------------------------------
def counter_names(f: Forward) -> list[str]:
    names = [f"f{f.id}_new", f"f{f.id}_in", f"f{f.id}_out"]
    if f.rate_limit:
        names.append(f"f{f.id}_rl")
    if f.max_conns:
        names.append(f"f{f.id}_cl")
    return names


def _ports(f: Forward) -> str:
    return str(f.listen_port) if not f.listen_port_end else f"{f.listen_port}-{f.listen_port_end}"


def _dnat(f: Forward, proto: str) -> str:
    if not f.listen_port_end:
        return f"dnat to {f.target_ip}:{f.target_port}"
    if f.target_port == f.listen_port:
        return f"dnat to {f.target_ip}"          # identity range: port is preserved
    pairs = ", ".join(f"{p} : {f.target_port + i}" for i, p in enumerate(f.listen_ports))
    return f"dnat to {f.target_ip} : {proto} dport map {{ {pairs} }}"


def prerouting_rules(f: Forward) -> list[str]:
    rules = []
    for proto in f.protocols:
        m = f'iifname "{config.LAN_IF}" ip saddr @f{f.id}_src {proto} dport {_ports(f)}'
        if f.rate_limit:
            rules.append(f'{m} limit rate over {f.rate_limit}/second counter name "f{f.id}_rl" drop')
        if f.max_conns:
            rules.append(f'{m} ct count over {f.max_conns} counter name "f{f.id}_cl" drop')
        rules.append(f'{m} counter name "f{f.id}_new" {_dnat(f, proto)}')
    return rules


def vmap_elements(forwards: Iterable[Forward]) -> list[str]:
    return [f"{proto} . {_ports(f)} : jump f{f.id}" for f in forwards for proto in f.protocols]


@dataclass
class KernelState:
    exists: bool = False
    chains: set = field(default_factory=set)
    sets: set = field(default_factory=set)
    counters: set = field(default_factory=set)
    prerouting_rules: int = 0


def active(forwards: Iterable[Forward]) -> list[Forward]:
    return [f for f in forwards if f.enabled and not f.expired()]


def build_script(forwards: list[Forward], kernel: KernelState) -> str:
    """Idempotent transaction that makes table ip pm match `forwards`."""
    act = active(forwards)
    want_counters = {n for f in forwards for n in counter_names(f)}
    want_sets = {f"f{f.id}_src" for f in act}
    want_chains = {f"f{f.id}" for f in act}

    L = [
        f"add table ip {T}",
        f"add chain ip {T} prerouting {{ type nat hook prerouting priority dstnat; policy accept; }}",
        f"add chain ip {T} forward {{ type filter hook forward priority filter - 1; policy accept; }}",
        f"add set ip {T} kill4 {{ type ipv4_addr . inet_proto . inet_service . inet_service; flags timeout; timeout 60s; }}",
        f"flush chain ip {T} prerouting",
        f"flush chain ip {T} forward",
        f"add rule ip {T} forward meta l4proto tcp {KILL_KEY} @kill4 reject with tcp reset",
        f"add rule ip {T} forward {KILL_KEY} @kill4 drop",
    ]
    # stale objects (their references were just flushed away)
    for ch in sorted(kernel.chains - want_chains - {"prerouting", "forward"}):
        L += [f"flush chain ip {T} {ch}", f"delete chain ip {T} {ch}"]
    for s in sorted(kernel.sets - want_sets - {"kill4"}):
        L.append(f"delete set ip {T} {s}")
    for c in sorted(kernel.counters - want_counters):
        L.append(f"delete counter ip {T} {c}")

    for c in sorted(want_counters):
        L.append(f"add counter ip {T} {c}")
    for f in act:
        srcs = ", ".join(str(n) for n in f.allowed_sources)
        L += [
            f"add set ip {T} f{f.id}_src {{ type ipv4_addr; flags interval; auto-merge; }}",
            f"flush set ip {T} f{f.id}_src",
            f"add element ip {T} f{f.id}_src {{ {srcs} }}",
            f"add chain ip {T} f{f.id}",
            f"flush chain ip {T} f{f.id}",
            f'add rule ip {T} f{f.id} ct direction original counter name "f{f.id}_in"',
            f'add rule ip {T} f{f.id} ct direction reply counter name "f{f.id}_out"',
        ]
    for f in act:
        for r in prerouting_rules(f):
            L.append(f"add rule ip {T} prerouting {r}")
    elems = vmap_elements(act)
    if elems:
        L.append(f"add rule ip {T} forward ct status dnat meta l4proto . ct original proto-dst "
                 f"vmap {{ {', '.join(elems)} }}")
    return "\n".join(L) + "\n"


def build_boot_script(forwards: list[Forward]) -> str:
    """Standalone file loaded by the gateway at boot, before the manager starts."""
    head = f"#!/usr/sbin/nft -f\n# generated by portmirror manager - do not edit\n" \
           f"table ip {T}\ndelete table ip {T}\n"
    return head + build_script(forwards, KernelState())


def expected_prerouting_rules(forwards: list[Forward]) -> int:
    return sum(len(prerouting_rules(f)) for f in active(forwards))


# --------------------------------------------------------------------------
# kernel I/O
# --------------------------------------------------------------------------
async def run(*cmd: str, stdin: Optional[str] = None, timeout: float = 15) -> tuple[int, str, str]:
    """Run a command. If `stdin` is given, it's written to a temp file and any
    trailing "-" argument is replaced with that file's path, instead of piping
    the text to the child's stdin.

    This isn't just style: under uvloop (uvicorn's default loop), a subprocess's
    stdin pipe is a socketpair, not a plain pipe/fifo - and some nftables builds
    (seen with 1.0.9, not with 1.1.6) reject `-f -` outright when stdin is a
    socket ("Not a regular file: /dev/stdin"), so every apply would fail. A real
    temp file sidesteps the whole question of what kind of fd stdin is.
    """
    tmp = None
    if stdin is not None:
        fd, path = tempfile.mkstemp(suffix=".nft", prefix="pm-")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(stdin)
        except BaseException:
            os.unlink(path)
            raise
        tmp = path
        cmd = tuple(tmp if c == "-" else c for c in cmd)
    try:
        p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), timeout)
        return p.returncode, out.decode(errors="replace"), err.decode(errors="replace")
    finally:
        if tmp is not None:
            os.unlink(tmp)


async def kernel_state() -> KernelState:
    rc, out, _ = await run("nft", "-j", "list", "table", "ip", T)
    if rc != 0:
        return KernelState(exists=False)
    ks = KernelState(exists=True)
    for item in json.loads(out).get("nftables", []):
        if "chain" in item:
            ks.chains.add(item["chain"]["name"])
        elif "set" in item:
            ks.sets.add(item["set"]["name"])
        elif "counter" in item:
            ks.counters.add(item["counter"]["name"])
        elif "rule" in item and item["rule"].get("chain") == "prerouting":
            ks.prerouting_rules += 1
    return ks


async def apply(forwards: list[Forward]) -> str:
    """Check, then apply atomically. Raises NftError and leaves the kernel untouched on failure."""
    script = build_script(forwards, await kernel_state())
    rc, _, err = await run("nft", "-c", "-f", "-", stdin=script)
    if rc != 0:
        raise NftError(_clean(err))
    rc, _, err = await run("nft", "-f", "-", stdin=script)
    if rc != 0:
        raise NftError(_clean(err))
    write_boot_file(forwards)
    return script


def write_boot_file(forwards: list[Forward]):
    tmp = config.BOOT_RULES.with_suffix(".tmp")
    tmp.write_text(build_boot_script(forwards))
    os.replace(tmp, config.BOOT_RULES)


def _clean(err: str) -> str:
    return "\n".join(l for l in err.strip().splitlines() if l.strip())[:2000]


async def read_counters() -> dict[str, tuple[int, int]]:
    rc, out, _ = await run("nft", "-j", "list", "counters", "table", "ip", T)
    if rc != 0:
        return {}
    res = {}
    for item in json.loads(out).get("nftables", []):
        c = item.get("counter")
        if c:
            res[c["name"]] = (c.get("packets", 0), c.get("bytes", 0))
    return res


# --------------------------------------------------------------------------
# conntrack
# --------------------------------------------------------------------------
_KV = re.compile(r"(\w+)=(\S+)")


@dataclass
class Flow:
    proto: str
    state: str
    ttl: int
    src: str
    dst: str
    sport: int
    dport: int
    reply_src: str
    reply_sport: int
    pkts_in: int = 0
    bytes_in: int = 0
    pkts_out: int = 0
    bytes_out: int = 0
    assured: bool = False

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def parse_conntrack(text: str) -> list[Flow]:
    flows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] not in ("tcp", "udp"):
            continue
        proto, ttl = parts[0], int(parts[2])
        state = parts[3] if "=" not in parts[3] else ""
        # each "src=" opens a tuple: first = original direction, second = reply
        groups, cur = [], None
        for k, v in _KV.findall(line):
            if k == "src":
                cur = {}
                groups.append(cur)
            if cur is not None and k in ("src", "dst", "sport", "dport", "packets", "bytes"):
                cur[k] = v
        if len(groups) < 2:
            continue
        orig, reply = groups[0], groups[1]
        if not state:
            state = "UNREPLIED" if "[UNREPLIED]" in line else "ASSURED" if "[ASSURED]" in line else "ACTIVE"
        try:
            flows.append(Flow(
                proto=proto, state=state,
                ttl=ttl, src=orig["src"], dst=orig["dst"], sport=int(orig["sport"]), dport=int(orig["dport"]),
                reply_src=reply.get("src", ""), reply_sport=int(reply.get("sport", 0)),
                pkts_in=int(orig.get("packets", 0)), bytes_in=int(orig.get("bytes", 0)),
                pkts_out=int(reply.get("packets", 0)), bytes_out=int(reply.get("bytes", 0)),
                assured="[ASSURED]" in line))
        except (KeyError, ValueError):
            continue
    return flows


async def list_flows() -> list[Flow]:
    rc, out, _ = await run("conntrack", "-L", timeout=20)
    return parse_conntrack(out) if rc == 0 else []


def flow_matches(flow: Flow, f: Forward) -> bool:
    return (flow.proto in f.protocols and flow.dport in f.listen_ports
            and flow.reply_src == str(f.target_ip) and flow.src != flow.reply_src)


CLOSED_TCP = {"TIME_WAIT", "CLOSE"}


async def kill_forward_flows(f: Forward, flows: Optional[list[Flow]] = None) -> int:
    """Cut every open connection of a forward. Returns how many open connections were cut.

    Cost is O(ports), not O(connections): open flows are blocked in one batched nft call,
    then conntrack entries are removed with one `conntrack -D` per (protocol, port),
    filtered by the forward's target - finished (TIME_WAIT) entries are just cleared.
    """
    flows = flows if flows is not None else await list_flows()
    targets = [fl for fl in flows if flow_matches(fl, f)]
    open_flows = [fl for fl in targets if not (fl.proto == "tcp" and fl.state in CLOSED_TCP)]
    await _block([(fl.src, fl.proto, fl.sport, fl.dport) for fl in open_flows])
    for proto, port in sorted({(fl.proto, fl.dport) for fl in targets}):
        await run("conntrack", "-D", "-p", proto, "--orig-dst", str(config.GW_LAN_IP), "--dport", str(port),
                  "--reply-src", str(f.target_ip))
    return len(open_flows)


async def kill_flow(proto: str, src: str, sport: int, dst: str, dport: int) -> bool:
    await _block([(src, proto, sport, dport)])
    return await _ct_delete(proto, src, sport, dst, dport)


async def _block(tuples: list[tuple]):
    """Reject the next packets of these flows (see kill4 above)."""
    for i in range(0, len(tuples), 500):
        elems = ", ".join(f"{src} . {proto} . {sport} . {dport}" for src, proto, sport, dport in tuples[i:i + 500])
        if elems:
            await run("nft", "add", "element", "ip", T, "kill4", f"{{ {elems} }}")


async def _ct_delete(proto: str, src: str, sport: int, dst: str, dport: int) -> bool:
    rc, _, _ = await run("conntrack", "-D", "-p", proto, "--orig-src", src, "--orig-dst", dst,
                         "--sport", str(sport), "--dport", str(dport))
    return rc == 0
