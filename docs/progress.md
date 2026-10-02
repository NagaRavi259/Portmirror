# Current status

## Validated

- **Core mechanism**: DNAT without masquerade reproducibly fails exactly as the design predicts (a packet
  capture shows the reply with nowhere to go); DNAT with masquerade reproducibly works, with the remote side
  never seeing the real local client's address. Confirmed with real packet captures, not just configuration
  review.
- **Every service type exercised**: HTTP, HTTPS, RDP, VNC, a real Postgres database, a live WebSocket log
  stream, and UDP — all reachable through the gateway, all mirrored correctly.
- **Dynamic forward management**: creating, editing, disabling, and deleting forwards while traffic is flowing
  through *other* forwards causes no disruption to that other traffic. A forward being edited or deleted keeps
  its own existing connections working unless "cut" is explicitly chosen.
- **Resilience**: the gateway recovers automatically from its own restart, from the management process
  crashing, from the VPN link dropping and returning, and from a backend service restarting. Rules that drift
  from the stored configuration (edited or removed outside the application) are detected and repaired within
  seconds.
- **Load**: sustained mixed-protocol traffic (HTTP, HTTPS, a database, UDP, a live stream) from concurrent
  clients, with zero failed requests, while forwards are simultaneously being created and deleted.
- **Security scoping**: traffic from outside a forward's allowed sources is never forwarded; a port scan of the
  gateway shows exactly the intended ports open; using the gateway as a plain router around the forwards
  (rather than through them) is dropped.
- **Native Raspberry Pi deployment**: installed and running in ongoing daily use on real hardware, over a real
  Tailscale-based VPN link to a second physical device, managing real services (not a test topology). A
  spontaneous reboot during that real use recovered automatically with the full set of configured forwards
  restored — the first real-hardware confirmation that a full reboot is survived cleanly.
- **Per-connection session log**: every real connection through a forward — who, which forward, when it
  started and ended, and how much data moved — is recorded and queryable per client and per forward, distinct
  from the administrative audit log. Verified against real traffic, including connections that complete faster
  than one polling interval and connections that linger in the kernel's tracking table after finishing; see
  [`fixed-issues.md`](fixed-issues.md) for the bugs that surfaced along the way.
- **Clickable forward links**: an enabled TCP forward's port on the dashboard opens that service directly in a
  new browser tab.
- **Per-forward bandwidth shaping**: a forward can cap its own throughput (kbit/s, each direction)
  independently of every other forward. Verified against real traffic with `iperf3` at multiple cap values,
  confirmed against an unthrottled baseline on the same link (a ~270x difference at a 20 Mbit/s cap), and tuned
  live for smoothness after the first version showed correct average throughput but a bursty, stalling pattern
  at low caps.

- **GitHub-based updates**: pushing a version tag builds and publishes a release automatically; GitHub is the
  only source of deployable software now, not ad-hoc file copies. The update script's own logic — the version
  check, the download, the install, and both rollback outcomes (previous version restarts fine / also fails to
  restart) — is verified against a simulated install, including the two real releases this verification itself
  shipped (one of which carried a fix for a bug the testing found: see
  [`fixed-issues.md`](fixed-issues.md)). Running `pmctl update` against a real, running Pi install is still
  outstanding — see below.

## Outstanding

These need a specific real event on physical hardware to exercise directly, rather than a Docker-based
simulation of one:

- Running `pmctl update` against the real deployed Pi (verified so far only against a simulated install on a
  dev machine, with `systemctl` itself stubbed out)
- A deliberate VPN-unit restart (as opposed to the gateway's own restart, already confirmed)
- A remote target device restarting mid-session
- The tunnel interface keeping a stable name across a real VPN reconnect (a renamed interface is known to
  break masquerade silently — confirmed in testing — but a real VPN client's actual reconnect behaviour hasn't
  been observed directly)
- Real tunnel MTU and encapsulation overhead affecting large transfers

## Known, understood, and not currently worth fixing

- A board without a battery-backed clock can show a misleading "active since" timestamp in `systemctl status`
  for services that start before the VPN link comes up and the clock corrects. Cosmetic only — see
  [`monitoring.md`](monitoring.md).
- A backend running a default development HTTP server with a small listen backlog will show kernel-level
  overflow counters under heavy concurrent load; client-side retries absorb this with zero failed requests, and
  the gateway's own connection-tracking counters stay clean throughout. This is a property of that particular
  backend, not of the gateway.
- Forwarding `iperf3` to a target makes that target's own server log a "Bad file descriptor" cookie-handshake
  error on a fixed interval, forever, with zero real clients involved. The cause is understood precisely: the
  gateway's own health probe does a plain TCP connect-then-disconnect with no data sent, which is harmless for
  every other service type tested, but trips a known fragility in how `iperf3`'s server manages its accept loop
  (worse with its default dual IPv4/IPv6 listening setup) - it always expects the very next bytes after
  accepting a connection to be its own handshake cookie, and logs an error when a connection closes before
  that happens. This is cosmetic noise in the target's own log, not a defect in the forward itself - a real
  `iperf3` test still runs and measures correctly alongside it. Worth a proper look later (see
  [`roadmap.md`](roadmap.md)) rather than a quick fix now, since the real fix is a per-forward way to turn
  health probing off entirely, which is a small feature of its own rather than a one-line change.
