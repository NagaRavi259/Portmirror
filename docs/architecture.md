# Architecture

## The problem

A service on a remote network — say `10.0.0.112:3389`, an RDP host behind a home VPN — needs to be reachable
from a local network as if it were local, without local devices knowing it actually lives behind a tunnel. The
common fix is a per-service SSH reverse tunnel, which works but doesn't scale: it's TCP-only, needs a new
forward for every port, and stops working the instant the SSH session drops.

A VPN connection between the gateway and the remote network already exists (WireGuard, OpenVPN, Tailscale —
the design doesn't care which). What's missing is a general-purpose mapping layer that uses that existing
tunnel for any number of ports, TCP and UDP, without hand-maintained sessions.

## The design: DNAT + masquerade

The gateway sits on both networks — one interface on the local LAN, one on the VPN. Two kernel-level
`nftables` rules do the actual work:

1. **DNAT** (destination NAT), in the `prerouting` hook: a connection arriving on the gateway's LAN address and
   a given port gets its destination rewritten to the real remote service's address and port.
2. **Masquerade**, in the `postrouting` hook, on the VPN-facing interface: the connection's *source* address
   gets rewritten to the gateway's own address on that interface.

The masquerade step is not optional — it's the reason the whole thing works. Without it, the remote service
sees the real local device's address as the source and tries to reply directly to it. The remote network has
no route to the local network, so the reply is dropped and the connection hangs. With masquerade in place, the
remote service only ever talks to an address it can already reach — the gateway's own VPN-side address — and
the kernel's connection tracking transparently routes the reply back to the real local client. This also means
the remote side never learns the real local device's address, which is the mirroring behaviour the design
exists to provide, not an incidental side effect.

```
Local device  ──DNAT──▶  Gateway  ──masquerade──▶  VPN tunnel  ──▶  Remote service
     ▲                 (rewrites the              (rewrites the
     │                  destination)                source)
     └── connection tracking un-translates the reply back to the real local device
```

## Static rules vs. dynamic rules

The ruleset is split into two parts:

- **Static base rules** (loaded once, at startup): masquerade on the VPN interface, a forward filter that
  drops anything that isn't an already-DNAT'd flow from the LAN side, and a rule blocking the management UI's
  port from the VPN-side network entirely.
- **Dynamic forward rules**, owned by the manager service and rebuilt whenever a forward is added, edited, or
  removed. Each enabled forward gets:
  - a set of allowed source addresses (so a forward can be scoped to specific devices, not just the whole LAN)
  - a DNAT rule per protocol, with a named counter that increments once per new connection
  - a small per-forward chain that counts bytes and packets in each direction, reached through a single
    verdict map keyed on protocol and destination port
  - optional rate-limit and concurrent-connection-limit rules

Named counters live outside the chains they're referenced from, so rebuilding the ruleset — which happens on
every single change — never resets a forward's totals and never disturbs connections already in flight.
Rebuilding is also checked before it's applied (`nft -c`) and only takes effect if the check passes, so a bad
configuration can't take down a working one.

### Cutting a connection properly

Deleting a connection's conntrack entry while its forward is still enabled isn't enough to actually end it —
the very next packet gets DNAT'd and masqueraded again exactly as before, and the connection simply resumes.
Ending a connection for real means its next packet has to be rejected outright. A short-lived blocklist set
handles this: a cut connection's packets are matched against it and get a TCP reset (or are dropped, for UDP)
before the conntrack entry is removed.

## Bandwidth shaping

A forward can also cap its own throughput, independently of every other forward, in each direction. This is a
separate mechanism from the rest of the ruleset above, layered on top of it rather than built into it:

- A forward with a limit set gets one extra, otherwise-inert rule in its per-forward chain: mark every packet
  of that forward's connections, in both directions, with the forward's own id. Nothing reads that mark unless
  shaping is actually in use, so it costs nothing for every other forward.
- On each of the gateway's two interfaces, a shared traffic-control tree holds one class per shaped forward,
  sized to that forward's own limit, selected purely by matching the mark just set. A forward with no limit
  never gets marked and so never leaves that tree's default, unrestricted class.
- Each class also gets its own fair-queuing leaf. A hard rate cap alone, squeezing a connection down to a small
  fraction of the interface's real capacity, holds the right long-run average but does it in sharp bursts
  separated by stalls; the extra leaf trades a bit of packet loss for a steady, accurately-capped line instead
  — the same trade-off any real bandwidth limiter (a home router's QoS, an ISP's own throttling) makes.
- Rebuilding this tree is best-effort and separate from the main ruleset's own all-or-nothing apply: if the
  underlying mechanism is ever unavailable, a forward simply runs unshaped rather than failing to apply at all.

This is a steady-state throughput cap (kbit/s, sustained), not a quota — see the roadmap for the different,
not-yet-built idea of a total-bytes-over-a-period limit.

## Target health and metrics

A background task probes every enabled forward's target every few seconds — a TCP connect for TCP/TCP+UDP
forwards, an ICMP ping for UDP-only ones — and the result is shown in the dashboard. A separate task reads the
kernel's counters and connection table once a second, keeping a short in-memory history for live charts and
writing per-minute summaries to disk for longer-range history.

## Security scoping

- Each forward can be restricted to specific source addresses, not just "anyone on the LAN."
- The management UI's port is blocked from the VPN-side network by the base ruleset — only LAN devices can
  reach it, the same trust boundary the forwards themselves use.
- The forward filter only ever allows traffic that a DNAT rule actually created; using the gateway as a plain
  router to the remote network — bypassing the forwards entirely — is dropped.
- IPv6 is out of scope for this design and is explicitly dropped rather than silently forwarded unfiltered.
