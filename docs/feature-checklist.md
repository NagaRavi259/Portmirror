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

### [ ] 4. Service presets / quick-add

A dropdown of common services (RDP 3389, VNC 5900, SSH 22, Jellyfin 8096, Pi-hole 80, Home Assistant 8123,
Plex 32400, Minecraft 25565, …) that pre-fills protocol and listen port in the new-forward form.

**Plan:** a static list in the UI only (no backend change) - selecting a preset fills `protocol` and
`listen_port` (never the target, since that's always specific to the user's own network) and the user still
reviews/edits before saving. A "custom" option (today's blank form) stays the default.

**Test cases:**
- [ ] Each preset fills the exact protocol and port it claims to
- [ ] Picking a preset, then changing the port manually, keeps the manual edit (the preset doesn't re-apply
  and overwrite it)
- [ ] Picking a different preset after one is already selected replaces the fields cleanly, no leftover state
- [ ] A preset whose port conflicts with an existing forward still shows the normal inline conflict error

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

### [ ] 6. Recurring access windows

A forward that's only meant to be on during certain hours (e.g. 9am-10pm daily), instead of needing to be
flipped by hand, and distinct from the existing one-shot `expires_at`.

**Plan:** add a schedule to `ForwardIn` (e.g. days-of-week + start/end local time). The housekeeping loop,
which already disables expired forwards every 5s, gains the same check for "outside its scheduled window" and
toggles accordingly - reusing the exact enable/disable + kernel-apply path that already exists, not a new
mechanism. A forward within its window behaves exactly like `enabled: true` does today.

**Test cases:**
- [ ] A forward turns on and off at the right wall-clock times, confirmed across a real day boundary (not just
  asserted against a mocked clock)
- [ ] A window that spans midnight (e.g. 10pm-6am) works correctly, not just same-day windows
- [ ] Manually disabling a forward during its "on" window keeps it off until the next window start, rather than
  the schedule fighting the manual override every 5s
- [ ] A forward with both a schedule and a one-shot `expires_at` behaves sensibly when both are active
- [ ] Existing forwards with no schedule set are completely unaffected

### [ ] 7. Per-forward bandwidth quota (bytes over a period)

Distinct from the realtime kbit/s throughput cap already shipped (`bandwidth_limit_kbps` - done, see
`progress.md`): a cumulative cap, e.g. "500 MB/day," after which the forward throttles hard or disables itself
until the period resets. Relevant when data usage itself matters (e.g. relaying over a metered connection),
not just instantaneous speed.

**Plan:** a `quota_bytes` + `quota_period` (daily/weekly/monthly) field on `ForwardIn`. Usage is summed from
the traffic history rollups already being written every minute for each forward - no new counting mechanism
needed, just a periodic check (housekeeping loop again) comparing the period's running total against the
quota, and disabling the forward (or clamping its `bandwidth_limit_kbps` down hard, if an already-shaped
forward hits its quota - needs a decision on which) when exceeded. Resets automatically at the next period
boundary.

**Test cases:**
- [ ] Usage sums bytes in **and** out correctly over the period, matching the dashboard's own totals for the
  same window
- [ ] The forward is correctly disabled (or throttled, per whichever behavior is chosen) the moment it crosses
  the quota, not just eventually
- [ ] The quota resets cleanly at the period boundary and the forward can run again
- [ ] A quota combined with an existing `bandwidth_limit_kbps` on the same forward behaves as decided, not
  ambiguously
- [ ] The UI shows current usage against the quota (a progress bar or similar), updating live

### [ ] 8. CSV export for history and audit

**Plan:** `/api/export/history.csv` and `/api/export/audit.csv` endpoints alongside the existing JSON
export/import; buttons on the relevant pages (History/dashboard, Audit log) next to the existing JSON export.

**Test cases:**
- [ ] Exported CSV headers and rows match the underlying data exactly (spot-checked against the JSON export of
  the same range)
- [ ] Values that could break naive CSV (commas, quotes, newlines in a forward's name or description) are
  escaped correctly - confirmed by actually opening the export in a real spreadsheet app, not just checking
  that it's technically valid CSV
- [ ] A large export (the full retained history) completes without timing out or truncating

### [ ] 9. Readable audit-log diffs

Audit entries for an update already store `{before, after}` - render that as a field-by-field diff instead of
a raw JSON dump.

**Plan:** UI-only change to the Audit log page: when an entry's detail has `before`/`after` keys, render only
the fields that actually changed, old value → new value, instead of the full JSON blob. Create/delete entries
(no `before`/`after` pair) keep their current rendering.

**Test cases:**
- [ ] Changing one field (e.g. just `rate_limit`) shows only that field in the diff, not the whole object
- [ ] Changing several fields at once shows all of them, clearly separated
- [ ] A field that didn't change is never shown as part of the diff, even if it's present in both `before` and
  `after`
- [ ] Create and delete entries still render sensibly (no empty/broken diff view)
- [ ] The raw JSON is still reachable somehow (e.g. an expandable "raw" toggle) for anyone who wants it

### [ ] 10. PWA manifest (installable dashboard)

**Plan:** a `manifest.json`, an icon set, and enough of a service worker to pass installability checks - just
"add to home screen" convenience, not offline support (the dashboard is meaningless without a live connection
to the gateway, so there's no offline mode to build).

**Test cases:**
- [ ] A real mobile browser (not just a Lighthouse score) offers "Add to Home Screen," and it works
- [ ] The installed icon and name are correct, not a generic browser icon
- [ ] Launching from the home screen opens straight to the dashboard, already logged in if the session is
  still valid
- [ ] No regression to the ordinary in-browser experience for anyone who doesn't install it

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
