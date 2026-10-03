# Roadmap

## Selected and actively planned

A batch of ten features has moved from "idea" to "planned, with test cases" - see
[`feature-checklist.md`](feature-checklist.md) for the checkbox-tracked list: a diagnostics page, device
nicknames, in-app alerting, service presets, dark mode, recurring access windows, a bandwidth quota (bytes over
a period, distinct from the realtime kbit/s cap already shipped), CSV export, readable audit-log diffs, and a
PWA manifest. That document is the source of truth for those ten going forward, not this one.

## Under consideration

Everything else: grouped by theme, not yet prioritized, not yet planned in detail.

**Operational visibility**
- Notifications delivered outside the app itself (webhook, a push-notification service, or a messaging-platform
  integration) when a target's health flips, or when logins start failing repeatedly - distinct from the
  in-app notification center in `feature-checklist.md`, which doesn't need any of this.
- A resource-usage trend on the dashboard (CPU and memory over time), not just the instantaneous reading.
- A per-forward way to turn off health probing entirely. Would also resolve a known, understood, cosmetic
  side effect: probing a target that's picky about unexpected connections (`iperf3` is one - see
  [`progress.md`](progress.md)) makes it log its own error on a fixed interval, harmlessly, forever. Worth
  researching properly (is a plain connect-probe the only option, or is there a gentler check for services
  like this) rather than just adding an on/off switch.

**Usability**
- Resolving a forward's target by hostname instead of a fixed address. Deferred, to revisit later: address
  stability is handled at the router with DHCP reservations for now. Open concerns if it's ever built: two
  devices can report the same hostname (e.g. several `raspberrypi`), and some devices report none.

**Access and sharing**
- Read-only accounts, for sharing the dashboard without granting control.
- An opt-in way to expose a single forward publicly through the VPN provider's own tunnel feature, where one is
  available, without touching the gateway's own firewall.

**Scale and integration**
- Support for more than one remote network/VPN interface at once, if a second remote site is ever added.
- A metrics endpoint in a standard scrape format, for anyone already running their own monitoring stack.

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
