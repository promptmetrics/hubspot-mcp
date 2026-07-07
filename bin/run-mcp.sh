#!/bin/sh
# MCP server launcher for the hubspot-mcp Claude Code plugin.
#
# Provisions an isolated Python venv in the plugin's persistent data dir on
# first run (and whenever the installed plugin root changes, e.g. after a
# /plugin update), then execs the stdio MCP server. Provisioning happens here
# — at server launch — so the venv is guaranteed ready before the server starts
# (no race with SessionStart hooks).
#
# CLAUDE_PLUGIN_ROOT / CLAUDE_PLUGIN_DATA are exported to MCP subprocesses by
# Claude Code (see plugins-reference §environment-variables).
set -u

DATA="${CLAUDE_PLUGIN_DATA:-}"
ROOT="${CLAUDE_PLUGIN_ROOT:-}"

if [ -z "$DATA" ] || [ -z "$ROOT" ]; then
  echo "hubspot-mcp: CLAUDE_PLUGIN_DATA/CLAUDE_PLUGIN_ROOT not set — plugin misconfigured" >&2
  exit 1
fi

MARKER="$DATA/.installed_root"
need_install=0
if [ ! -x "$DATA/venv/bin/python3" ]; then
  need_install=1
elif [ ! -f "$MARKER" ] || [ "$(cat "$MARKER" 2>/dev/null)" != "$ROOT" ]; then
  need_install=1
fi

if [ "$need_install" = 1 ]; then
  echo "hubspot-mcp: provisioning venv in $DATA/venv (first run or updated plugin)…" >&2
  python3 -m venv "$DATA/venv" || {
    echo "hubspot-mcp: venv creation failed — need python3 >= 3.12 on PATH" >&2
    exit 1
  }
  "$DATA/venv/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$DATA/venv/bin/pip" install --quiet "$ROOT" || {
    echo "hubspot-mcp: pip install failed — check network and that python3 >= 3.12" >&2
    exit 1
  }
  echo "$ROOT" > "$MARKER"
fi

exec "$DATA/venv/bin/python3" -m hubspot_mcp run --transport stdio