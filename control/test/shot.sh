#!/usr/bin/env bash
# Screenshot the Studio Routing viewer at two window sizes (1280x800 and 1920x1080).
# Usage: test/shot.sh [OUTDIR] [PORT]     defaults: state/shots, 8799. Port 8765 (the live viewer) is refused.
# Starts viewer/server.py on PORT, polls /api/studio until it answers, then test/shot.mjs drives headless
# Chromium: it waits until the page is live and the stage has nodes, so no blind sleep is needed.
# The server is stopped by its listening PID.
# RE_STUDIO_CONFIG is inherited from the environment, so the server reads the same machine config.
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REPO="$(dirname "$HERE")"
OUT="${1:-$REPO/state/shots}"
PORT="${2:-8799}"
URL="http://127.0.0.1:$PORT"

# OUTDIR may be relative to the caller's directory; resolve it before anything changes directory.
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

[ "$PORT" != 8765 ] || { echo "refusing port 8765, the live viewer" >&2; exit 2; }
if [ "${SHOT_DRY_RUN:-0}" = 1 ]; then
  echo "dry run: OUTDIR=$OUT PORT=$PORT"
  echo "dry run: would write $OUT/viewer-1280x800.png and $OUT/viewer-1920x1080.png"
  exit 0
fi
if ss -ltn | grep -q ":$PORT "; then
  echo "port $PORT is already in use; pick another" >&2
  exit 1
fi

stop_server() {
  local pid
  pid="$(ss -ltnp "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2 || true)"
  [ -z "$pid" ] || kill "$pid" 2>/dev/null || true
}
trap stop_server EXIT

LOG="$OUT/server-$PORT.log"
(cd "$REPO/viewer" && exec python3 server.py "$PORT" > "$LOG" 2>&1) &

ready=0
for _ in $(seq 1 60); do
  if curl -fs -m 3 "$URL/api/studio" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" != 1 ]; then
  echo "viewer did not answer on $PORT; see $LOG" >&2
  exit 1
fi
echo "viewer answering on $PORT; taking screenshots"
node "$HERE/shot.mjs" "$URL/" "$OUT" 1280x800 1920x1080
