#!/usr/bin/env bash
# Message Gateway installer: builds and starts the gateway, waits until it is
# healthy, then opens the guided setup in your browser.
#
#   ./install.sh
#
# Nothing to configure first: no .env file, no keys. You finish setup in the
# browser. Options (environment variables):
#   MG_PORT=9000       use a different port (default 8010)
#   MG_BIND=0.0.0.0    reachable from other machines, not just this one (default
#                      127.0.0.1 - see the warning this prints when you set it)
#   MG_NO_OPEN=1       don't open the browser automatically
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PORT="${MG_PORT:-8010}"
BIND="${MG_BIND:-127.0.0.1}"
URL="http://localhost:${PORT}"
export MG_PORT="$PORT"
export MG_BIND="$BIND"

say()  { printf '%s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# ---- 1. prerequisites -------------------------------------------------------
command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Install Docker Desktop (https://www.docker.com/products/docker-desktop/), start it, then run ./install.sh again."
docker info >/dev/null 2>&1        || fail "Docker is installed but not running. Start Docker Desktop (or the Docker service), wait until it says it is running, then run ./install.sh again."
docker compose version >/dev/null 2>&1 || fail "The 'docker compose' plugin is missing. Update Docker Desktop, or install the Compose v2 plugin: https://docs.docker.com/compose/install/"

# ---- 2. port check (skip if it's our own gateway, e.g. on upgrade) -----------
if docker compose ps --status running --services 2>/dev/null | grep -qx gateway; then
  say "The gateway is already running. Rebuilding and restarting it (your data is kept)."
elif command -v lsof >/dev/null 2>&1 && lsof -iTCP:"$PORT" -sTCP:LISTEN -P >/dev/null 2>&1; then
  fail "Port ${PORT} is already in use by another program. Free it, or pick another port:  MG_PORT=9000 ./install.sh"
fi

# ---- 3. build and start -----------------------------------------------------
say "Building and starting Message Gateway (the first build takes a few minutes)..."
docker compose up -d --build

# ---- 4. wait for healthy ----------------------------------------------------
say "Waiting for the gateway to become healthy..."
ok=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -Eq '"db_ok": *true'; then
    ok=1; break
  fi
  printf '.'
  sleep 3
done
printf '\n'

if [ "$ok" -ne 1 ]; then
  say "The gateway did not become healthy in time. Last log lines:"
  docker compose logs --tail 40 gateway || true
  fail "Startup failed. Fix the problem above and run ./install.sh again. To start over completely: docker compose down -v"
fi

# ---- 5. done ----------------------------------------------------------------
say ""
say "Message Gateway is running."
say ""
say "  Finish setup in your browser:  ${URL}"
say ""
if [ "$BIND" != "127.0.0.1" ]; then
  # Best guess at this machine's address on the local network (skips Docker/VM bridges).
  LAN_IP=""
  if command -v hostname >/dev/null 2>&1; then
    LAN_IP="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -Ev '^(172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.122\.|127\.|169\.254\.|$)' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -n1 || true)"
  fi
  if [ -z "$LAN_IP" ] && command -v ipconfig >/dev/null 2>&1; then
    LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
  fi
  if [ -n "$LAN_IP" ]; then
    say "  Other machines on your network:  http://${LAN_IP}:${PORT}"
    say ""
  fi
  say "  Reachable from other machines too (MG_BIND=${BIND}). The setup page has no"
  say "  login until you create the administrator account below - finish that first"
  say "  if this machine is reachable by anyone you don't trust yet."
  say ""
fi
say "  Two quick steps: choose where to store data, then create the administrator"
say "  account. After that, connect your email / SMS / push / Telegram / WhatsApp"
say "  providers from inside the app. Credentials are entered in the browser and"
say "  stored encrypted in your database. There is no .env file to edit."
say ""
say "  Stop:       docker compose stop"
say "  Start:      docker compose start"
say "  Uninstall:  docker compose down -v    (deletes local data and the encryption key)"
say ""

# ---- 6. open the setup page in the default browser --------------------------
open_url() {
  case "$(uname -s)" in
    Darwin)               open "$1" >/dev/null 2>&1 ;;
    MINGW*|MSYS*|CYGWIN*) cmd.exe //c start "" "$1" >/dev/null 2>&1 ;;
    *)
      if grep -qi microsoft /proc/version 2>/dev/null; then
        cmd.exe /c start "" "$1" >/dev/null 2>&1        # WSL: opens the Windows browser
      elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$1" >/dev/null 2>&1
      else
        return 1
      fi ;;
  esac
}

if [ "${MG_NO_OPEN:-0}" = "1" ]; then
  say "Browser not opened (MG_NO_OPEN=1). Open this address yourself: ${URL}"
elif open_url "$URL"; then
  say "Opened the setup page in your default browser: ${URL}"
else
  say "Could not open a browser automatically. Open this address yourself: ${URL}"
fi
