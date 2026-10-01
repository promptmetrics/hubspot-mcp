#!/usr/bin/env bash
# Run the local server for the bench and restart it whenever it exits.
# Start this once in a terminal that has AI_GATEWAY_API_KEY exported. After a
# code change, anyone can `kill $(lsof -ti tcp:${PORT:-8000})` and the loop
# brings the server back with the same environment and the new code.
set -u
cd "$(dirname "$0")/.."
PORTAL="${HUBSPOT_PORTAL:?set HUBSPOT_PORTAL}"
PORT="${PORT:-8000}"
while true; do
  echo "[serve.sh] starting hubspot-mcp on 127.0.0.1:${PORT} for portal ${PORTAL} at $(date '+%H:%M:%S')"
  .venv/bin/hubspot-mcp --mode token --portal "$PORTAL" run --transport http --host 127.0.0.1 --port "$PORT"
  echo "[serve.sh] server exited ($?); restarting in 1s"
  sleep 1
done
