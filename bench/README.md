# bench: full surface vs routed surface, same tasks

Measures what the MCP adds to a Claude Code session and what the routed
surface (`/mcp/routed`, four meta-tools) changes: context tokens, cost, time,
turns, tool calls and task completion. Runs are paired: every task runs through
Claude Code headless against `control` (no MCP), `full` (`/mcp`, 88 tools) and
`routed`, interleaved, for N reps. The same runner compares two routers on one
surface by pointing two arms at two server instances (`--arm-url`).

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

# 3. report (Opus list prices as Claude Code reports them; --price-model sonnet-5.5 reprices the same tokens)
.venv/bin/python bench/report.py bench/runs/<timestamp>
.venv/bin/python bench/report.py bench/runs/<timestamp> --price-model sonnet-5.5
```

Router comparison, Jev vs keyword on the routed surface: run a second instance with the
keyword router forced and name the arms for the router they test. The runner probes each
routed arm's `find_capabilities` at startup and aborts if an arm named `*jev*` answers with
keywords (which is what happens when the Jev instance was started without `AI_GATEWAY_API_KEY`).

```bash
HUBSPOT_PORTAL=<portal id> PORT=8000 bench/serve.sh                              # Jev (key exported)
HUBSPOT_PORTAL=<portal id> HUBSPOT_MCP_ROUTER=keyword PORT=8001 bench/serve.sh   # keyword
.venv/bin/python bench/run.py --reps 3 --test-contact "..." --test-deal "..." \
  --arm-url routed-jev=http://127.0.0.1:8000/mcp/routed \
  --arm-url routed-kw=http://127.0.0.1:8001/mcp/routed
```

`--model sonnet` runs the sessions on another Claude Code model alias; the report records the
model id actually observed (the `sonnet` alias resolved to `claude-sonnet-5` on 2.1.281).
`--only t13,t14` limits the task set.

`bench/runs/` is gitignored: transcripts carry portal data. `report.md` uses
placeholders only; grep it for real names before it leaves the machine.

## Files

- `tasks.jsonl`: 20 tasks with `{TEST_CONTACT}` / `{TEST_DEAL}` placeholders and completion checks: 12 from the first bench, four skill tasks (t13–t16), four boundary prompts (t17–t20) with charter and skill labels.
- `run.py`: the runner. Parses `stream-json`, checks completion, records routing decisions, rejects pending previews and undoes AUTO writes after each session.
- `mcp_client.py`: bare JSON-RPC-over-HTTP client for tools/list, state checks and cleanup.
- `report.py`: aggregates `sessions.jsonl` into `report.md`, with routing-per-arm and paired-per-task sections when two routed arms are present; `--price-model` reprices.
- `skill_eval.py` / `skill_cases.jsonl`: Jev vs keyword on 40 labelled skill prompts, offline or `--via-server`.
- `charter_eval.py`: Jev vs keyword on the 49-prompt charter routing corpus (`tests/routing_corpus.yaml`).
- `rescore.py`: re-applies the checks to an existing run after a check changes.
- `serve.sh`: restart loop for the local server; `PORT` and `HUBSPOT_MCP_ROUTER` select the instance.

## Gotchas

- Restart the server after code changes; it does not reload. `bench/serve.sh` wraps it in a
  restart loop so a `kill $(lsof -ti tcp:8000)` brings it back with the new code and the same env.
- Do not launch it under another project's `vercel env run`: an injected `REDIS_URL` switches the
  state store to Redis and every tool fails without `HUBSPOT_MCP_STATE_KEY`.
- CONFIRM-tier writes come back `declined` in headless runs (Claude Code declines the inline
  confirmation). That still counts as reaching the gate; nothing is applied.
- The runner's cleanup rejects every pending preview on the portal after each session. Do not
  leave your own previews pending while a run is going.
