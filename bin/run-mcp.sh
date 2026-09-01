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

LOG="$DATA/install.log"

if [ "$need_install" = 1 ]; then
  echo "hubspot-mcp: provisioning venv in $DATA/venv (first run or updated plugin)…" >&2
  mkdir -p "$DATA"
  : > "$LOG"

  # uv when available (seconds rather than tens of seconds), plain venv+pip
  # otherwise. Never `curl | sh` a bootstrap: this runs unattended at server
  # launch, and silently installing a toolchain is not this script's business.
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 "$DATA/venv" >>"$LOG" 2>&1 &&
      VIRTUAL_ENV="$DATA/venv" uv pip install --quiet "$ROOT" >>"$LOG" 2>&1
  else
    python3 -m venv "$DATA/venv" >>"$LOG" 2>&1 &&
      "$DATA/venv/bin/pip" install --quiet --upgrade pip >>"$LOG" 2>&1 &&
      "$DATA/venv/bin/pip" install --quiet "$ROOT" >>"$LOG" 2>&1
  fi

  if [ ! -x "$DATA/venv/bin/python3" ]; then
    echo "hubspot-mcp: venv provisioning failed. Needs python3 >= 3.12 on PATH and" >&2
    echo "  network access to PyPI. Full output: $LOG" >&2
    exit 1
  fi
  echo "$ROOT" > "$MARKER"
fi

# Re-check after provisioning: a partially-installed venv (interrupted first
# run) would otherwise exec a python with no hubspot_mcp and fail with an
# opaque ModuleNotFoundError on stdio, where the client sees only a dead server.
if ! "$DATA/venv/bin/python3" -c "import hubspot_mcp" >/dev/null 2>&1; then
  echo "hubspot-mcp: venv exists but hubspot_mcp is not importable — removing the" >&2
  echo "  marker so the next launch reprovisions. Details: $LOG" >&2
  rm -f "$MARKER"
  exit 1
fi

exec "$DATA/venv/bin/python3" -m hubspot_mcp run --transport stdio