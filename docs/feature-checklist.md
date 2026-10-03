# Feature checklist

A living tracker for the next batch of features: one checkbox per feature, with its plan and the test cases
that must pass before it's ticked off. Test cases accumulate and are never removed as the project grows - a
regression suite is only worth anything if it keeps every case that was ever worth checking, not just the
newest ones. A feature is only checked off once it's implemented **and** verified against real traffic or a
real browser, per this project's own standard (see `progress.md`), not just unit-tested in isolation.

## Selected for implementation

### [ ] 1. Built-in diagnostics ("Doctor") — implemented, not fully exercised yet

One button (UI page, and a `pmctl diag` equivalent) that runs everything that's currently a manual log-reading
exercise, and shows a clear pass/fail per check instead.

**Built:** `manager/app/diag.py` (ten checks: ruleset syntax, kernel table presence, route to the VPN subnet,
LAN/VPN interface state, conntrack usage, disk, memory, target health, service restart counts), a new
`GET /api/diag` endpoint, a `pmctl diag` command, and a new Diagnostics page in the UI (green/red rows, a
summary banner, a manual re-run button). Each check's own ok/warn/fail/skip thresholds are unit-tested as pure
functions, and `pmctl diag` + the UI page both read the same endpoint, so they can't disagree with each other
by construction.

**Test cases:**
- [x] Every check reports pass under a healthy stack — confirmed live against the real running gateway
- [ ] A deliberately broken ruleset (syntax error written directly, bypassing the app) is reported as failing,
  specifically, not just "something's wrong" — not yet exercised; would need the self-heal housekeeping loop
  paused first (it repairs kernel drift within seconds, which would otherwise mask the broken state before a
  check could observe it)
- [ ] The VPN-down case (interface carrier lost) is reported correctly, distinctly from a route-missing case —
  not yet exercised; needs a safe way to simulate carrier loss without disrupting the shared dev host's own
  networking
- [ ] High conntrack usage (near `nf_conntrack_max`) is flagged before it actually fills up — not yet
  exercised; needs a realistic way to generate that much load
- [x] `pmctl diag`'s output and the UI page agree on every check, every time — true by construction (same
  endpoint), and cross-checked directly in both the pass and fail cases below
- [x] Running it causes no disruption to live traffic — confirmed across every run during testing, including
  while a real target was deliberately stopped
- [x] *(added during testing)* A real failing check renders correctly, not just the happy path — verified by
  stopping a real target container: `pmctl diag` showed `[FAIL] Target health 1/7 target(s) unreachable` with
  exit code 1, and the UI showed a red summary banner and a red row/dot on exactly that check, both matching

### [x] 2. Device nicknames

Name a client IP once; see that name everywhere that IP would otherwise show up raw.

**Built:** a `device_names(ip PRIMARY KEY, name, created_at)` table, `GET/PUT/DELETE /api/device-names`, and a
shared `ClientLabel` component (click an IP, type a name, Enter to save, clear it to remove) used in the
live-connections table, the Connections page (recent sessions and the "By client" summary), and the top-clients
list — all read from one `deviceNames` map fetched once in `App.tsx`, so everywhere updates together from a
single source. The audit log takes a different approach: it mentions IPs inside free-form text rather than a
discrete field, so a small `withDeviceNames()` text-annotation pass handles it instead of `ClientLabel`.

**Test cases:**
- [x] Naming an IP updates the live-connections table immediately, without a page reload — e2e-tested
- [x] The same name shows on the Connections page and in audit log entries mentioning that IP — e2e-tested for
  Connections; audit log confirmed with a real screenshot (`192.168.88.67 (Verify Audit) -> Verify Audit`)
- [x] An unnamed IP still displays exactly as it does today - no regression for the common case — confirmed
  throughout testing; the only change is a hover-only rename affordance, never a layout or data change
- [x] Renaming and deleting a nickname both work and take effect everywhere at once — verified directly via the
  API (rename in place, not a duplicate row) and live in the UI
- [x] Names persist across a gateway restart — confirmed with a real `docker compose restart`
- [x] Two different IPs can't accidentally collide on one name silently — covered at the data layer by
  `test_device_names_set_list_rename_delete` (two distinct IPs/names never cross), and the UI side is a plain
  per-row map lookup keyed by that row's own IP, with no shared or positional state to confuse rows

### [x] 3. In-app alerting / notification center

A notifications panel inside the app itself - not email/webhook/push (that's the separate, still-just-an-idea
"Notifications" item in `roadmap.md`) - surfacing things like a target's health flipping, repeated failed
logins, a forward auto-expiring, or kernel drift being repaired, with "took action," "dismiss," and "skip
this kind of alert in future" actions.

