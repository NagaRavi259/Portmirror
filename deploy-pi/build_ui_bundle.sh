#!/bin/bash
# Builds the manager's web UI into ../manager/ui/dist, using a throwaway
# Node container - run this on any machine with Docker (it does NOT need to
# be the Pi; the Pi never needs Node.js at all). Then copy this whole
# directory (this dist/ included) to the Pi, e.g.:
#
#   bash deploy-pi/build_ui_bundle.sh
#   rsync -az --exclude results --exclude manager/ui/node_modules \
#         /root/tools/port-mirroring/ pi@<pi-ip>:/opt/portmirror-src/
#   ssh pi@<pi-ip> 'cd /opt/portmirror-src/deploy-pi && sudo ./install.sh'
set -euo pipefail
cd "$(dirname "$0")/../manager/ui"
docker run --rm -v "$PWD:/ui" -w /ui node:22-alpine sh -c 'npm ci --no-audit --no-fund --loglevel=error || npm install --no-audit --no-fund --loglevel=error; npx vite build'
echo "Built: $(du -sh dist | cut -f1) in $(pwd)/dist"
