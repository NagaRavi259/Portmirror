#!/bin/bash
# Checks GitHub for a newer release and, with confirmation, installs it. Installed
# alongside the application at /opt/portmirror/update.sh by install.sh (and refreshed
# by every later update, so this script itself stays current); `pmctl update` runs it.
#
# Usage:
#   pmctl update          check, show what's new, ask, then install if confirmed
#   pmctl update check    check only - never downloads or changes anything.
#                         Exit code: 0 = already up to date, 2 = an update is available
#   pmctl update --yes    skip the confirmation prompt (for scripted/unattended use)
set -euo pipefail

REPO="NagaRavi259/Portmirror"
INSTALL_DIR="/opt/portmirror"
VERSION_FILE="$INSTALL_DIR/VERSION"
API="https://api.github.com/repos/$REPO/releases/latest"

[ "$(id -u)" = 0 ] || { echo "Run as root (sudo pmctl update)" >&2; exit 1; }

current="$(cat "$VERSION_FILE" 2>/dev/null || echo "unknown")"

echo "Checking $REPO for updates..."
release_json="$(curl -fsSL "$API")" || {
  echo "Could not reach GitHub - check this Pi's internet connection." >&2
  exit 1
}
latest="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])' <<<"$release_json")"
asset_url="$(python3 -c '
import json, sys
d = json.load(sys.stdin)
hits = [a["browser_download_url"] for a in d["assets"] if a["name"].endswith(".tar.gz")]
print(hits[0] if hits else "")
' <<<"$release_json")"
notes="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("body") or "(no release notes)")' <<<"$release_json")"

echo "Installed version: $current"
echo "Latest version:    $latest"

if [ "$current" = "$latest" ]; then
  echo "Already up to date."
  exit 0
fi

if [ "${1:-}" = "check" ]; then
  echo
  echo "An update is available. Run 'pmctl update' to install it."
  exit 2
fi

if [ -z "$asset_url" ]; then
  echo "ERROR: release $latest has no .tar.gz asset to install." >&2
  exit 1
fi

echo
echo "What's new in $latest:"
sed 's/^/    /' <<<"$notes"
echo

if [ "${1:-}" != "--yes" ]; then
  read -rp "Install $latest now? Services will restart briefly. [y/N] " ok
  [ "$ok" = "y" ] || { echo "Cancelled."; exit 0; }
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
echo "Downloading $latest..."
curl -fsSL "$asset_url" -o "$tmpdir/release.tar.gz"
tar -xzf "$tmpdir/release.tar.gz" -C "$tmpdir"
release_root="$(find "$tmpdir" -maxdepth 1 -mindepth 1 -type d)"

echo "Backing up the current install to $INSTALL_DIR.bak..."
rm -rf "$INSTALL_DIR.bak"
cp -a "$INSTALL_DIR" "$INSTALL_DIR.bak"

roll_back() {
  echo "ERROR: $1 - rolling back to $current." >&2
  systemctl stop portmirror-manager.service portmirror-base.service 2>/dev/null || true
  rm -rf "$INSTALL_DIR"
  mv "$INSTALL_DIR.bak" "$INSTALL_DIR"
  systemctl start portmirror-base.service portmirror-manager.service
  echo "Rolled back. The update to $latest was NOT applied." >&2
  exit 1
}

echo "Stopping services..."
systemctl stop portmirror-manager.service portmirror-base.service

echo "Installing $latest..."
rsync -a --delete "$release_root/app/" "$INSTALL_DIR/app/"
rsync -a --delete "$release_root/ui/" "$INSTALL_DIR/ui/"
cp "$release_root/requirements.txt" "$INSTALL_DIR/requirements.txt"
cp "$release_root/update.sh" "$INSTALL_DIR/update.sh" && chmod +x "$INSTALL_DIR/update.sh"
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt" || roll_back "dependency install failed"
echo "$latest" > "$VERSION_FILE"

echo "Restarting services..."
systemctl start portmirror-base.service portmirror-manager.service || roll_back "services failed to start"
sleep 2
systemctl is-active --quiet portmirror-manager.service || roll_back "manager isn't staying up"

rm -rf "$INSTALL_DIR.bak"
echo "Updated $current -> $latest and running."
