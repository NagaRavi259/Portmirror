#!/bin/bash
# Installs the port-mirroring manager natively on a Raspberry Pi (or any
# Debian/Raspberry Pi OS box) - no Docker. Run as root, from this directory,
# AFTER copying this whole repo onto the Pi and building the UI
# bundle on a dev machine (see build_ui_bundle.sh).
#
# Usage: sudo ./install.sh
#
# This script is idempotent: re-running it after editing nftables.conf or
# portmirror.env just refreshes the installed copies and restarts the
# services - it never overwrites an existing /etc/portmirror/portmirror.env.
set -euo pipefail
cd "$(dirname "$0")"

[ "$(id -u)" = 0 ] || { echo "Run as root (sudo ./install.sh)" >&2; exit 1; }

echo "== 1. Confirm before continuing =="
echo "Real interfaces on this Pi:"
ip -br link | sed 's/^/    /'
echo "Real VPN-capable services:"
systemctl list-units --type=service --all 2>/dev/null | grep -Ei 'wg-quick|openvpn|wireguard|tailscaled' | sed 's/^/    /' || echo "    (none found - is the VPN client installed and named differently?)"
if command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale detected - if that's your 'VPN' here, use tailscaled.service (not wg-quick@wg0.service) in the"
  echo "  two systemd unit files, and confirm the route actually exists: ip route get <a remote-subnet address>"
fi
echo
echo "nftables.conf currently defines: LAN_IF=$(grep -oP 'define LAN_IF\s*=\s*"\K[^"]+' nftables.conf), VPN_IF=$(grep -oP 'define VPN_IF\s*=\s*"\K[^"]+' nftables.conf)"
echo "portmirror.env.example currently defines: PM_LAN_IF=$(grep -oP 'PM_LAN_IF=\K.*' portmirror.env.example), PM_VPN_IF=$(grep -oP 'PM_VPN_IF=\K.*' portmirror.env.example)"
read -rp "Do these match your real setup (edit nftables.conf / portmirror.env.example now if not)? [y/N] " ok
[ "$ok" = "y" ] || { echo "Aborted - edit the files above, then re-run."; exit 1; }

if [ ! -d ../manager/ui/dist ]; then
  echo "ERROR: ../manager/ui/dist not found. Build it first (on any machine with Docker):" >&2
  echo "  cd $(cd .. && pwd) && bash deploy-pi/build_ui_bundle.sh" >&2
  exit 1
fi

echo "== 2. System packages =="
apt-get update -qq
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip nftables conntrack iproute2 iputils-ping \
  build-essential libffi-dev curl rsync >/dev/null
for bin in nft conntrack ping python3 curl rsync; do
  command -v "$bin" >/dev/null || { echo "ERROR: $bin still missing after install" >&2; exit 1; }
done

echo "== 3. Application files (/opt/portmirror) =="
install -d /opt/portmirror
rsync -a --delete ../manager/app/ /opt/portmirror/app/
rsync -a --delete ../manager/ui/dist/ /opt/portmirror/ui/
cp ../manager/requirements.txt /opt/portmirror/requirements.txt
install -m 755 update.sh /opt/portmirror/update.sh
install -m 755 pmctl /usr/local/bin/pmctl
# `pmctl update` compares against this. Installing straight from a dev checkout (not a
# release tarball) has no release version to record, so it's marked "dev" - any real
# release will look newer than that and `pmctl update` will offer to move onto one.
[ -f /opt/portmirror/VERSION ] || echo "dev" > /opt/portmirror/VERSION

if [ ! -d /opt/portmirror/venv ]; then
  python3 -m venv /opt/portmirror/venv
fi
/opt/portmirror/venv/bin/pip install -q --upgrade pip
/opt/portmirror/venv/bin/pip install -q -r /opt/portmirror/requirements.txt

echo "== 4. Config (/etc/portmirror) - state (/var/lib/portmirror) =="
install -d /etc/portmirror /var/lib/portmirror
install -m 644 nftables.conf /etc/portmirror/nftables.conf
if [ ! -f /etc/portmirror/portmirror.env ]; then
  install -m 600 portmirror.env.example /etc/portmirror/portmirror.env
  echo "  wrote /etc/portmirror/portmirror.env from the template - review it"
else
  echo "  /etc/portmirror/portmirror.env already exists - left untouched"
fi
install -m 644 99-portmirror.conf /etc/sysctl.d/99-portmirror.conf
sysctl --system >/dev/null

echo "== 5. Syntax-check the ruleset (does not apply it) =="
nft -c -f /etc/portmirror/nftables.conf

echo "== 6. systemd units =="
install -m 644 portmirror-base.service /etc/systemd/system/portmirror-base.service
install -m 644 portmirror-manager.service /etc/systemd/system/portmirror-manager.service
systemctl daemon-reload
systemctl enable --now portmirror-base.service
systemctl enable --now portmirror-manager.service

echo
echo "== Done =="
sleep 2
systemctl --no-pager --lines=5 status portmirror-base.service portmirror-manager.service || true
echo
GW_LAN_IP=$(grep -oP 'PM_GW_LAN_IP=\K.*' /etc/portmirror/portmirror.env)
UI_PORT=$(grep -oP 'PM_UI_PORT=\K.*' /etc/portmirror/portmirror.env)
echo "Open http://${GW_LAN_IP}:${UI_PORT} from a LAN device."
echo "Admin password: run 'pmctl password' on this Pi."
echo "Add your first forward through the UI - none are seeded by default."
echo "Check for updates any time with 'pmctl update'."
