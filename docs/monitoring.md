# Monitoring and diagnostics

## The dashboard

- **Forwards list**: every configured forward, its live traffic, health status, and a quick enable/disable
  toggle. Hovering the top summary cards explains what each number means.
- **Forward detail**: live connection list (with a button to cut one connection, or all of them), top clients,
  recent activity, and history charts from the live 5-minute window out to as far back as you've kept data.
- **Audit log**: every change made through the UI or API, who made it, and what changed.
- **Settings**: change the admin password, manage API tokens, export or import the full set of forwards as
  JSON, and see the gateway's own configuration (interfaces, networks, retention, database size).

## `pmctl`

A command-line equivalent to parts of the dashboard, useful for scripting or a quick check without opening a
browser.

| Command | Docker | Raspberry Pi |
|---|---|---|
| Status summary | `docker exec pm-E pmctl status` | `pmctl status` |
| List forwards | `docker exec pm-E pmctl forwards` | `pmctl forwards` |
| Force a rule rebuild | `docker exec pm-E pmctl reapply` | `pmctl reapply` |
| Show the admin password (until changed) | `docker exec pm-E pmctl password` | `pmctl password` |
| Create an API token | `docker exec pm-E pmctl token <name>` | `pmctl token <name>` |
| Check for / install an update | n/a — see [`updates.md`](updates.md) for Docker | `pmctl update` |

On a native Pi install, `pmctl` needs root (it reads a deliberately root-only internal token) and elevates
itself automatically via `sudo` when not already running as one.

## Logs

**Docker:** `docker logs pm-E` (or `-f` to follow).

**Raspberry Pi:**
```bash
journalctl -u portmirror-manager -u portmirror-base --no-pager -n 400   # recent activity
journalctl -u portmirror-manager -u portmirror-base --no-pager -p warning   # warnings and errors only, all history
```

## A full diagnostic sweep

This is the single most useful command for "something seems off, what's going on" — it pulls service logs,
restart history, live application health, kernel ruleset state, storage, and basic resource usage in one pass.
Run it on the Pi (or inside the gateway container, substituting the paths):

```bash
{
echo "=== service logs: warnings/errors across all history ==="
journalctl -u portmirror-manager -u portmirror-base --no-pager -p warning

echo; echo "=== service logs: recent activity ==="
journalctl -u portmirror-manager -u portmirror-base --no-pager -n 400

echo; echo "=== service status and restart counts (a high count means past crashes) ==="
systemctl status portmirror-base portmirror-manager --no-pager -l
systemctl show portmirror-manager -p NRestarts -p ActiveEnterTimestamp

echo; echo "=== application-level health ==="
pmctl status
pmctl forwards

echo; echo "=== kernel forwarding sanity ==="
sysctl net.ipv4.ip_forward net.ipv4.conf.all.rp_filter
sudo nft list ruleset | grep -E "^table|counter name"

echo; echo "=== state database size ==="
du -sh /var/lib/portmirror/pm.db 2>/dev/null
ls -la /var/lib/portmirror

echo; echo "=== resources ==="
uptime
free -h
vcgencmd measure_temp 2>/dev/null
df -h /
} 2>&1 | tee ~/portmirror-diag.txt
```

### Reading the output

- **Any `-p warning` output at all** is worth looking into — in normal operation there should be none.
- **`NRestarts` above 0** means the manager has crashed and been restarted by systemd since its last full
  start; forwarding itself keeps working through this (the kernel rules don't depend on the manager process
  staying up), but it's worth finding out why.
- **A `systemctl status` "active since" timestamp that looks earlier than the system's actual `uptime`** isn't
  a real problem — it's a known quirk on boards without a battery-backed clock: at boot, before the VPN comes
  up and the clock gets corrected, these two services can start in the brief window where the clock still
  reads a stale value from before shutdown. The services' own log timestamps, written moments later, are
  correct. Harmless, just confusing to read.
- **`nft list ruleset` showing other tables** (commonly `ip filter`, `ip nat`, `ip6 filter`, and similar, often
  with a "managed by iptables-nft, do not touch" warning) is normal on a system that already has its own
  firewall rules from something else — Docker, in particular, creates exactly these. The gateway's own tables
  are uniquely named (`pm_nat`, `pm_filter`, and the dynamic `pm` table) specifically so they never collide
  with whatever else is already on the box.
- **A `ConnectionRefused` from `pmctl` right after restarting the manager** usually just means the command ran
  before the service finished starting (a few seconds, typically) — retry it a moment later. The manager's own
  log line (`"manager ready on :8088 with N forwards"`) is the clearest signal that it's actually up.
- **Memory and swap**: on a small board (an older or Zero-class Pi), seeing some swap in use isn't automatically
  a problem, but persistent heavy swapping is worth watching — it can slow things down and, on an SD-card-based
  system, wear the card over time.
