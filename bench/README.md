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

Multi-turn reuse: `bench/multiturn.py` runs each scenario in `bench/scenarios.jsonl` as one
session with several user turns (turn 1 with `--session-id`, later turns with `--resume`, so
per-turn usage is exact), then the same prompts as single-turn sessions for comparison. It
reports lookup and load calls per turn, cache reads and writes, and per-turn cost, and deletes
the transcript Claude Code saved for the session afterwards. `bench/multiturn_report.py` writes
the report.

```bash
.venv/bin/python bench/multiturn.py --smoke --arm-url routed-jev=http://127.0.0.1:8000/mcp/routed --test-contact "..." --test-deal "..."
.venv/bin/python bench/multiturn.py --reps 3 --single-reps 2 \
  --arm-url routed-jev=http://127.0.0.1:8000/mcp/routed --arm-url full=http://127.0.0.1:8000/mcp \
  --test-contact "..." --test-deal "..."
.venv/bin/python bench/multiturn_report.py bench/runs/<stamp>-multiturn
```

`--model sonnet` runs the sessions on another Claude Code model alias; the report records the
model id actually observed (the `sonnet` alias resolved to `claude-sonnet-5` on 2.1.281).
`--only t13,t14` limits the task set.

`bench/runs/` is gitignored: transcripts carry portal data. `report.md` uses
placeholders only; grep it for real names before it leaves the machine.

## Intent benchmark: official MCP, HubSpot CLI, intent server, grouped control

`bench/intent_run.py` compares four setups on seven rep tasks (`bench/jobs.jsonl`: `job1`..`job5`, `off_phone`,
`off_globex`) against the seeded test portal. One headless Claude Code session per task x arm x model x rep, strictly
sequential. The contract (arm table, session record, failure classes) is `bench/intent_interface.md`.

| Arm | Tools the session has | Models |
|---|---|---|
| A | official HubSpot remote MCP (`hubspot-bench`, `https://mcp.hubspot.com`), no built-ins, no skills | sonnet, opus |
| B | same MCP + Bash, Skill, Read + the `hubspot` Agent CLI + HubSpot's `agent-cli-skills` | sonnet, opus |
| C | local `/mcp/intent` (13 `pronova_*` tools) + Skill + the thin Pronova skills | sonnet, opus |
| D | local `/mcp/grouped` (12 tools) + Skill + the same thin skills | sonnet |

Every session runs from a fresh temp directory that holds only its arm's skills, with `--setting-sources` and `--tools`
set per arm, `--model claude-sonnet-5-5` or `claude-opus-5-5` passed explicitly, and the same rendered `sales-rules.md`
appended to the system prompt. The runner records which skills the session saw (init event) and which it invoked, and
marks a session `harness` when an arm sees a skill it should not. Claude Code 2.1.314 facts it relies on: project skills
load from `<cwd>/.claude/skills` under `--setting-sources project`; `--setting-sources ""` hides them; 19 built-in skills
show in every session; `--tools Skill` leaves `Skill` as the only built-in tool; the Skill tool's input key is `skill`.

### One-time setup (yours; the runner never does these)

