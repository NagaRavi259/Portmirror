#!/bin/bash
# Checks GitHub for a newer release and, with confirmation, installs it. Installed
# alongside the application at /opt/portmirror/update.sh by install.sh (and refreshed
# by every later update, along with the `pmctl` wrapper itself, so both stay current);
# `pmctl update` runs it.
#
# Also doubles as the bootstrap for a Pi that predates this whole mechanism (installed
# by hand, with no VERSION file yet): downloaded and run from anywhere - it doesn't need
# to already be the installed copy at /opt/portmirror/update.sh - a missing VERSION file
# just reads as "unknown", which looks older than any real release and triggers a normal
# update onto the latest one, pmctl wrapper included.
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
  trap - ERR   # don't re-trigger ourselves if a command in here also fails
  echo "ERROR: $1 - rolling back to $current." >&2
  systemctl stop portmirror-manager.service portmirror-base.service 2>/dev/null || true
  rm -rf "$INSTALL_DIR"
  mv "$INSTALL_DIR.bak" "$INSTALL_DIR"
  # Checked explicitly, not left to `set -e`: if the rollback's own restart also fails, that's a
  # worse, different situation (the previous version won't come up either) and deserves its own
  # clear message - not a script that silently stops partway through rolling back.
  if systemctl start portmirror-base.service portmirror-manager.service; then
    echo "Rolled back. The update to $latest was NOT applied." >&2
  else
    echo "Rolled the files back to $current, but it also failed to restart - check" \
         "'systemctl status portmirror-base portmirror-manager' by hand." >&2
  fi
  exit 1
}

echo "Stopping services..."
systemctl stop portmirror-manager.service portmirror-base.service

# From here until the final cleanup below, ANY failing command - not just the two or three most
# likely ones - must roll back: services are already stopped, so a script that just dies partway
# through (an rsync that runs out of disk, a cp of a file a release forgot to include, anything)
# would otherwise leave the gateway down with nothing watching it. A trap on ERR catches all of
# them in one place instead of hand-wrapping every single line with `|| roll_back ...`.
trap 'roll_back "unexpected failure while installing"' ERR

echo "Installing $latest..."
rsync -a --delete "$release_root/app/" "$INSTALL_DIR/app/"
rsync -a --delete "$release_root/ui/" "$INSTALL_DIR/ui/"
cp "$release_root/requirements.txt" "$INSTALL_DIR/requirements.txt"
cp "$release_root/update.sh" "$INSTALL_DIR/update.sh"
chmod +x "$INSTALL_DIR/update.sh"
cp "$release_root/pmctl" /usr/local/bin/pmctl
chmod +x /usr/local/bin/pmctl
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
echo "$latest" > "$VERSION_FILE"

echo "Restarting services..."
systemctl start portmirror-base.service portmirror-manager.service
sleep 2
systemctl is-active --quiet portmirror-manager.service || roll_back "manager isn't staying up"

trap - ERR
rm -rf "$INSTALL_DIR.bak"
echo "Updated $current -> $latest and running."
