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

## Outstanding

These need a specific real event on physical hardware to exercise directly, rather than a Docker-based
simulation of one:

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
