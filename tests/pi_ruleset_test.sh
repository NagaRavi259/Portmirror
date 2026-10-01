#!/bin/bash
# Behavioural test of deploy-pi/nftables.conf inside gateway E.
#
# deploy-pi/nftables.conf is BASE-ONLY (masquerade + forward filter + UI-port
# protection) - it does not know about any forwards; those live in the
# manager's own `ip pm` table. So this test does exactly what a real Pi boot
# does: flush everything, load the Pi's base ruleset (with interface names
# swapped lan0/vpn0), then have the *running* manager re-apply its own
# forwards into the now-empty kernel (`pmctl reapply`, the same call
# portmirror-base.service's boot sequence relies on via /var/lib/portmirror/pm.nft
# - here we drive it directly via the API instead of a restart, since the
# manager process itself is untouched by any of this).
cd "$(dirname "$0")/.." || exit 1
OUTDIR="${1:-./results}"; mkdir -p "$OUTDIR"
RESULTS="$OUTDIR/results_pi_ruleset.csv"
echo "test_id,category,description,result,detail" > "$RESULTS"
GW=192.168.88.8
PASS_COUNT=0; FAIL_COUNT=0
log() {
  echo "$1,$2,\"$3\",$4,\"$5\"" >> "$RESULTS"
  [ "$4" = PASS ] && PASS_COUNT=$((PASS_COUNT+1)) || FAIL_COUNT=$((FAIL_COUNT+1))
  printf "[%-20s] %-8s %-64s %s\n" "$1" "$2" "$3" "$4"
}
# Hold off the manager's own self-heal while we swap rulesets by hand, then
# restore the stack's base rules + let the manager reapply its forwards again.
restore() {
  docker exec pm-E nft flush ruleset
  docker exec pm-E nft -f /etc/nftables/nftables.conf
  docker exec pm-E pmctl reapply >/dev/null 2>&1
  docker exec pm-E rm -f /run/pm/hold
  docker exec pm-E conntrack -F >/dev/null 2>&1
}
trap restore EXIT
docker exec pm-E touch /run/pm/hold

pi_ruleset() {
  sed -e 's/define LAN_IF   = "eth0"/define LAN_IF   = "lan0"/' -e 's/define VPN_IF   = "wg0"/define VPN_IF   = "vpn0"/' \
    deploy-pi/nftables.conf
}

echo "== Pi base ruleset (lan0/vpn0) loaded into E, manager forwards reapplied on top"
docker exec pm-E nft flush ruleset
pi_ruleset | docker exec -i pm-E nft -f -
docker exec pm-E pmctl reapply >/dev/null
TABLES=$(docker exec pm-E nft list tables | paste -sd' ' -)
[ "$(echo "$TABLES" | tr ' ' '\n' | grep -c '^table')" -eq 3 ] && echo "$TABLES" | grep -q "ip pm$" && R=PASS || R=FAIL
log "PI-LOAD" LOAD "deploy-pi/nftables.conf (base) loads; manager reapply recreates table ip pm on top" $R "$TABLES"

# Idempotent reload: loading the BASE file twice must not duplicate its own
# rules or touch a pre-existing foreign table or the manager's own `ip pm` table.
docker exec pm-E nft add table ip foreign_fw
DNAT_BEFORE=$(docker exec pm-E nft -j list table ip pm | python3 -c "import json,sys; print(sum(1 for i in json.load(sys.stdin)['nftables'] if 'rule' in i and i['rule'].get('chain')=='prerouting'))")
pi_ruleset | docker exec -i pm-E nft -f -
pi_ruleset | docker exec -i pm-E nft -f -
DNAT_AFTER=$(docker exec pm-E nft -j list table ip pm | python3 -c "import json,sys; print(sum(1 for i in json.load(sys.stdin)['nftables'] if 'rule' in i and i['rule'].get('chain')=='prerouting'))")
MASQ_RULES=$(docker exec pm-E nft -j list table ip pm_nat | python3 -c "import json,sys; print(sum(1 for i in json.load(sys.stdin)['nftables'] if 'rule' in i))")
docker exec pm-E nft list table ip foreign_fw >/dev/null 2>&1 && FOREIGN=kept || FOREIGN=wiped
[ "$DNAT_AFTER" = "$DNAT_BEFORE" ] && [ "$MASQ_RULES" -eq 1 ] && [ "$FOREIGN" = kept ] && R=PASS || R=FAIL
log "PI-RELOAD-IDEMPOTENT" LOAD "Reloading the base file twice: no dupes, foreign table and ip pm untouched" $R \
  "manager_dnat_rules=$DNAT_BEFORE->$DNAT_AFTER masquerade_rules=$MASQ_RULES foreign_table=$FOREIGN"
docker exec pm-E nft delete table ip foreign_fw

docker exec pm-E conntrack -F >/dev/null 2>&1
OUT=$(docker exec pm-F bash /opt/tests/client_test_suite.sh $GW 2>&1 | grep -oP 'Passed: \d+   Failed: \d+')
echo "$OUT" | grep -q "Failed: 0" && R=PASS || R=FAIL
log "PI-CLIENT-SUITE" SUITE "Full client suite from F against the Pi base ruleset + manager's own forwards" $R "$OUT"

PID=$(docker inspect -f '{{.State.Pid}}' pm-A)
nsenter -t "$PID" -n timeout 4 tcpdump -i any -nn -l "tcp port 80" > /tmp/pi_masq.$$ 2>/dev/null & sleep 1
docker exec pm-F curl -s -o /dev/null --max-time 3 http://$GW/; sleep 3
REAL=$(grep -c 192.168.88.67 /tmp/pi_masq.$$); GWS=$(grep -c 10.0.0.88 /tmp/pi_masq.$$); rm -f /tmp/pi_masq.$$
[ "$REAL" -eq 0 ] && [ "$GWS" -gt 0 ] && R=PASS || R=FAIL
log "PI-MASQ" MASQ "A sees only 10.0.0.88, never F's IP" $R "real_ip_pkts=$REAL gw_ip_pkts=$GWS"

docker exec pm-F ip route add 10.0.0.0/24 via $GW
CODE=$(docker exec pm-F curl -s --max-time 3 -o /dev/null -w "%{http_code}" http://10.0.0.12/ 2>/dev/null)
docker exec pm-F ip route del 10.0.0.0/24 via $GW
[ "$CODE" != "200" ] && R=PASS || R=FAIL
log "PI-NOT-A-ROUTER" SECURITY "Routing to 10.0.0.12 via E without DNAT is dropped" $R "http_code=$CODE"

echo " Pi ruleset: Passed: $PASS_COUNT   Failed: $FAIL_COUNT"