**Built:** a `notifications` table (state: unread / read / actioned / dismissed) and a `notification_mutes`
table (one row per muted type), a `Notifier` class that four real event sources call into - `prober.py` (target
health flips, via a pure `health_flip()` helper so the transition logic is unit-testable without real network
I/O), `service.py` (`forward_expired`, `kernel_drift`), and `auth.py` (`login_failures`, firing once when the
existing 60s failed-login counter crosses 3, not on every failure past it) - plus `GET/POST /api/notifications*`
and a bell icon in the header (unread badge, a dropdown panel, three actions per item).

**Test cases:**
- [x] A target going down creates exactly one notification, not one per polling tick while it stays down —
  unit-tested (`health_flip` never re-fires while the state is unchanged) and confirmed live: stopped a real
  target container, got exactly one `target_down`, no repeats over the following polls
- [x] The same target coming back up creates its own, separate "recovered" notification — confirmed live:
  restarted the container, got exactly one `target_up`
- [x] "Took action" and "skip"/dismiss both update state correctly and leave an audit trail of which was
  chosen — confirmed live via the real audit log (`notification.dismissed` / `notification.mute` entries)
- [x] "Skip future" for one alert type suppresses only that type, confirmed by also triggering a different
  type and seeing it still come through — confirmed live: muted `target_up`, then a fresh stop/start cycle
  produced a new `target_down` but no new `target_up`
- [x] The unread badge count is accurate after each action, including across a page reload — confirmed with a
  real reload (badge read identically before and after)
- [x] *(confirmed, not built further)* A burst of many simultaneous events doesn't flood the panel unreadably —
  actual behavior: no automatic grouping: each event is its own row, newest-first, in a scrolling list capped
  at 100. Acceptable for how infrequently these events actually fire (health flips, expiries, drift repairs are
  not high-volume by nature) - revisit only if real use shows otherwise

### [ ] 4. Service presets / quick-add — built, some test cases still open

A dropdown of common services (RDP 3389, VNC 5900, SSH 22, Jellyfin 8096, Pi-hole 80, Home Assistant 8123,
Plex 32400, Minecraft 25565) that pre-fills protocol and local port in the new-forward form.

**Built:** `lib/presets.ts` (the list), and a "Start from a service" dropdown at the top of the new-forward
form (create mode only; edit is unchanged). Picking a preset fills the port and protocol and nothing else -
the remote target is never touched, since it's always specific to your own network.

**Test cases:**
- [x] Each preset fills the port it claims to - e2e (`UI-PRESET`) checks Jellyfin 8096 and Minecraft 25565 land in the port field
- [ ] Protocol is set from the preset - *not really exercised yet*: every current preset is TCP, so a preset that changes protocol has never been tried
- [x] Picking a second preset replaces the first cleanly - covered by the same e2e test (second pick replaces 8096 with 25565)
- [ ] A preset whose port conflicts with an existing forward shows the normal inline conflict error - not yet tested explicitly

### [x] 5. Dark mode

A toggle in the header (quick access, every page) and a Light/Dark/System tri-state control in Settings
(the only one of the two that can get back to "follow system" after an explicit choice).

**Built:** every existing color token (`ink`, `line`, `canvas`, `accent`, `signal`, the status colors `rose`/
`emerald`/`amber`, and `white` itself - repurposed as "the card/panel surface," which is what the vast majority
of its existing use actually meant) now resolves through a CSS custom property instead of a literal hex value,
with light and dark value sets in `index.css`. This re-themes every existing component automatically - no
per-component `dark:` classes needed anywhere. A few spots deliberately do **not** track the theme, because
inverting them would be wrong: modal/drawer backdrops stay true black in both themes (a scrim must darken, not
lighten), and text sitting on an already-vivid, non-theme-aware surface (button labels, the notification
badge) stays true white via a new `oncolor` token. The one place canvas rendering is involved - the uPlot
traffic charts, which draw directly, not through CSS - reads the same CSS variables live via `getComputedStyle`
and rebuilds itself when the theme changes, so it never drifts out of sync with the rest of the page.

**Test cases:**
- [x] Toggling switches every page, not just the one currently open - confirmed with real screenshots of the
  dashboard, Connections, Audit log, Diagnostics, Settings, and a forward's detail page (traffic charts
  included) all in dark mode, plus the equivalent light-mode shots for comparison
- [x] The choice persists across a reload - confirmed with a real reload keeping `data-theme="dark"`
- [x] With no stored preference, it follows the OS/browser dark-mode setting - confirmed: clearing the stored
  preference via Settings' "System" option resolves back to the browser's own setting
