# Testing

## Suites

| Suite | What it covers | Where |
|---|---|---|
| Unit tests | Rule generation, validation logic, the `nftables` script generator (dry-run checked against a real kernel), conntrack-output parsing, the data store, retention | `manager/tests/test_unit.py` |
| Client suite | Every service type reachable through the gateway, parallel connections, mixed TCP+UDP, dropped/intermittent connections, basic negative cases | `tests/client_test_suite.sh`, run from inside a client container |
| Orchestrator suite | Source-IP mirroring (captured on the backend's own interface), the core DNAT-without-masquerade failure reproduced and fixed, port-scan scoping, IPv6 handling, recovery from a gateway/backend restart, sustained mixed-protocol load | `tests/orchestrator_tests.sh`, run from the host |
| Manager integration suite | Every forward operation against real traffic: create/edit/toggle/delete, drain vs. cut, rate and connection limits, expiry, atomic rollback on a bad configuration, byte/connection counters, crash recovery, kernel-drift repair, isolation between forwards under concurrent changes | `tests/manager_tests.py` |
| UI end-to-end suite | A full browser pass through login, creating a forward with live traffic, the live connection view, cutting a connection, the delete/disable prompts, the audit log, API tokens, and mobile layout | `tests/ui_e2e.py` |
| Raspberry Pi ruleset suite | The actual Pi-targeted ruleset, loaded into a real gateway and exercised with the full client suite, including an idempotent-reload check | `tests/pi_ruleset_test.sh` |

## Running everything

```bash
./run_all.sh            # build, start, wait for readiness, run every suite, summarize
./run_all.sh --down     # the same, then tear the stack down afterwards
```

Results land in `results/` as one CSV per suite plus a combined `summary.txt`; UI test screenshots go to
`results/ui/`.

To run a single suite:
```bash
docker exec pm-F bash /opt/tests/client_test_suite.sh 192.168.88.8   # from a client container
bash tests/orchestrator_tests.sh ./results                           # from the host (needs root — uses nsenter/tcpdump)
```

The orchestrator and manager-integration suites accept environment variables to adjust their load/timing
parameters (`SCALE_WORKERS`, `SCALE_DURATION`, `UDP_IDLE_S`) — see the top of each script.

## Current coverage

Last verified with a full clean-slate rebuild: **124 result rows, all passing** — 41 unit tests, 24+24 client
checks, 23 orchestrator checks, 33 manager integration checks, 14 UI checks, and 5 Raspberry Pi ruleset checks.
These numbers move as suites gain or lose individual checks; treat them as a snapshot, not a permanent count,
and re-run `run_all.sh` for a current figure.
