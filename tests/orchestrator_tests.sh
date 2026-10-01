#!/bin/bash
# ============================================================================
# Host-side tests for the port-mirroring gateway. Run on the Docker host.
# Covers what a client can't see from inside: packet captures on backends,
# the DNAT-only negative control, security scoping, and container-level
# equivalents of the reboot / VPN-reconnect / remote-restart scenarios.
#
# Usage: bash tests/orchestrator_tests.sh [results_dir]
#   UDP_IDLE_S (default 35)  SCALE_WORKERS (default 50/client)  SCALE_DURATION (default 120)
# ============================================================================

cd "$(dirname "$0")/.." || exit 1
OUTDIR="${1:-./results}"
mkdir -p "$OUTDIR"
RESULTS="$OUTDIR/results_orchestrator.csv"
echo "test_id,category,description,result,detail" > "$RESULTS"
CAPDIR=$(mktemp -d)

GW=192.168.88.8
VPN_NET=portmirror_vpn_net
UDP_IDLE_S="${UDP_IDLE_S:-35}"
SCALE_WORKERS="${SCALE_WORKERS:-50}"
SCALE_DURATION="${SCALE_DURATION:-120}"
PASS_COUNT=0; FAIL_COUNT=0

log() {
  echo "$1,$2,\"$3\",$4,\"${5//\"/\'}\"" >> "$RESULTS"
  [ "$4" = PASS ] && PASS_COUNT=$((PASS_COUNT+1)) || FAIL_COUNT=$((FAIL_COUNT+1))
  printf "[%-20s] %-10s %-66s %s\n" "$1" "$2" "$3" "$4"
}

http_code() {  # client [url] [max_time]
  docker exec "$1" curl -s --max-time "${3:-4}" -o /dev/null -w "%{http_code}" "${2:-http://$GW:80/}" 2>/dev/null
}

wait_http() {  # client timeout_s -> 0 once a 200 comes back
  local end=$((SECONDS + $2))
  while [ $SECONDS -lt $end ]; do
    [ "$(http_code "$1" http://$GW:80/ 2)" = "200" ] && return 0
    sleep 1
  done
  return 1
}

# Start tcpdump inside a container's network namespace (from the host).
capture_start() {  # container filter file seconds
  local pid; pid=$(docker inspect -f '{{.State.Pid}}' "$1")
  nsenter -t "$pid" -n timeout "$4" tcpdump -i any -nn -l "$2" > "$3" 2>/dev/null &
  sleep 1
}

# Tests that swap rulesets hold off the manager's self-heal (/run/pm/hold),
# then restore base rules + the manager's last-applied forwards.
hold_manager() { docker exec pm-E touch /run/pm/hold; }
restore_gateway() {
  docker exec pm-E nft -f /etc/nftables/nftables.conf >/dev/null 2>&1
  docker exec pm-E nft -f /state/pm.nft >/dev/null 2>&1
  docker exec pm-E rm -f /run/pm/hold
  docker exec pm-E conntrack -F >/dev/null 2>&1
}
trap 'restore_gateway; rm -rf "$CAPDIR"' EXIT

echo "=================================================================="
echo " Orchestrator tests  ->  gateway $GW"
echo "=================================================================="

# ---------------------------------------------------------------------------
# 1. Masquerade / source-IP mirroring
# ---------------------------------------------------------------------------
for C in F G; do
  CLIENT_IP=$(docker exec pm-$C hostname -i | awk '{print $1}')
  capture_start pm-A "tcp port 80" "$CAPDIR/masq_$C" 4
  http_code pm-$C >/dev/null
  sleep 3
  REAL=$(grep -c "$CLIENT_IP" "$CAPDIR/masq_$C")
  GWSEEN=$(grep -c "10.0.0.88" "$CAPDIR/masq_$C")
  [ "$REAL" -eq 0 ] && [ "$GWSEEN" -gt 0 ] && R=PASS || R=FAIL
  log "MASQ-CAP-$C" MASQUERADE "Capture on A: $C's IP ($CLIENT_IP) never seen, only 10.0.0.88" $R "real_ip_pkts=$REAL gw_ip_pkts=$GWSEEN"

  ADDR=$(docker exec -e PGPASSWORD=testpass123 pm-$C psql -h $GW -U postgres -d world -tAc \
         "SELECT client_addr FROM pg_stat_activity WHERE pid = pg_backend_pid()" 2>&1)
  [ "$ADDR" = "10.0.0.88" ] && R=PASS || R=FAIL
  log "MASQ-PG-$C" MASQUERADE "Postgres's own view of $C's session: client_addr = 10.0.0.88" $R "client_addr=$ADDR"
done