- [x] Every page and component is checked for contrast/legibility in dark mode specifically - charts, chips,
  status dots, and the live traffic sparklines included, not just text and backgrounds - checked visually
  across all six pages above; the chart axis labels and gridlines in particular needed their own fix (see
  `docs/fixed-issues.md`), since they're drawn to canvas and CSS alone can't reach them
- [x] No UI element is left hardcoded to a light-only color that breaks in dark mode - the 124 existing
  `rose`/`emerald`/`amber` utility usages and 21 `bg-white` usages across 15 files were audited and now all
  resolve through the same themeable tokens, rather than hand-patching each file

### [ ] 6. Recurring access windows — built, some test cases still open

A forward that's only on during certain hours, on certain days, instead of being flipped by hand. Distinct
from the one-shot `expires_at`.

**Built:** an optional `access_window` on each forward: a set of weekdays plus a start and end time (gateway
local time). A window whose end is earlier than its start runs past midnight. The housekeeping loop (every 5 s)
acts only when a boundary is crossed - it never fights a manual change between boundaries. Form: a "Only on a
schedule" switch with day toggles and start/end times. Detail page shows it as a chip.

**Test cases:**
- [x] Window turns a forward off outside its hours and on inside them - verified live: a Monday-only window
  disabled a Saturday forward within 8 s; a Saturday 06:00-07:00 window (current time 06:2x) enabled it
- [x] A manual disable inside an active window holds until the next boundary - verified live: disabled at 06:22:15,
  still disabled 15 s later; audit trail records each window action separately from manual ones
- [x] Midnight-wrapping windows and day boundaries - unit-tested (`test_access_window_wraps_midnight...`,
  `test_access_window_same_day_range`)
- [x] Validation rejects empty day lists, bad times, and start == end - unit-tested
- [x] Schedule persists in the store - unit-tested (`test_forward_round_trips_its_access_window_through_the_store`)
- [ ] A real midnight crossing observed on the live clock - not yet; covered by the unit test of the boundary logic instead
- [ ] A forward with both a schedule and `expires_at` - not yet tested together
- [x] Existing forwards with no schedule are unaffected - a forward with `access_window=None` is skipped entirely by the loop

### [ ] 7. Per-forward bandwidth quota (bytes over a period) — built, verification in progress

Total data (bytes in + out) allowed per day, week or month. Distinct from the realtime kbit/s cap, which
limits speed. The two are independent and both apply to the same forward.

**Decided (2026-10-03):**
- Reaching the quota **disables** the forward - it is never deleted.
- The monitoring page shows it in a distinct colour (violet) as "quota reached".
- A notification says the forward reached its quota and offers **Increase quota** or **Delete forward**.
  Both open the forward's page, where its own Edit and Delete (with confirmation) live.
- The realtime cap and the quota combine as: the cap limits speed, the quota limits total data. A forward
  switched off by its quota loses its shaping class, since it isn't carrying traffic.

**Built:**
- `app/quota.py`: period starts (day from local midnight, week from Monday, month from the 1st) and the pure
  decision (`quota_action`): disable when over and on; re-enable only a forward the quota itself switched off,
  and only once back under the limit.
- `Quota` on `ForwardIn` (bytes, period); usage = the per-minute rollups for the current period plus the
  in-memory current minute (`Collector.current_minute_bytes`), so enforcement reacts within a few seconds
  instead of waiting for the minute to flush.
- Housekeeping loop (every 5 s) enforces it and writes `forward.quota_reached` / `forward.quota_reset` audit
  entries plus the notification. An expired forward is left to expiry.
- A person taking control (a manual toggle, or an edit that disables the forward) clears the quota's claim, so
  a quota reset never overrides a manual choice.
- Form: "Data quota" amount (GiB) and period, inline validation. Detail page: a "Data quota" stat with usage.
  Notification: the two actions above.

**Test cases:**
- [x] Usage sums bytes in and out for the period - unit-tested (`test_store_usage_sums_bytes_in_and_out_since_a_cutoff`)
- [x] Period starts for day, week (Monday) and month - unit-tested (`test_quota_period_starts`)
- [x] The forward is disabled the moment it crosses the quota - verified live: 3,000-byte quota, 10,103 bytes
  used, forward switched off, audit entry and notification written, shaping class removed, forward not deleted
