#!/bin/sh
# SessionStart hook for the hubspot-mcp plugin.
#
# Writes ~/.claude/hubspot/app_credentials.json from the user-supplied app
# credentials (passed in via env by the hook command in hooks/hooks.json), so
# both the MCP server (token refresh) and the /hubspot-mcp:auth slash command
# can read them. chmod 600 — readable only by the owner. The secret never
# enters the MCP subprocess env or any log; it lives in the OS keychain (via
# plugin userConfig) -> this file -> HubSpot's token endpoint.
#
# Then nudges the user to authenticate if no portal token file exists yet.
set -u

: "${HUBSPOT_CLIENT_ID:?hubspot-mcp: HUBSPOT_CLIENT_ID not set}"
: "${HUBSPOT_CLIENT_SECRET:?hubspot-mcp: HUBSPOT_CLIENT_SECRET not set}"

DIR="$HOME/.claude/hubspot"
mkdir -p "$DIR"

python3 - <<'PY'
import json, os, pathlib
d = pathlib.Path.home() / ".claude" / "hubspot"
d.mkdir(parents=True, exist_ok=True)
p = d / "app_credentials.json"
p.write_text(json.dumps({
    "client_id": os.environ["HUBSPOT_CLIENT_ID"],
    "client_secret": os.environ["HUBSPOT_CLIENT_SECRET"],
    "region": os.environ.get("HUBSPOT_REGION") or "us",
}))
p.chmod(0o600)
PY

PORTAL="${HUBSPOT_PORTAL:-}"
if [ -n "$PORTAL" ] && [ ! -f "$DIR/$PORTAL.json" ]; then
  echo "HubSpot MCP: not authenticated for portal $PORTAL — run /hubspot-mcp:auth to sign in."
fi