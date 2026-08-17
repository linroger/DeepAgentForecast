#!/bin/bash
# setup_port80.sh — make plain http://localhost/ open the DeepResearchForecast UI.
#
# What it does (all reversible):
#   1. Stops and disables the macOS bundled Apache (the "It works!" page on :80).
#   2. Installs a pf loopback redirect 80 -> 127.0.0.1:5001 (the DRF Flask server),
#      via an anchor file + two tagged lines in /etc/pf.conf (backed up first).
#   3. Installs a tiny LaunchDaemon that re-enables pf at boot so the redirect
#      survives restarts.
#
# The redirect is loopback-only (lo0): nothing is exposed to the network, and the
# Flask server keeps its 127.0.0.1-only bind. Requires sudo:
#
#   sudo bash scripts/setup_port80.sh            # install
#   sudo bash scripts/setup_port80.sh --status   # show current state
#   sudo bash scripts/setup_port80.sh --uninstall# restore Apache + remove redirect
#
# Note: http://localhost/ only answers while the DRF backend is running
# (cd backend && .venv/bin/python run.py — or scripts/start.sh).
set -euo pipefail

TAG="drf-port80"
ANCHOR_NAME="drf.port80"
ANCHOR_FILE="/etc/pf.anchors/${ANCHOR_NAME}"
PF_CONF="/etc/pf.conf"
DAEMON_PLIST="/Library/LaunchDaemons/com.drf.pf-port80.plist"
APACHE_SVC="system/org.apache.httpd"
TARGET_PORT="${DRF_PORT:-5001}"

status() {
  echo "— Apache on :80:"
  if launchctl print "$APACHE_SVC" >/dev/null 2>&1; then echo "  loaded (serving :80)"; else echo "  not loaded"; fi
  echo "— pf redirect:"
  if grep -q "# ${TAG}\$" "$PF_CONF" 2>/dev/null; then echo "  installed in $PF_CONF"; else echo "  not installed"; fi
  [ -f "$ANCHOR_FILE" ] && echo "  anchor file present: $ANCHOR_FILE"
  [ -f "$DAEMON_PLIST" ] && echo "  boot daemon present: $DAEMON_PLIST"
  echo "— DRF backend on :${TARGET_PORT}:"
  if curl -s -m 2 -o /dev/null "http://127.0.0.1:${TARGET_PORT}/"; then echo "  running"; else echo "  NOT running (start it: cd backend && .venv/bin/python run.py)"; fi
  echo "— http://localhost/ answers:"
  curl -s -m 3 "http://localhost/" | grep -o "<title>[^<]*</title>" || echo "  (no response)"
}

uninstall() {
  echo "Removing pf redirect…"
  if grep -q "# ${TAG}\$" "$PF_CONF" 2>/dev/null; then
    cp "$PF_CONF" "${PF_CONF}.${TAG}-backup-$(date +%Y%m%d%H%M%S)"
    sed -i '' "/# ${TAG}\$/d" "$PF_CONF"
    pfctl -f "$PF_CONF" 2>/dev/null || true
  fi
  rm -f "$ANCHOR_FILE"
  if [ -f "$DAEMON_PLIST" ]; then
    launchctl bootout system "$DAEMON_PLIST" 2>/dev/null || true
    rm -f "$DAEMON_PLIST"
  fi
  echo "Re-enabling bundled Apache…"
  launchctl enable "$APACHE_SVC" 2>/dev/null || true
  launchctl bootstrap system /System/Library/LaunchDaemons/org.apache.httpd.plist 2>/dev/null || true
  echo "Done. http://localhost/ is back to Apache."
}

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "This script needs sudo:  sudo bash $0 ${1:-}" >&2
  exit 1
fi

case "${1:-}" in
  --status) status; exit 0 ;;
  --uninstall) uninstall; exit 0 ;;
  "") ;;
  *) echo "Usage: sudo bash $0 [--status|--uninstall]" >&2; exit 1 ;;
esac

echo "1/4 Stopping + disabling bundled Apache…"
apachectl stop 2>/dev/null || true
launchctl bootout "$APACHE_SVC" 2>/dev/null || true
launchctl disable "$APACHE_SVC" 2>/dev/null || true

echo "2/4 Installing pf redirect 80 -> 127.0.0.1:${TARGET_PORT} (loopback only)…"
printf 'rdr pass on lo0 inet proto tcp from any to any port 80 -> 127.0.0.1 port %s\n' "$TARGET_PORT" > "$ANCHOR_FILE"
if ! grep -q "# ${TAG}\$" "$PF_CONF"; then
  cp "$PF_CONF" "${PF_CONF}.${TAG}-backup-$(date +%Y%m%d%H%M%S)"
  # Translation rules must stay in the translation section: insert our rdr-anchor
  # directly after Apple's, and the load directive at end of file. Both lines are
  # tagged so --uninstall can remove exactly them.
  sed -i '' "/^rdr-anchor \"com.apple\/\*\"/a\\
rdr-anchor \"${ANCHOR_NAME}\" # ${TAG}
" "$PF_CONF"
  printf 'load anchor "%s" from "%s" # %s\n' "$ANCHOR_NAME" "$ANCHOR_FILE" "$TAG" >> "$PF_CONF"
fi
if ! pfctl -nf "$PF_CONF"; then
  echo "pf.conf failed validation — restoring backup." >&2
  latest_backup=$(ls -t "${PF_CONF}.${TAG}-backup-"* 2>/dev/null | head -1)
  [ -n "$latest_backup" ] && cp "$latest_backup" "$PF_CONF"
  exit 1
fi
pfctl -f "$PF_CONF"
pfctl -E 2>/dev/null || true

echo "3/4 Installing boot daemon so the redirect survives restarts…"
cat > "$DAEMON_PLIST" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.drf.pf-port80</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>/sbin/pfctl -f /etc/pf.conf && /sbin/pfctl -E</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
PLIST
chmod 644 "$DAEMON_PLIST"
launchctl bootout system "$DAEMON_PLIST" 2>/dev/null || true
launchctl bootstrap system "$DAEMON_PLIST" 2>/dev/null || true

echo "4/4 Verifying…"
sleep 1
if curl -s -m 3 "http://localhost/" | grep -q "DeepResearchForecast"; then
  echo "✅ http://localhost/ now serves the DeepResearchForecast UI."
else
  if curl -s -m 2 -o /dev/null "http://127.0.0.1:${TARGET_PORT}/"; then
    echo "⚠️  Redirect installed, but http://localhost/ did not answer as expected. Run: sudo bash $0 --status"
  else
    echo "⚠️  Redirect installed, but the DRF backend is not running on :${TARGET_PORT}."
    echo "    Start it (cd backend && .venv/bin/python run.py), then http://localhost/ will work."
  fi
fi