1. **Pin the HubSpot CLI.** The runner sets `HUBSPOT_NO_AUTO_UPGRADE=1` for arm B and records `hubspot --version` in
   `meta.json`. Installed and expected: `hubspot 0.17.0 (build 1618, commit aafef8f)` at `~/.hubspot/bin/hubspot`. A
   different version is a warning, not an abort: do not run `hubspot upgrade` between smoke and full run. The CLI must
   already be authenticated to portal 149614891. The runner checks this before any session starts (see "Portal
   identity" below) and aborts when the CLI or the official MCP grant points anywhere else.
2. **OAuth for the official MCP, server name `hubspot-bench`, portal 149614891.** Claude Code stores the token; the runner
   reuses it.

   ```bash
   claude mcp add --scope user --transport http hubspot-bench https://mcp.hubspot.com
   claude            # interactive session: /mcp, pick hubspot-bench, Authenticate, choose portal 149614891
   claude mcp get hubspot-bench     # must print "Status: ✔ Connected"
   ```

   `--scope user` matters: the default scope ("local") is stored per project directory, so a server added in one
   directory reads as "not connected" when the runner starts from another. Prefer a dedicated demo Claude account (PLAN review table, row 8): `hubspot-bench` is its own Claude Code server entry
   and leaves the claude.ai HubSpot connector alone. The runner's pre-flight runs `claude mcp get hubspot-bench` (no model tokens) and aborts
   with this command when the server is not connected. Not verified: whether the stored token is reused for a server
   that arrives through `--mcp-config` with `--setting-sources ""`. The first smoke session on arm A shows it; an
   `mcp_needs-auth` failure on every A and B session means it is not.
3. **Rules.** The intent server reads `<dir>/<portal>.yaml`; the seed writes `rules-enterprise.yaml`, so copy it:

   ```bash
   RULES_DIR=$(mktemp -d)        # outside any repo: the YAML names the demo owner's email
   cp /Users/izzy/Documents/hubspot-forecast/demo/seed/out/rules-enterprise.yaml "$RULES_DIR/149614891.yaml"
   ```

4. **Server, arm D in preview-everything mode.** Start it with the env below, in a shell that has the portal token (names
   only; set the values yourself):

   ```bash
   export HUBSPOT_PORTAL=149614891 HUBSPOT_MCP_RULES_DIR="$RULES_DIR"
   export HUBSPOT_MCP_SERVER_SECRET=$(openssl rand -hex 32)
   export HUBSPOT_MCP_GROUPED_FORCE_PREVIEW=1        # arm D previews every write, as arm C does; unset = AUTO tier
   bench/serve.sh
   ```

   The runner cannot change a running server, but it checks it: `get_sales_rules` on `/mcp/grouped` reports
   `force_preview` (the variable's state in the server process), and pre-flight aborts when that differs from the tier
   the run asks for (`preview` by default, `auto` with `--arm-d-auto-tier`; restart the server without the variable for
   `auto`). `meta.json` records both `arm_d_tier` (requested) and `arm_d_force_preview_observed` (what the server said).
5. **Environment names the runner reads:** `HUBSPOT_TOKEN_149614891` (read-only end-state checks; restore.py gets it as
   `HUBSPOT_PRIVATE_APP_TOKEN` when that is unset), `HUBSPOT_MCP_SERVER_SECRET` (bearer for C and D, also stripped from
   every session's environment). Stripped from every session: `CLAUDE_CODE_SUBAGENT_MODEL`,
   `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, `HUBSPOT_PRIVATE_APP_TOKEN`, `HUBSPOT_TOKEN_*`, `SEED_DEMO_USER_EMAIL`,
   `AI_GATEWAY_API_KEY`, and the `--secret-env` variable. Arm B, whose Bash tool can run `env`, gets an allowlist instead
   of a denylist (`B_ENV_ALLOW` in `intent_run.py`: PATH, HOME, USER, locale, proxy and CA variables, `ANTHROPIC_*` and
   `CLAUDE_*` settings whose names do not look like credentials) plus `HUBSPOT_NO_AUTO_UPGRADE=1`, and runs zsh with an
   empty `ZDOTDIR` so your shell profile (which often exports API keys) is not sourced. Consequence: arm B authenticates
   through `claude login`, not an exported `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`. Do not set
   `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`: in 2.1.314 it forces permission mode `default` and arm B's tools stall on prompts.

### Arm B isolation and portal identity

- **Filesystem.** Arm B is the only arm with Bash and Read, and the session directory is only a starting point. The
  runner writes `.claude/settings.json` into arm B's temp directory: `Read(//<path>/**)` deny rules plus a sandbox with
  `denyRead` and `allowUnsandboxedCommands: false` over the workspace that holds this repo (answer key, seed output,
  `bench/runs/`, the thin skills), `~/.claude`, `~/.ssh` and `~/.aws`. Checked with claude 2.1.314 under
  `--permission-mode bypassPermissions`: both the Read deny and the sandbox hold, and a retry with
  `dangerouslyDisableSandbox` is refused. Residual: `~/.hubspot` stays readable and writable because the CLI needs it, and
  the sandbox adds its own proxy variables to Bash's environment. Not verified: that the `hubspot` CLI runs unchanged
  inside the sandbox. The portal identity check below runs the CLI inside it before the first session.
- **Portal identity.** A and B write with whatever portal the official MCP grant and the CLI's default account point at,
  and `PortalReader`, the restore and the snapshot only ever look at portal 149614891. Before the first session the
  runner runs one cheap read-only session on sonnet (arm B's configuration when B runs, else arm A's): it calls
  `get_organization_details` on the official MCP and, for B, runs `hubspot whoami` piped through
  `python3 -c` so only `portalId` is printed (the user's address never reaches the model). Any id other than `--portal`
  aborts with exit 2. The ids seen go to `meta.json` as `portal_identity`. Not run live yet.
- **Leaks stop the run.** A session that sees skills it should not, or built-in tools its arm does not have, is a
  `harness` failure and the run stops after that session's restore (exit 4): the same leak would repeat in every later
  session of the arm.
- **Model.** A session whose init event reports a model that does not start with the requested id is a `harness`
  failure (`model_mismatch: <observed> != <requested>`), never graded as that cell.

### Smoke run

```bash
T=/Users/izzy/Documents/hubspot-forecast/demo/seed/out/truth-enterprise-2026-11-10.json
.venv/bin/python bench/intent_run.py --truth "$T" --dry-run        # fakes only: plan, argv per arm, composed prompts
.venv/bin/python bench/intent_run.py --truth "$T" --rules-dir "$RULES_DIR" --smoke
```

`--smoke` is one rep of all seven tasks in all seven cells, 49 sessions. `--dry-run` starts no claude, no HubSpot request,
no restore and needs no server. Pre-flight (exit 2 with a one-line reason on failure): Claude and CLI versions; skills
sources hold `*/SKILL.md`; the official server is connected; the rules render and their as-of date equals the truth
file's; `/mcp/intent` lists 13 tools and `/mcp/grouped` 12, both serve the rendered rules' fingerprint, and
`/mcp/grouped` runs at the requested write tier; the portal identity check; one restore, then the baseline read
(`baseline.json`). A missing `claude` binary or a local server that is down aborts the same way (exit 2). During the run:

- After every write task (`job3`, `job5`, `off_globex`) the runner reads the end state read-only (`PortalReader`: GETs,
  searches and batch reads only; a write method raises), stores it in the session record, then runs restore.py. After a
  read task it checks the seeded records are unchanged; a read task that wrote is a `model` failure and gets a restore.
  "Unchanged" compares only the properties `restore.py` rewrites (deal name, amount, close date, stage, pipeline,
  forecast category, owner, next step, currency; contact name, email, phone, job title; company name, domain). A stray
  write to anything else (a deal description, a contact's owner) cannot be undone by a restore, so it is listed under
  `unrestorable` and does not fail every later session as stale state; the grader still sees it in the end state.
- A restore that fails twice stops the run (exit 3): the portal is dirty. `--no-restore` skips restore.py (use it only
  with a fake or an already-clean portal); the end-state read still runs.
- Failures are classed per session: `upstream` (429, 5xx, overloaded, OAuth expiry, MCP not connected, stale seed state,
  end-state read failed) is re-run once and both attempts are kept; `harness` (skills or tools check, claude missing, no init
  event); `model` (timeout, no or invalid answer block, wrote on a read task). A wrong answer is graded, not a failure.
- Outputs land in `bench/runs/<stamp>-intent/` (gitignored: they hold the demo owner's email and portal data):
  `meta.json`, `sessions.jsonl` (each record keeps the full final reply as `result_text`, capped at 60000 characters),
  `events-*.jsonl`, `baseline.json`, `tools_list.json`, `sales-rules.md`, `jobs.jsonl`.
  Skim the classes without opening records: `jq -r '[.task_id,.arm,.model,.attempt,(.failure_class//"ok")]|@tsv' sessions.jsonl`.
- Run nothing else against portal 149614891 while it runs: Search is rate limited and every session assumes seed state.

### Arm D: preview-everything

`/mcp/grouped` reaches the write gate through `server._run_domain_tool(..., mode="grouped")`, which lets AUTO-tier writes
apply at once. The switch is `HUBSPOT_MCP_GROUPED_FORCE_PREVIEW=1` in the server's environment (off by default, read on
every call): `intent/grouped.py` passes `force_preview=True` through `server._run_domain_tool`, and
`handlers.handle_tool` turns an AUTO classification into CONFIRM, so the write returns `status: "preview"` and applies
only after `write_actions(action="approve")`. CONFIRM and FULL_GATE writes, `/mcp` and `/mcp/intent` are unchanged.
Tested in `tests/test_intent_grouped.py`.

### Grading (`bench/grade.py`)

`python bench/grade.py <run dir> --truth <truth.json>` writes `grades.jsonl` and `consistency.json` and prints one row
per task x arm x model. Three columns, three questions:

- `mean`: the average `score`, which measures the task. It ignores collateral writes.
- `exact`: `score` is 1.0, no violations, and nothing the grader flags as soft (job1: every named deal carries exactly
  the truth's reason codes; off_phone: full colleague recall; a write task with no answer block is never exact).
- `clean`: scored runs with no violation (other deals touched, extra records created, the wrong Jane's data in a
  brief). Read `mean` and `clean` together: a run can score 1.000 and still have changed ten other deals.

Choices worth knowing when reading a result: job2's `timeline` check scores only past entries (the scheduled call is
the `call_tomorrow` check) and maps every email variant to `email`; the job2 answer rule fixes `recent_activity.type` to
`call|meeting|email|note|task`; job2's `open_tasks` match needs the answer title to contain the truth title or to be at
least 60 percent of it; job5's "confirm timing" check looks for the words in the task title plus body, in any order;
a write task whose answer block is missing or unparsable still scores its CRM end state, and counts as a `model`
failure (`model_fail` column, `answer_ok` false, `details.block_missing`).

## Files

- `tasks.jsonl`: 20 tasks with `{TEST_CONTACT}` / `{TEST_DEAL}` placeholders and completion checks: 12 from the first bench, four skill tasks (t13–t16), four boundary prompts (t17–t20) with charter and skill labels.
- `run.py`: the runner. Parses `stream-json`, checks completion, records routing decisions, rejects pending previews and undoes AUTO writes after each session.
- `mcp_client.py`: bare JSON-RPC-over-HTTP client for tools/list, state checks and cleanup.
- `report.py`: aggregates `sessions.jsonl` into `report.md`, with routing-per-arm and paired-per-task sections when two routed arms are present; `--price-model` reprices.
- `skill_eval.py` / `skill_cases.jsonl`: Jev vs keyword on 40 labelled skill prompts, offline or `--via-server`.
- `charter_eval.py`: Jev vs keyword on the 49-prompt charter routing corpus (`tests/routing_corpus.yaml`).
- `rescore.py`: re-applies the checks to an existing run after a check changes.
- `serve.sh`: restart loop for the local server; `PORT` and `HUBSPOT_MCP_ROUTER` select the instance.
- `scenarios.jsonl`, `multiturn.py`, `multiturn_report.py`: multi-turn sessions (one skill, several user turns) and their report.
- `intent_run.py`, `portal_read.py`, `jobs.jsonl`, `intent_interface.md`: the intent benchmark (section above): runner, read-only portal reader, the seven tasks, the shared contract.

## Gotchas

- Restart the server after code changes; it does not reload. `bench/serve.sh` wraps it in a
  restart loop so a `kill $(lsof -ti tcp:8000)` brings it back with the new code and the same env.
- Do not launch it under another project's `vercel env run`: an injected `REDIS_URL` switches the
  state store to Redis and every tool fails without `HUBSPOT_MCP_STATE_KEY`.
- CONFIRM-tier writes come back `declined` in headless runs (Claude Code declines the inline
  confirmation). That still counts as reaching the gate; nothing is applied.
- The runner's cleanup rejects every pending preview on the portal after each session. Do not
  leave your own previews pending while a run is going.
