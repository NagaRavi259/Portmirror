# Portmirror

A Linux gateway that replaces per-service SSH reverse tunnels with a proper, managed DNAT/masquerade setup.
Devices on your local network reach services on a remote network — across an existing VPN tunnel — through
fixed local ports, without knowing or caring that those services aren't actually local. Forwards are managed
live through a web dashboard: add, edit, or remove a port mapping in seconds, no manual firewall editing, no
SSH session to keep alive.

```
 Local network                    Gateway                      VPN tunnel           Remote network
 192.168.1.0/24                                                                      10.0.0.0/24

 Local device  ───────────▶  LAN side : local port   ─────────────────────▶   Remote service
                             VPN side : masquerades the connection as its own address
                             (the remote side never sees the local device's real IP)
```

## Why this exists

The common alternative — an SSH reverse tunnel per service — works until it doesn't: it's TCP-only, needs a
new forward for every port, and quietly dies the moment the SSH session drops. This gateway uses the Linux
kernel's own packet filter (`nftables`) instead: DNAT rewrites the destination of inbound connections, and
masquerade on the VPN-facing interface rewrites the source on the way out, so the remote side always has
somewhere to send its reply — the one problem that breaks a naive DNAT-only setup. See
[`docs/architecture.md`](docs/architecture.md) for the full design.

## Features

- **TCP, UDP, or both**, per forward, with optional port ranges mapped 1:1
- **Live management** through a web UI and a REST API — add, edit, disable, or delete a forward without
  restarting anything or dropping other traffic in flight
- **Per-forward controls**: allowed source addresses, a new-connection rate limit, a concurrent-connection
  cap, and an auto-expiry time
- **Live monitoring**: throughput, new-connection rate, live connection list (with per-connection kill),
  target health checks, and history going back as far as you want to keep it
- **Delete/disable asks first** whether to let existing connections finish or cut them immediately
- **Self-healing**: if the kernel rules ever drift from what's configured, they're repaired automatically;
  if the management process itself crashes, forwarding keeps working while it restarts
- **Admin login, API tokens, and an audit log** of every change, with configurable retention
- Runs in Docker, or natively on a Raspberry Pi with no Docker at all

## Quick start

**Docker** (the fastest way to try it, and the easiest to tear down):
```bash
docker compose up -d --build
```
See [`docs/installation-docker.md`](docs/installation-docker.md) for configuration and first login.

**Raspberry Pi** (a plain Python service under systemd, no containers):
See [`docs/installation-raspberry-pi.md`](docs/installation-raspberry-pi.md) for the full walkthrough.

Either way, once it's running: open the dashboard, sign in, change the generated password, and add your
first forward.

## Project layout

| Path | What's there |
|---|---|
| `manager/app/` | The backend: a FastAPI service that turns forward definitions into `nftables` rules, collects live metrics, probes target health, and serves the API |
| `manager/ui/` | The web dashboard (React + TypeScript), built into the gateway image |
| `manager/tests/` | Unit tests for the backend |
| `images/` | Docker build contexts for the gateway and every test-topology service |
| `nftables/` | The gateway's static base ruleset (masquerade, forward filtering, UI-port protection) |
| `deploy-pi/` | Everything needed for a native (no-Docker) install on a Raspberry Pi: ruleset, systemd units, install script |
| `tests/` | Integration and end-to-end test suites that exercise a running deployment |
| `docker-compose.yml`, `run_all.sh` | Bring up the full Docker test topology and run every test suite against it |

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — how the gateway actually works, in detail
- [`docs/installation-docker.md`](docs/installation-docker.md) — Docker setup and configuration
- [`docs/installation-raspberry-pi.md`](docs/installation-raspberry-pi.md) — native Raspberry Pi setup
- [`docs/monitoring.md`](docs/monitoring.md) — using the dashboard, `pmctl`, and diagnosing problems
- [`docs/updates.md`](docs/updates.md) — how releases are cut and how a deployment updates to one
- [`docs/testing.md`](docs/testing.md) — the test suites and how to run them
- [`docs/progress.md`](docs/progress.md) — what's validated, what isn't yet
- [`docs/fixed-issues.md`](docs/fixed-issues.md) — real bugs found during development and deployment, and how they were fixed
- [`docs/roadmap.md`](docs/roadmap.md) — planned features and ideas under consideration
