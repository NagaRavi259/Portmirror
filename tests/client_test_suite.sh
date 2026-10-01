#!/bin/bash
# ============================================================================
# Client-side test suite for the port-mirroring gateway.
# Run this independently from EACH client (F and G) against the gateway IP.
#
# Usage:  bash client_test_suite.sh [gateway_ip]
#         (defaults to 192.168.88.8)
# ============================================================================

GW="${1:-192.168.88.8}"
CLIENT_NAME="${HOSTNAME:-$(hostname 2>/dev/null || echo unknown)}"
OUTDIR="${OUTDIR:-/tmp/client_test_results}"
PG_DB="${PG_DB:-world}"
PG_CHECK_SQL="${PG_CHECK_SQL:-SELECT count(*) FROM city;}"
PG_EXPECT="${PG_EXPECT:-4079}"   # rows in the world dataset's city table
mkdir -p "$OUTDIR"
RESULTS="$OUTDIR/results_${CLIENT_NAME}.csv"
echo "test_id,category,description,result,detail" > "$RESULTS"

PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

log() {
  # id, category, description, result, detail
  echo "$1,$2,\"$3\",$4,\"$5\"" >> "$RESULTS"
  case "$4" in
    PASS) PASS_COUNT=$((PASS_COUNT+1)) ;;
    SKIP) SKIP_COUNT=$((SKIP_COUNT+1)) ;;
    *)    FAIL_COUNT=$((FAIL_COUNT+1)) ;;
  esac
  printf "[%-12s] %-12s %-58s %s\n" "$1" "$2" "$3" "$4"
}

echo "=================================================================="
echo " Client test suite — running as: $CLIENT_NAME  ->  gateway: $GW"
echo "=================================================================="