docker exec pm-F python3 -c "
import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(3)
s.sendto(b'masq-probe', ('$GW',9000)); s.recvfrom(4096)" 2>/dev/null
LAST=$(docker logs --tail 1 pm-D_udp 2>&1)
echo "$LAST" | grep -q "from 10.0.0.88:" && R=PASS || R=FAIL
log "MASQ-UDP" MASQUERADE "UDP echo server's view of the sender is 10.0.0.88" $R "$LAST"

# ---------------------------------------------------------------------------
# 2. The design report's proof, reproduced: DNAT-only fails, DNAT+masq works
# ---------------------------------------------------------------------------
hold_manager
docker exec pm-E nft -f /etc/nftables/nftables-dnat-only.conf
docker exec pm-E conntrack -F >/dev/null 2>&1
capture_start pm-A "tcp port 80" "$CAPDIR/proof_a" 7
CODE=$(http_code pm-F http://$GW:80/ 5)
sleep 2
SYN_FROM_CLIENT=$(grep -c "192.168.88.67.* > 10.0.0.12.80: Flags \[S\]" "$CAPDIR/proof_a")
SYNACK_TO_CLIENT=$(grep -c "10.0.0.12.80 > 192.168.88.67.*Flags \[S\.\]" "$CAPDIR/proof_a")
[ "$CODE" != "200" ] && [ "$SYN_FROM_CLIENT" -gt 0 ] && R=PASS || R=FAIL
log "PROOF-A-DNAT-ONLY" PROOF "DNAT without masquerade: SYN reaches A with real client IP, reply is lost" $R "http_code=$CODE syn_from_real_ip=$SYN_FROM_CLIENT synack_attempts=$SYNACK_TO_CLIENT"

restore_gateway
capture_start pm-A "tcp port 80" "$CAPDIR/proof_b" 4
CODE=$(http_code pm-F)
sleep 3
HANDSHAKE=$(grep -c "10.0.0.12.80 > 10.0.0.88.*Flags \[S\.\]" "$CAPDIR/proof_b")
[ "$CODE" = "200" ] && [ "$HANDSHAKE" -gt 0 ] && R=PASS || R=FAIL
log "PROOF-B-DNAT-MASQ" PROOF "DNAT + masquerade: full handshake to 10.0.0.88, HTTP 200" $R "http_code=$CODE synack_to_gw=$HANDSHAKE"

# ---------------------------------------------------------------------------
# 3. Security scoping
# ---------------------------------------------------------------------------
# SEC-01: a source outside 192.168.88.0/24 must not be forwarded.
# Evidence comes from the gateway itself: the manager's DNAT counters (they only
# count flows that were actually forwarded) and conntrack. A capture at A can't
# tell a leaked flow from the manager's own health probes, since both arrive
# masqueraded as 10.0.0.88.
dnat_hits() { docker exec pm-E nft -j list counters table ip pm | python3 -c "
import json,sys; print(sum(i['counter']['packets'] for i in json.load(sys.stdin)['nftables']
                           if 'counter' in i and i['counter']['name'].endswith('_new')))"; }
H0=$(dnat_hits)
docker exec pm-F ip addr add 192.168.77.5/32 dev eth0
CODE=$(docker exec pm-F curl -s --interface 192.168.77.5 --max-time 3 -o /dev/null -w "%{http_code}" http://$GW:80/ 2>/dev/null)
LEAK=$(docker exec pm-E conntrack -L -s 192.168.77.5 2>/dev/null | grep -c "src=10.0.0.")
docker exec pm-F ip addr del 192.168.77.5/32 dev eth0
H1=$(dnat_hits)
[ "$CODE" != "200" ] && [ "$LEAK" -eq 0 ] && [ "$H0" = "$H1" ] && R=PASS || R=FAIL
log "SEC-01-UNTRUSTED-SRC" SECURITY "Source 192.168.77.5 (outside trusted LAN) is never DNAT'ed/forwarded" $R "http_code=$CODE dnat_hits=$H0->$H1 conntrack_to_backend=$LEAK"

# SEC-02: only the mapped ports answer on the gateway's LAN IP.
OPEN=$(docker exec pm-F nmap -Pn -n -p- -T4 --open $GW 2>/dev/null | awk '/\/tcp +open/ {split($1,a,"/"); print a[1]}' | sort -n | paste -sd, -)
[ "$OPEN" = "80,443,3389,5432,5900,8088,8765" ] && R=PASS || R=FAIL
log "SEC-02-PORTSCAN-TCP" SECURITY "Full TCP scan of $GW: the 6 forwarded ports + manager UI 8088 only" $R "open=$OPEN"

UDPSTATE=$(docker exec pm-F nmap -Pn -n -sU -p 9000 $GW 2>/dev/null | awk '/9000\/udp/ {print $2}')
[ "$UDPSTATE" = "open" ] && R=PASS || R=FAIL
log "SEC-02-PORTSCAN-UDP" SECURITY "UDP 9000 is open through the gateway" $R "state=$UDPSTATE"

# SEC-03: the gateway is not an open router - using it as a next hop for
# 10.0.0.0/24 (instead of the mapped IP:port) must be dropped by the forward filter.
DROP_BEFORE=$(docker exec pm-E nft list chain inet filter forward | grep -oP 'packets \K[0-9]+' | tail -1)
docker exec pm-F ip route add 10.0.0.0/24 via $GW
CODE=$(http_code pm-F http://10.0.0.12:80/ 3)
docker exec pm-F ip route del 10.0.0.0/24 via $GW
DROP_AFTER=$(docker exec pm-E nft list chain inet filter forward | grep -oP 'packets \K[0-9]+' | tail -1)
[ "$CODE" != "200" ] && [ "$DROP_AFTER" -gt "$DROP_BEFORE" ] && R=PASS || R=FAIL
log "SEC-03-NOT-A-ROUTER" SECURITY "Routing to 10.0.0.12 via E (no DNAT) is dropped by forward filter" $R "http_code=$CODE drops=$DROP_BEFORE->$DROP_AFTER"

# ---------------------------------------------------------------------------
# 4. IPv6 - explicit decision: IPv4-only, v6 must not bypass the NAT rules
# ---------------------------------------------------------------------------
V6FWD=$(docker exec pm-E cat /proc/sys/net/ipv6/conf/all/forwarding)
V6GLOBAL=$(for c in pm-E pm-A pm-F; do docker exec $c ip -6 addr show scope global 2>/dev/null; done | grep -c inet6)
docker exec pm-E nft list table inet filter >/dev/null 2>&1 && INET=yes || INET=no
[ "$V6FWD" = "0" ] && [ "$V6GLOBAL" -eq 0 ] && [ "$INET" = yes ] && R=PASS || R=FAIL
log "V6-01" PROTOCOL "IPv6: forwarding off, no global v6 addrs, inet forward filter drops v6" $R "ipv6_forwarding=$V6FWD global_v6_addrs=$V6GLOBAL inet_filter=$INET"

# ---------------------------------------------------------------------------
# 5. Recovery (container equivalents of the hardware-only scenarios)
# ---------------------------------------------------------------------------
# HW-REMOTE-01: restart a backend; the gateway must not wedge.
docker restart pm-A >/dev/null
wait_http pm-F 20 && R=PASS || R=FAIL
log "REC-REMOTE-HTTP" RECOVERY "Backend A restarted: fresh connection via gateway succeeds" $R "waited_up_to=20s"

docker exec -d pm-F python3 -c "
import websocket; ws=websocket.create_connection('ws://$GW:8765/', timeout=30)
while True: ws.recv()"
sleep 1
docker restart pm-ws_log_stream >/dev/null
OUT=""
for i in $(seq 1 15); do
  OUT=$(docker exec pm-F timeout 6 python3 -c "
import websocket, json
ws = websocket.create_connection('ws://$GW:8765/', timeout=5)
while True:
    if not json.loads(ws.recv()).get('backlog'): print('LIVE'); break" 2>/dev/null)
  [ "$OUT" = "LIVE" ] && break
  sleep 1
done
[ "$OUT" = "LIVE" ] && R=PASS || R=FAIL
log "REC-REMOTE-WS" RECOVERY "WS streamer restarted mid-session: new session gets live events" $R "result=$OUT"

# HW-REBOOT-01 / PERSIST-01: restart the gateway; rules must reload by themselves.
docker restart pm-E >/dev/null
sleep 2
RULES=$(docker exec pm-E nft list ruleset 2>/dev/null | grep -c "dnat to\|masquerade")
wait_http pm-F 20 && CF=200 || CF=fail
wait_http pm-G 5 && CG=200 || CG=fail
[ "$RULES" -eq 8 ] && [ "$CF" = 200 ] && [ "$CG" = 200 ] && R=PASS || R=FAIL
log "REC-GW-RESTART" RECOVERY "Gateway restarted: 7 DNAT + masquerade reloaded automatically, F+G OK" $R "nat_rules=$RULES F=$CF G=$CG"

# HW-VPN-01: VPN link drops and returns.
docker network disconnect $VPN_NET pm-E
CODE=$(http_code pm-F http://$GW:80/ 3)
[ "$CODE" != "200" ] && R=PASS || R=FAIL
log "REC-VPN-DOWN" RECOVERY "VPN link removed from E: forwarded traffic fails (not misrouted)" $R "http_code=$CODE"

docker network connect --ip 10.0.0.88 --driver-opt com.docker.network.endpoint.ifname=vpn0 $VPN_NET pm-E
docker exec pm-E conntrack -F >/dev/null 2>&1
wait_http pm-F 15 && R=PASS || R=FAIL
log "REC-VPN-UP" RECOVERY "VPN link back with same iface name: works with no rule reload" $R "iface=vpn0"

# The design report's warning: a renamed tunnel iface breaks the rule silently.
docker network disconnect $VPN_NET pm-E
docker network connect --ip 10.0.0.88 --driver-opt com.docker.network.endpoint.ifname=vpn9 $VPN_NET pm-E
docker exec pm-E conntrack -F >/dev/null 2>&1
sleep 1
M1=$(docker exec pm-E nft list chain ip nat postrouting | grep -oP 'packets \K[0-9]+')
CODE=$(http_code pm-F http://$GW:80/ 4)
M2=$(docker exec pm-E nft list chain ip nat postrouting | grep -oP 'packets \K[0-9]+')
[ "$CODE" != "200" ] && [ "$M1" = "$M2" ] && R=PASS || R=FAIL
log "RISK-IFACE-RENAME" RECOVERY "Tunnel iface renamed (vpn0->vpn9): masquerade stops matching, forwarding breaks" $R "http_code=$CODE masq_counter=$M1->$M2"

docker network disconnect $VPN_NET pm-E
docker network connect --ip 10.0.0.88 --driver-opt com.docker.network.endpoint.ifname=vpn0 $VPN_NET pm-E
restore_gateway
wait_http pm-F 15 && R=PASS || R=FAIL
log "REC-VPN-RESTORE" RECOVERY "Iface name restored to vpn0: service back" $R ""

# ---------------------------------------------------------------------------
# 6. UDP idle behaviour vs. conntrack timeouts (UDP-LOSS-01)
# ---------------------------------------------------------------------------
T_UDP=$(docker exec pm-E cat /proc/sys/net/netfilter/nf_conntrack_udp_timeout)
T_STREAM=$(docker exec pm-E cat /proc/sys/net/netfilter/nf_conntrack_udp_timeout_stream)
OUT=$(docker exec pm-F timeout $((UDP_IDLE_S + 15)) python3 -c "
import socket, time
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(3)
s.sendto(b'a', ('$GW',9000)); r1=s.recvfrom(64)[0]
time.sleep($UDP_IDLE_S)
s.sendto(b'b', ('$GW',9000)); r2=s.recvfrom(64)[0]
print(r1.decode(), r2.decode())" 2>&1)
[ "$OUT" = "echo:a echo:b" ] && R=PASS || R=FAIL
log "UDP-IDLE" UDP "Same UDP socket works again after ${UDP_IDLE_S}s idle (> ${T_UDP}s unreplied timeout)" $R "resp=$OUT udp_timeout=$T_UDP udp_timeout_stream=$T_STREAM"

# ---------------------------------------------------------------------------
# 7. Sustained mixed-protocol load from both clients at once (SCALE-02)
# ---------------------------------------------------------------------------
echo "   ... running ${SCALE_DURATION}s of mixed load, ${SCALE_WORKERS} workers per client"
docker exec pm-F python3 /opt/tests/scale_mixed.py $GW $SCALE_WORKERS $SCALE_DURATION > "$CAPDIR/scale_F" 2>&1 &
docker exec pm-G python3 /opt/tests/scale_mixed.py $GW $SCALE_WORKERS $SCALE_DURATION > "$CAPDIR/scale_G" 2>&1 &
wait
for C in F G; do
  J=$(tail -1 "$CAPDIR/scale_$C")
  read -r OK FAIL < <(echo "$J" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['ok'], d['fail'])" 2>/dev/null)
  if [ -n "$OK" ] && [ $((OK + FAIL)) -gt 0 ] && [ $((FAIL * 1000)) -le $(((OK + FAIL) * 5)) ]; then R=PASS; else R=FAIL; fi
  log "SCALE-MIXED-$C" LOAD "${SCALE_WORKERS} workers x ${SCALE_DURATION}s, 5 protocols, F+G together (>=99.5%)" $R "$J"
done
CT=$(docker exec pm-E conntrack -C 2>/dev/null)
log "SCALE-GW-HEALTH" LOAD "Gateway still serving after load" \
  $([ "$(http_code pm-F)" = "200" ] && echo PASS || echo FAIL) "conntrack_entries=$CT"

echo "=================================================================="
echo " Orchestrator: Passed: $PASS_COUNT   Failed: $FAIL_COUNT   Total: $((PASS_COUNT+FAIL_COUNT))"
echo " Results: $RESULTS"
echo "=================================================================="
