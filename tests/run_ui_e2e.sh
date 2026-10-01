#!/bin/bash
# Run the browser e2e test in the official Playwright image (host networking:
# reaches the UI on 127.0.0.1:8088 and the LAN side of the gateway as 192.168.88.1).
cd "$(dirname "$0")/.." || exit 1
OUT="$(cd "${1:-./results}" && pwd)"
PW=$(docker exec pm-E pmctl password)
if [ "$PW" = "initial password already changed" ]; then
  echo "SKIP: initial admin password was changed; set PM_PASSWORD to run the UI test" >&2
  PW="${PM_PASSWORD:?}"
fi
docker run --rm --network host --ipc=host \
  -e PM_PASSWORD="$PW" -e PM_URL="${PM_URL:-http://127.0.0.1:8088}" -e OUT=/out \
  -v "$PWD/tests:/tests:ro" -v "$OUT:/out" \
  mcr.microsoft.com/playwright/python:v1.49.1-noble \
  bash -c "pip install -q playwright==1.49.1 2>/dev/null; python3 /tests/ui_e2e.py"
