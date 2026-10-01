#!/bin/bash
# Bring the topology up (if needed) and run every test suite.
#   ./run_all.sh            full run
#   ./run_all.sh --down     tear down afterwards
# Results land in ./results/ (per-suite CSVs + summary.txt).
cd "$(dirname "$0")" || exit 1
RES=./results
set -a; [ -f .env ] && . ./.env; set +a
export PM_API="http://${PM_UI_BIND:-127.0.0.1}:8088" PM_URL="http://${PM_UI_BIND:-127.0.0.1}:8088"
mkdir -p "$RES"
rm -f "$RES"/results_*.csv "$RES"/summary.txt

docker compose up -d --build --wait --wait-timeout 180 >/dev/null 2>&1 || docker compose up -d --build
for i in $(seq 1 60); do curl -sf -m 2 "$PM_API/api/health" >/dev/null && break; sleep 2; done
# the demo traffic generator would skew exact-count tests: pause it, resume afterwards if it was running
TRAFFIC_WAS_UP=$(docker ps -q -f name=^pm-traffic$)
[ -n "$TRAFFIC_WAS_UP" ] && docker stop pm-traffic >/dev/null && echo "paused pm-traffic for the test run"

echo "Waiting for the stack to be ready..."
for i in $(seq 1 60); do
  docker exec pm-F curl -s --max-time 2 -o /dev/null http://192.168.88.8:80/ 2>/dev/null \
  && docker exec pm-F python3 -c "import websocket; websocket.create_connection('ws://192.168.88.8:8765/', timeout=2).recv()" 2>/dev/null \
  && docker exec pm-F bash -c "exec 3<>/dev/tcp/192.168.88.8/3389" 2>/dev/null \
  && break
  sleep 2
done

for C in F G; do
  docker exec pm-$C bash /opt/tests/client_test_suite.sh 192.168.88.8
  docker cp pm-$C:/tmp/client_test_results/results_$C.csv "$RES/results_client_$C.csv" >/dev/null
done

bash tests/orchestrator_tests.sh "$RES"
bash tests/pi_ruleset_test.sh "$RES"

# manager: unit tests (inside the gateway, nft dry-runs against its kernel), API integration, browser e2e
UNIT=$(docker exec pm-E sh -c 'cd /opt/pm && python3 -m pytest -q tests 2>&1 | tail -1')
echo " Manager unit tests: $UNIT"
{ echo "test_id,category,description,result,detail"
  echo "UNIT-PYTEST,UNIT,\"pytest: validation, rule generation, nft -c dry-runs, conntrack parsing, store\",$(echo "$UNIT" | grep -qE '^[0-9]+ passed' && echo PASS || echo FAIL),\"$UNIT\""
} > "$RES/results_unit.csv"
python3 -u tests/manager_tests.py "$RES"
bash tests/run_ui_e2e.sh "$RES"

{
  echo "Port-mirroring test run - $(date -Is)"
  for f in "$RES"/results_*.csv; do
    P=$(grep -c ',PASS,' "$f"); F=$(grep -c ',FAIL,' "$f"); S=$(grep -c ',SKIP,' "$f")
    printf "  %-32s pass=%-3s fail=%-3s skip=%s\n" "$(basename "$f")" "$P" "$F" "$S"
  done
  TP=$(cat "$RES"/results_*.csv | grep -c ',PASS,'); TF=$(cat "$RES"/results_*.csv | grep -c ',FAIL,')
  echo "  TOTAL pass=$TP fail=$TF"
  [ "$TF" -gt 0 ] && { echo "  Failures:"; grep -h ',FAIL,' "$RES"/results_*.csv | sed 's/^/    /'; }
} | tee "$RES/summary.txt"

[ -n "$TRAFFIC_WAS_UP" ] && [ "$1" != "--down" ] && docker start pm-traffic >/dev/null && echo "resumed pm-traffic"
[ "$1" = "--down" ] && docker compose down
grep -q 'fail=0$' <(grep TOTAL "$RES/summary.txt")