- [x] Raising the quota turns it back on - verified live (`forward.quota_reset`)
- [x] A manual disable holds even while under the quota - verified live: disabled, still disabled 16 s later
- [x] Quota and realtime cap on the same forward - both stored and applied; the disabled forward lost its class
- [ ] The quota resets at the period boundary and the forward runs again - decision logic unit-tested
  (`test_quota_action_only_when_it_changes_something`). Verified live without waiting for midnight:
  rollups dated yesterday (5 MB) are excluded from today's usage; clearing today's rows drops usage to 0 and
  `forward.quota_reset` re-enables the forward. Not yet observed across a real day/week/month rollover.
- [x] UI shows usage against the quota - verified in a real browser: detail page "100% - 9.87 KB of 1000 B per
  day - turned off"; the violet status colour and the bell's two actions render
- [x] Form accepts an amount and period and rejects nonsense inline - e2e `UI-QUOTA`
- [x] Usage reacts near-live - enforcement verified live: a 3,000-byte quota switched the forward off about 3 s
  after the traffic crossed it (in-memory current minute); the housekeeping cadence is 5 s. The page's display
  refresh follows the UI poll interval.

### [ ] 8. CSV export for history and audit — built, some test cases still open

**Built:** `app/export.py` (pure rendering, unit-tested), `GET /api/export/audit.csv` and
`GET /api/export/history.csv?forward_id=&range=`, served as attachments. Download buttons on the Audit log page
and on each forward's History panel.

**Test cases:**
- [x] Exported headers and rows match the underlying data - verified live through a real browser session:
  audit export 200 `text/csv`, correct header and 501 rows; history export correct header, ISO timestamps, 289 rows
- [x] Values that could break naive CSV (commas, quotes, newlines) are escaped - unit-tested (`test_csv_quotes_commas...`)
- [x] Spreadsheet formula injection is neutralised - a cell starting with `= + - @` gets a leading apostrophe,
  unit-tested. Live note: the formula-looking name sits after the `#id` prefix in audit targets, so it never
  starts a cell and was never a live formula; the defence is for any cell that does begin with user text
- [ ] Opened in a real spreadsheet application (Excel / LibreOffice) - not yet done; needs a desktop app
- [x] Large export completes - audit export capped at 100,000 rows; the live run returned 501 rows quickly

### [ ] 9. Readable audit-log diffs — built, some test cases still open

**Built:** expanding an update entry now shows only the fields that changed, as `field — old → new`, with a
"Show raw JSON" toggle for the full record. Create, delete and other entries without a before/after pair keep
their raw view.

**Test cases:**
- [x] Changing one field shows only that field - verified live with a real `rate_limit` change (`— → 9`), and
  restored afterwards; e2e `UI-AUDIT-DIFF` checks the same
- [ ] Several fields changed at once - rendering is written for it, not yet exercised live
- [x] Unchanged fields never appear - the diff compares both sides key by key
- [x] Create and delete entries still render sensibly - the raw view is used when there's no before/after pair
- [x] Raw JSON is reachable - "Show raw JSON" toggle

### [ ] 10. PWA manifest (installable dashboard) — built, some test cases still open

**Built:** `manifest.webmanifest` (standalone display, 192 and 512 icons plus a maskable 512), an Apple touch
icon and iOS meta tags, and a service worker that deliberately caches nothing (the dashboard is live data).
Icons were rendered from one SVG source. The worker registers only in a secure context.

**Test cases:**
- [x] Manifest and icons served by the gateway - verified live (200, correct content types)
- [ ] Installs on an Android phone - *not possible over the plain-HTTP LAN address*: Chrome offers install only
  from a secure origin. Needs the dashboard reached over HTTPS (e.g. Tailscale's HTTPS serve) to test
- [x] iOS "Add to Home Screen" - the apple-touch-icon and web-app meta tags are present; not yet tried on a device
- [ ] Launch from the home screen lands on the dashboard - not yet tested on a device
- [x] No regression in the ordinary browser experience - the full e2e suite runs unchanged

## Needs a decision before planning further

### [ ] Target by hostname instead of a static IP

**Resolved, not being built:** raised as possibly hard (device hostname collisions - e.g. two devices both
reporting `raspberrypi` - and devices with no hostname at all). The user is handling address stability at the
router level instead (DHCP reservations), which sidesteps the whole problem without any application change.
Left here, unchecked, as a record of the decision rather than as pending work - revisit only if the DHCP-based
approach ever stops being sufficient.

## Kept for the future, not currently planned

These stay recorded but are deliberately not being designed yet - see `roadmap.md` for both:

- **Multiple remote networks** — the gateway currently assumes one VPN interface/subnet; a second site would
  mean every forward also needs to pick "which remote."
- **Read-only viewer accounts** — sharing a dashboard link without granting control.
