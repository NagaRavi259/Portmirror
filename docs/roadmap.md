# Roadmap

## Under consideration

Grouped by theme, not yet prioritized.

**Operational visibility**
- A built-in diagnostics page (or command) that runs the standard health checks — ruleset validity, route to
  the remote network, interface status, storage and resource use, recent restarts — and shows the result
  directly, instead of needing a manual log-collection pass.
- Notifications (webhook, a push-notification service, or a messaging-platform integration) when a target's
  health flips, or when logins start failing repeatedly.
- A resource-usage trend on the dashboard (CPU and memory over time), not just the instantaneous reading.

**Usability**
- Service presets for common targets (RDP, VNC, SSH, common self-hosted services) that pre-fill a new
  forward's protocol and port.
- Resolving a forward's target by hostname instead of a fixed address, so it keeps working if the target's
  address changes.
- Human-readable names for known client addresses, so the live-connections view doesn't show raw addresses.
- A dark theme.
- Recurring time-based access windows for a forward (only active during certain hours), beyond the existing
  one-shot auto-expiry.

**Access and sharing**
- Read-only accounts, for sharing the dashboard without granting control.
- An opt-in way to expose a single forward publicly through the VPN provider's own tunnel feature, where one is
  available, without touching the gateway's own firewall.

**Scale and integration**
- Support for more than one remote network/VPN interface at once, if a second remote site is ever added.
- A metrics endpoint in a standard scrape format, for anyone already running their own monitoring stack.
- Per-forward bandwidth quotas (total bytes over a period), beyond the existing new-connection rate limit.

**Smaller polish**
- A readable diff view for audit log entries, instead of a raw before/after dump.
- CSV export for history and the audit log.
- A home-screen-installable version of the dashboard on mobile.

## On hold

**Running the gateway on an Android phone instead of a Raspberry Pi.** Researched and set aside for now, not
built. A non-rooted phone can't do this at all — the platform never grants ordinary applications the
permission this design's core mechanism needs, and a phone's own tethering path routes other devices' traffic
below where any app, rooted or not, could intercept it without root access to the device. A rooted phone can
reach the same core mechanism others have built this way before, but this project's specific dependencies hit
real build problems on that platform, and the platform actively and repeatedly kills exactly this kind of
background service with no complete fix available — a meaningfully worse fit than a small dedicated device for
something meant to run unattended. A simpler, different tool — a plain relay process needing no special
permissions at all — would be the realistic path if a phone-based option is ever revisited, not a port of the
existing mechanism.
