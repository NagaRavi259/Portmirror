# Installation: Docker

This brings up the gateway plus a full test topology (sample backend services and client containers), useful
for trying the system out or running the test suites. For a real deployment on a Raspberry Pi with no test
scaffolding, see [`installation-raspberry-pi.md`](installation-raspberry-pi.md) instead.

## Prerequisites

- Docker with Compose v2 (`docker compose`, not the older standalone `docker-compose`)
- A host that isn't already using the `10.0.0.0/24` or `192.168.88.0/24` subnets, which the compose file
  assigns to its two internal networks

## Bring it up

```bash
docker compose up -d --build
```

This builds and starts:
- the gateway (`nftables` + the manager service, API/UI on port 8088)
- sample backend services (HTTP, HTTPS, RDP, a Postgres database, a UDP echo service, a live log stream over
  WebSocket)
- two client containers for testing connectivity from the "local" side

## Configuration

Copy `.env.example` to `.env` and adjust as needed:

```bash
cp .env.example .env
```

The settings that matter most:

| Variable | Default | Purpose |
|---|---|---|
| `PM_UI_BIND` | `127.0.0.1` | Which host address the dashboard is published on. Keep this to localhost or a private network address (e.g. a Tailscale IP) — never the public interface |
| `PM_ADMIN_PASSWORD` | unset (random) | Set a fixed admin password instead of a generated one, used only on first start |
| `PM_HISTORY_RETENTION_DAYS` | `0` (forever) | How long to keep traffic history |
| `PM_AUDIT_RETENTION_DAYS` | `0` (forever) | How long to keep the audit log |

Changes to `.env` take effect on `docker compose up -d` (no rebuild needed unless you've also changed code).

## First login

```bash
docker exec pm-E pmctl password
```

prints the generated admin password (only until you change it). Open the dashboard at whatever `PM_UI_BIND`
resolves to, port 8088 — e.g. `http://127.0.0.1:8088` with the default, or tunnel in with
`ssh -L 8088:localhost:8088 <host>` if you're connecting from elsewhere. Sign in as `admin`, and change the
password immediately.

No forwards are pre-configured for a from-scratch setup beyond the sample topology's own seeded forwards
(see `manager/seed.json`) — remove or replace them with your own through the dashboard.

## Everyday operations

- `docker exec pm-E pmctl status` — a quick health summary (forwards active, live connections, conntrack usage)
- `docker exec pm-E pmctl forwards` — list configured forwards from the command line
- `docker exec pm-E pmctl reapply` — force the kernel ruleset to be rebuilt from the current configuration
- `docker logs pm-E` — the gateway's own startup and runtime logs

## Demo traffic (optional)

A separate container can drive continuous, realistic mixed traffic through the configured forwards — useful
for seeing the dashboard's charts move without needing real clients:

```bash
docker compose --profile traffic up -d traffic_gen
```

Scale it with `TRAFFIC_LEVEL` in `.env` (default `1.0`), or stop it with `docker compose stop traffic_gen`.
It's excluded from the default `docker compose up` on purpose, and the test suites pause it automatically if
it's running, since it would otherwise throw off exact-count checks.

## Tearing down

```bash
docker compose down          # stop everything, keep the stored forwards/history
docker compose down -v       # also remove the stored database and history
docker compose down -v --rmi all --remove-orphans   # remove images too
```

If you've also started the demo traffic generator, include its profile in the teardown
(`docker compose --profile traffic down ...`) or stop it first — a plain `docker compose down` doesn't manage
services outside the active profile set, so a running `traffic_gen` container would otherwise be left behind
holding the network and image in use.
