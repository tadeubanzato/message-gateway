#!/usr/bin/env bash
# Lets the gateway's /reboot text command restart this machine.
#
#   sudo ./host/install-reboot-helper.sh             install
#   sudo ./host/install-reboot-helper.sh --remove    uninstall
#
# A container can't restart its host, and the gateway isn't given the access to
# try. Instead, /reboot writes a request file into host-control/ (a folder shared
# with the container), and the systemd units installed here watch for that file
# and reboot. That one file is all the gateway can do to this machine.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/host-control"
REQUEST="$DIR/reboot-request"
MARKER="$DIR/reboot-helper-installed"
UNIT=message-gateway-reboot

[ "$(id -u)" -eq 0 ] || { echo "Run this with sudo." >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "This helper needs systemd." >&2; exit 1; }

if [ "${1:-}" = "--remove" ]; then
  systemctl disable --now "$UNIT.path" 2>/dev/null || true
  rm -f "/etc/systemd/system/$UNIT.path" "/etc/systemd/system/$UNIT.service" "$MARKER" "$REQUEST"
  systemctl daemon-reload
  echo "Removed. /reboot will now answer that it isn't set up."
  exit 0
fi

mkdir -p "$DIR"
rm -f "$REQUEST"   # never start with a request already waiting

cat > "/etc/systemd/system/$UNIT.path" <<UNIT_EOF
[Unit]
Description=Message Gateway: watch for a /reboot request

[Path]
PathExists=$REQUEST

[Install]
WantedBy=multi-user.target
UNIT_EOF

# The request is deleted BEFORE rebooting, and the reboot only happens if that
# worked - otherwise the file would still be there after the restart and the
# machine would reboot in a loop.
cat > "/etc/systemd/system/$UNIT.service" <<UNIT_EOF
[Unit]
Description=Message Gateway: reboot requested by text command

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'rm -f "$REQUEST" && [ ! -e "$REQUEST" ] && systemctl reboot'
UNIT_EOF

systemctl daemon-reload
systemctl enable --now "$UNIT.path"
touch "$MARKER"
echo "Installed. Turn on /reboot under Channels > SMS > Text commands."