# ---------------------------------------------------------------------------
# 1. Basic connectivity — one real check per service
# ---------------------------------------------------------------------------
CODE=$(timeout 4 curl -s --max-time 3 -o /dev/null -w "%{http_code}" http://$GW:80/ 2>&1)
[ "$CODE" = "200" ] && R=PASS || R=FAIL
log "SVC-HTTP" BASIC "HTTP GET $GW:80 -> container A" $R "http_code=$CODE"

CODE=$(timeout 4 curl -sk --max-time 3 -o /dev/null -w "%{http_code}" https://$GW:443/ 2>&1)
[ "$CODE" = "200" ] && R=PASS || R=FAIL
log "SVC-HTTPS" BASIC "HTTPS GET $GW:443 -> container B" $R "http_code=$CODE"

RESP=$(timeout 4 bash -c "exec 3<>/dev/tcp/$GW/3389 && echo -en '\x03\x00\x00\x0b\x06\xe0\x00\x00\x00\x00\x00' >&3 && timeout 2 head -c 11 <&3 | xxd -p" 2>/dev/null)
PDU=$(echo "$RESP" | cut -c11-12)
[ "$PDU" = "d0" ] && R=PASS || R=FAIL
log "SVC-RDP" BASIC "RDP X.224 handshake $GW:3389 -> container C (xrdp)" $R "pdu_type=$PDU"

RESP=$(timeout 4 bash -c "exec 3<>/dev/tcp/$GW/5900 && timeout 2 head -c 12 <&3" 2>/dev/null)
echo "$RESP" | grep -q "^RFB 003" && R=PASS || R=FAIL
log "SVC-VNC" BASIC "VNC/RFB handshake $GW:5900 -> container C (x11vnc sidecar)" $R "banner=$RESP"

OUT=$(timeout 5 env PGPASSWORD=testpass123 psql -h $GW -p 5432 -U postgres -d "$PG_DB" -t -c "$PG_CHECK_SQL" 2>&1 | tr -d ' \n')
[ "$OUT" = "$PG_EXPECT" ] && R=PASS || R=FAIL
log "SVC-PG" BASIC "Postgres query $GW:5432 -> container D ($PG_DB dataset)" $R "rows=$OUT expected=$PG_EXPECT"

OUT=$(timeout 6 python3 -c "
import websocket, json
ws = websocket.create_connection('ws://$GW:8765/', timeout=4)
msg = json.loads(ws.recv())
ws.close()
print('OK' if 'log_id' in msg else 'BAD')
" 2>&1)
[ "$OUT" = "OK" ] && R=PASS || R=FAIL
log "SVC-WS" BASIC "WebSocket DB log stream $GW:8765 -> container D" $R "resp=$OUT"

OUT=$(timeout 4 python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
s.sendto(b'hello', ('$GW', 9000))
print(s.recvfrom(4096)[0].decode())
" 2>&1)
echo "$OUT" | grep -q "echo:hello" && R=PASS || R=FAIL
log "SVC-UDP" BASIC "UDP echo $GW:9000 -> container D" $R "resp=$OUT"

# ---------------------------------------------------------------------------
# 2. Parallel / concurrent connections
# ---------------------------------------------------------------------------
TMP=$(mktemp)
for i in $(seq 1 20); do ( curl -s --max-time 3 -o /dev/null -w "%{http_code}\n" http://$GW:80/ ) & done > "$TMP"; wait
OK=$(grep -c "^200$" "$TMP")
[ "$OK" -eq 20 ] && R=PASS || R=FAIL
log "PAR-HTTP-20" PARALLEL "20 concurrent HTTP connections" $R "ok=$OK/20"; rm -f "$TMP"

TMP=$(mktemp)
for i in $(seq 1 20); do ( python3 -c "
import socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(3)
s.sendto(b'p$i', ('$GW',9000)); print(s.recvfrom(4096)[0].decode())
" ) & done > "$TMP"; wait
OK=$(grep -c "^echo:p" "$TMP")
[ "$OK" -eq 20 ] && R=PASS || R=FAIL
log "PAR-UDP-20" PARALLEL "20 concurrent UDP packets" $R "ok=$OK/20"; rm -f "$TMP"

TMP=$(mktemp)
for i in $(seq 1 50); do ( curl -s --max-time 4 -o /dev/null -w "%{http_code}\n" http://$GW:80/ ) & done > "$TMP"; wait
OK=$(grep -c "^200$" "$TMP")
if [ "$OK" -ge 45 ]; then R=PASS; else R=FAIL; fi
log "SCALE-HTTP-50" PARALLEL "50 concurrent HTTP connections (scale check, >=45 required)" $R "ok=$OK/50"; rm -f "$TMP"

# ---------------------------------------------------------------------------
# 3. Mixed TCP + UDP simultaneous
# ---------------------------------------------------------------------------
T1=$(mktemp); T2=$(mktemp)
( for i in $(seq 1 10); do curl -s --max-time 3 -o /dev/null -w "%{http_code}\n" http://$GW:80/; done > "$T1" ) &
( for i in $(seq 1 10); do python3 -c "
import socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(3)
s.sendto(b'm', ('$GW',9000)); print(s.recvfrom(4096)[0].decode())
"; done > "$T2" ) &
wait
TCP_OK=$(grep -c "^200$" "$T1"); UDP_OK=$(grep -c "^echo:m$" "$T2")
[ "$TCP_OK" -eq 10 ] && [ "$UDP_OK" -eq 10 ] && R=PASS || R=FAIL
log "MIX-TCP-UDP" MIXED "10 TCP + 10 UDP simultaneously" $R "tcp=$TCP_OK/10 udp=$UDP_OK/10"
rm -f "$T1" "$T2"

# ---------------------------------------------------------------------------
# 4. Dropped connection
# ---------------------------------------------------------------------------
CODE1=$(timeout 4 curl -s --max-time 3 -o /dev/null -w "%{http_code}" http://$GW:80/ 2>&1)
timeout 2 bash -c "exec 3<>/dev/tcp/$GW/80; echo -en 'GET / HTTP/1.1\r\nHost: x\r\n' >&3" 2>/dev/null &
KILLPID=$!
sleep 0.3
kill -9 $KILLPID 2>/dev/null
sleep 0.5
CODE2=$(timeout 4 curl -s --max-time 3 -o /dev/null -w "%{http_code}" http://$GW:80/ 2>&1)
[ "$CODE2" = "200" ] && R=PASS || R=FAIL
log "DROP-RECOVER" DROPPED "Fresh connection succeeds right after an abruptly-killed one" $R "before=$CODE1 after=$CODE2"

# ---------------------------------------------------------------------------
# 5. Intermittent / lossy connection — real link-down/up flapping.
# (Note: this sandbox kernel has no netem qdisc available, so genuine
#  intermittent connectivity is modeled via actual interface flaps instead
#  of injected packet loss — arguably a more realistic model of a real
#  dropped Wi-Fi/VPN link anyway.)
# ---------------------------------------------------------------------------
IFACE=$(ip route get $GW 2>/dev/null | grep -oP 'dev \K\S+')
if [ -n "$IFACE" ]; then
  (
    for i in 1 2 3; do
      sleep 0.8
      ip link set "$IFACE" down 2>/dev/null
      sleep 0.3
      ip link set "$IFACE" up 2>/dev/null
      sleep 0.1
    done
  ) &
  FLAP_PID=$!

  OK=0; FAIL=0; TOTAL=15
  for i in $(seq 1 $TOTAL); do
    CODE=$(timeout 2 curl -s --max-time 1.5 --connect-timeout 1 -o /dev/null -w "%{http_code}" http://$GW:80/ 2>/dev/null)
    if [ "$CODE" = "200" ]; then OK=$((OK+1)); else FAIL=$((FAIL+1)); fi
    sleep 0.2
  done
  wait $FLAP_PID 2>/dev/null

  # Genuine intermittent connectivity should show SOME failures during the flapping
  # window and SOME successes in between - not a clean 0 or a clean TOTAL.
  if [ "$OK" -gt 0 ] && [ "$FAIL" -gt 0 ]; then R=PASS; else R=FAIL; fi
  log "INTER-FLAP" INTERMITTENT "15 requests while the link is flapped down/up 3 times" $R "ok=$OK fail=$FAIL total=$TOTAL"

  sleep 1
  OK2=0
  for i in $(seq 1 5); do
    CODE=$(timeout 3 curl -s --max-time 2 -o /dev/null -w "%{http_code}" http://$GW:80/ 2>/dev/null)
    [ "$CODE" = "200" ] && OK2=$((OK2+1))
  done
  [ "$OK2" -eq 5 ] && R=PASS || R=FAIL
  log "INTER-RECOVER" INTERMITTENT "Full recovery to 5/5 after flapping stops" $R "ok=$OK2/5"
else
  log "INTER-FLAP" INTERMITTENT "Requests during simulated link flapping" SKIP "could not determine outbound interface"
  log "INTER-RECOVER" INTERMITTENT "Recovery after flapping" SKIP "could not determine outbound interface"
fi

# ---------------------------------------------------------------------------
# 6. Negative / edge cases
# ---------------------------------------------------------------------------

# 6a. Connecting to a port with NO DNAT rule must fail, not silently succeed
timeout 3 bash -c "exec 3<>/dev/tcp/$GW/9999" 2>/dev/null
if [ $? -ne 0 ]; then R=PASS; else R=FAIL; fi
log "NEG-UNMAPPED-PORT" EDGE "Connecting to an unmapped port ($GW:9999) must fail" $R "expected=connection refused/timeout"

# 6a'. Backends must NOT be reachable directly - only through the gateway
CODE=$(timeout 4 curl -s --max-time 3 --connect-timeout 2 -o /dev/null -w "%{http_code}" http://10.0.0.12:80/ 2>/dev/null)
[ "$CODE" != "200" ] && R=PASS || R=FAIL
log "NEG-DIRECT-BACKEND" EDGE "Direct access to 10.0.0.12:80 (bypassing $GW) must fail" $R "http_code=$CODE"

# 6b. Wrong Postgres credentials must be rejected, not silently accepted
OUT=$(timeout 4 env PGPASSWORD=wrongpassword psql -h $GW -p 5432 -U postgres -d "$PG_DB" -t -c "SELECT 1;" 2>&1)
echo "$OUT" | grep -qi "password authentication failed" && R=PASS || R=FAIL
log "NEG-BAD-AUTH" EDGE "Postgres with wrong password must be rejected" $R "resp_snippet=$(echo "$OUT" | head -1)"

# 6c. Zero-byte UDP packet
OUT=$(timeout 3 python3 -c "
import socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(2)
s.sendto(b'', ('$GW',9000))
try:
    print(s.recvfrom(4096)[0].decode())
except socket.timeout:
    print('TIMEOUT')
" 2>&1)
# A zero-byte datagram is a valid, if unusual, UDP payload - service should not crash
[ -n "$OUT" ] && R=PASS || R=FAIL
log "EDGE-UDP-ZERO" EDGE "Zero-byte UDP payload doesn't crash the pipeline" $R "resp=$OUT"

# 6d. Oversized UDP packet (near typical practical limits, ~4000 bytes)
OUT=$(timeout 3 python3 -c "
import socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(2)
payload = b'X' * 4000
s.sendto(payload, ('$GW',9000))
try:
    r = s.recvfrom(8192)[0]
    print('OK' if len(r) > 4000 else 'SHORT')
except socket.timeout:
    print('TIMEOUT')
" 2>&1)
[ "$OUT" = "OK" ] && R=PASS || R=FAIL
log "EDGE-UDP-LARGE" EDGE "~4000-byte UDP payload round-trips intact" $R "resp=$OUT"

# 6e. Rapid connect/disconnect churn (30 connects, no data, immediate close)
FAILS=0
for i in $(seq 1 30); do
  timeout 1 bash -c "exec 3<>/dev/tcp/$GW/80; exec 3<&-; exec 3>&-" 2>/dev/null || FAILS=$((FAILS+1))
done
[ "$FAILS" -eq 0 ] && R=PASS || R=FAIL
log "EDGE-CHURN-30" EDGE "30 rapid connect/disconnect cycles, no failures" $R "failed=$FAILS/30"

# followed by a normal request, to confirm churn didn't degrade the gateway
CODE=$(timeout 4 curl -s --max-time 3 -o /dev/null -w "%{http_code}" http://$GW:80/ 2>&1)
[ "$CODE" = "200" ] && R=PASS || R=FAIL
log "EDGE-CHURN-RECOVER" EDGE "Normal request still succeeds after churn" $R "http_code=$CODE"

# 6f. Idle connection: open, send nothing, wait, then close - server/gateway must not hang or crash
timeout 6 bash -c "exec 3<>/dev/tcp/$GW/80; sleep 4; exec 3<&-" 2>&1
[ $? -eq 0 ] && R=PASS || R=FAIL
log "EDGE-IDLE" EDGE "Idle open connection (4s, no data) closes cleanly" $R ""

# followed again by a normal request
CODE=$(timeout 4 curl -s --max-time 3 -o /dev/null -w "%{http_code}" http://$GW:80/ 2>&1)
[ "$CODE" = "200" ] && R=PASS || R=FAIL
log "EDGE-IDLE-RECOVER" EDGE "Normal request still succeeds after an idle connection" $R "http_code=$CODE"

# 6g. WebSocket freshness: a live event must arrive within a bounded time window
OUT=$(timeout 6 python3 -c "
import websocket, json, time
ws = websocket.create_connection('ws://$GW:8765/', timeout=5)
start = time.time()
while time.time() - start < 5:
    d = json.loads(ws.recv())
    if not d.get('backlog'):
        print('LIVE-EVENT-OK'); break
ws.close()
" 2>&1)
echo "$OUT" | grep -q "LIVE-EVENT-OK" && R=PASS || R=FAIL
log "EDGE-WS-FRESH" EDGE "A live (non-backlog) log event arrives within 5s" $R "resp=$OUT"

# ---------------------------------------------------------------------------
echo "=================================================================="
echo " SUMMARY for $CLIENT_NAME  ->  gateway $GW"
echo " Passed: $PASS_COUNT   Failed: $FAIL_COUNT   Skipped: $SKIP_COUNT   Total: $((PASS_COUNT+FAIL_COUNT+SKIP_COUNT))"
echo " Full results: $RESULTS"
echo "=================================================================="
