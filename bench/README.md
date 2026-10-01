# bench: full surface vs routed surface, same tasks

Measures what the MCP adds to a Claude Code session and what the routed
surface (`/mcp/routed`, three meta-tools) changes: context tokens, cost, time,
turns, tool calls and task completion. Runs are paired: every task runs through
Claude Code headless against `control` (no MCP), `full` (`/mcp`, 86 tools) and
`routed`, interleaved, for N reps.

## Run

```bash
# 1. server, single-portal token mode, one process serving both surfaces
export HUBSPOT_PORTAL=<portal id>            # portal config in ~/.claude/hubspot/<id>.json, or HUBSPOT_TOKEN_<id>
export HUBSPOT_MCP_SERVER_SECRET=$(openssl rand -hex 32)
export AI_GATEWAY_API_KEY=<vercel ai gateway key>   # Jev; without it routing falls back to keywords
.venv/bin/hubspot-mcp --mode token --portal "$HUBSPOT_PORTAL" run --transport http --host 127.0.0.1 --port 8000

# 2. sessions (another shell, same env)
.venv/bin/python bench/run.py --dry-run --test-contact "<test contact name>" --test-deal "<test deal name>"
.venv/bin/python bench/run.py --reps 3 --test-contact "<test contact name>" --test-deal "<test deal name>"

# 3. report
.venv/bin/python bench/report.py bench/runs/<timestamp>
```

`bench/runs/` is gitignored: transcripts carry portal data. `report.md` uses
placeholders only; grep it for real names before it leaves the machine.

## Files

- `tasks.jsonl`: 12 tasks with `{TEST_CONTACT}` / `{TEST_DEAL}` placeholders and completion checks.
- `run.py`: the runner. Parses `stream-json`, checks completion, rejects pending previews and undoes AUTO writes after each session.
- `mcp_client.py`: bare JSON-RPC-over-HTTP client for tools/list, state checks and cleanup.
- `report.py`: aggregates `sessions.jsonl` into `report.md`.

## Gotchas

- Restart the server after code changes; it does not reload. `bench/serve.sh` wraps it in a
  restart loop so a `kill $(lsof -ti tcp:8000)` brings it back with the new code and the same env.
- Do not launch it under another project's `vercel env run`: an injected `REDIS_URL` switches the
  state store to Redis and every tool fails without `HUBSPOT_MCP_STATE_KEY`.
- CONFIRM-tier writes come back `declined` in headless runs (Claude Code declines the inline
  confirmation). That still counts as reaching the gate; nothing is applied.
- The runner's cleanup rejects every pending preview on the portal after each session. Do not
  leave your own previews pending while a run is going.
