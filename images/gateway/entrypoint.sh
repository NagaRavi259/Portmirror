#!/bin/bash
# Gateway boot sequence - the container equivalent of nftables.service ordered
# After= the VPN unit, plus a supervisor for the manager:
#   1. wait for lan0/vpn0   2. load base ruleset   3. load last-applied forwards
#   4. run the manager, restarting it if it dies (forwarding never depends on it)
RULESET="${NFT_RULESET:-/etc/nftables/nftables.conf}"
BOOT_RULES="${PM_STATE_DIR:-/state}/pm.nft"

for i in $(seq 1 30); do
  ip link show lan0 >/dev/null 2>&1 && ip link show vpn0 >/dev/null 2>&1 && break
  echo "waiting for lan0/vpn0 ($i)..."; sleep 1
done

echo "ip_forward=$(cat /proc/sys/net/ipv4/ip_forward) rp_filter(all)=$(cat /proc/sys/net/ipv4/conf/all/rp_filter) acct=$(cat /proc/sys/net/netfilter/nf_conntrack_acct 2>/dev/null)"
nft -f "$RULESET" || { echo "FATAL: base ruleset failed to load"; exit 1; }
echo "Loaded base ruleset $RULESET"
if [ -f "$BOOT_RULES" ]; then
  nft -f "$BOOT_RULES" && echo "Restored forwards from $BOOT_RULES" || echo "WARN: $BOOT_RULES failed to load; manager will reapply"
fi
mkdir -p /run/pm

MPID=""
trap '[ -n "$MPID" ] && kill -TERM "$MPID" 2>/dev/null; wait "$MPID" 2>/dev/null; exit 0' TERM INT

if [ "${PM_MANAGER:-1}" != "1" ]; then
  sleep infinity & MPID=$!; wait $MPID
fi

cd /opt/pm || exit 1
while true; do
  # HTTPS flags come from the saved switch state, so a restart picks up an HTTPS change or a revert
  TLS_FLAGS="$(python3 -m app.tls flags | tr '\n' ' ')"
  python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PM_UI_PORT:-8088}" --no-access-log --log-level warning $TLS_FLAGS &
  MPID=$!
  wait $MPID
  echo "manager exited (rc=$?) - restarting in 2s; forwarding continues in the kernel"
  sleep 2
done
