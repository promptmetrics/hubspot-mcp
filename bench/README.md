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

### Report and verdict (`bench/intent_report.py`)

`python bench/intent_report.py <run dir> --truth <truth.json> [--out docs/bench-2026-11-intent-report.md]` reads
`sessions.jsonl` and `grades.jsonl` and writes the report: the verdict first, then per-job tables by arm x model
(correctness, consistency, tool calls, context and output tokens, wall time, cost), write safety, failures by class,
job 2 invented facts when `grade.py --judge` ran, and the C vs D comparison. Upstream and harness failures are listed
apart and never count against an arm. The report holds aggregates only. Emails become `<email>` and `--mask-name NAME`
values (the demo owner's display name, repeatable) become `the rep`; the CLI refuses to write a report that still holds
an email or a masked name. The verdict rule sits verbatim in the module docstring and in every report; it is fixed
before the full run, so do not edit it afterwards. Exit codes: 0 written, 2 `sessions.jsonl` or `grades.jsonl` missing,
3 the leak check failed (nothing written), 4 `--out` is inside the repo and no name to mask was found (pass `--mask-name`).
The truth file holds owner emails rather than display names, so `--mask-name` is what covers the owner's full name; the
email local-part tokens are masked as well.

### Full run on demo day

Seeding writes to portal 149614891: nothing below that starts with `seed.py --apply`, `reset.py --apply` or
`restore.py --apply --yes` runs without your go. Variable names only; set the values in your shell, never in a file.
The full run takes roughly 8-10 hours (490 sessions: 7 tasks x 7 cells x 10 reps, strictly sequential), so start it
in the morning and keep the machine awake. The run needs a shell where `HUBSPOT_TOKEN_149614891`,
`HUBSPOT_MCP_SERVER_SECRET` and (for the seed's rules file) `SEED_DEMO_USER_EMAIL` are set, the one-time setup above
is done, and the CLI and Claude Code versions have not changed since the rehearsal.

Two terminals. Terminal 1 runs the server (`bench/serve.sh` loops in the foreground); terminal 2 runs the seed scripts,
`intent_run.py`, grading and the report. Run the common block in both (replace `<day>` with `2026-11-06` or `2026-11-10`;
run it again with the new day on 10 Nov, since `T` is per shell). Both terminals also need the same
`HUBSPOT_MCP_SERVER_SECRET` value, and terminal 2 needs `HUBSPOT_TOKEN_149614891`. Generate the secret once in terminal 1
(`export HUBSPOT_MCP_SERVER_SECRET=$(openssl rand -hex 32)`) and export that same value in terminal 2: set values in the
shell, never in a file. `RULES_DIR` is a fixed path so both terminals use the same directory:

```bash
SEED=/Users/izzy/Documents/hubspot-forecast            # seed repo; run its scripts from here
export T=$SEED/demo/seed/out/truth-enterprise-<day>.json
export RULES_DIR=$HOME/.hubspot-bench-rules            # outside any repo: the YAML names the demo owner's email
mkdir -p "$RULES_DIR" && chmod 700 "$RULES_DIR"
```

#### 6 Nov: dress rehearsal (seed dated 6 Nov, smoke, judge sample)

6 Nov is the first valid demo day (window 6 to 24 Nov). The rehearsal's seed carries the as-of date 2026-11-06, so its
answer key differs from the 10 Nov key: never compare figures across the two.

Terminal 2 (steps 1 to 3a):

```bash
# 1. Plan (offline), then seed. The apply step needs your go.
cd "$SEED"
python3 demo/seed/seed.py --portal enterprise --demo-month 2026-11 --demo-day 2026-11-06 --plan
python3 demo/seed/seed.py --portal enterprise --demo-month 2026-11 --demo-day 2026-11-06 \
    --expect-portal 149614891 --apply --yes
sleep 90                                                # Search indexing lag

# 2. Category check (read-only): the diff must list no change. A drifted forecast category means the seed's
#    pinning step did not hold; stop and re-run restore.py --apply --yes before any session.
python3 demo/seed/restore.py --expect-portal 149614891 --apply

# 3a. Rules file for the server (arm D previews every write, as arm C does)
cp "$SEED/demo/seed/out/rules-enterprise.yaml" "$RULES_DIR/149614891.yaml"
```

Terminal 1 (step 3b, leave it running):

```bash
cd /Users/izzy/Documents/hubspot-mcp
export HUBSPOT_PORTAL=149614891 HUBSPOT_MCP_RULES_DIR="$RULES_DIR"
export HUBSPOT_MCP_GROUPED_FORCE_PREVIEW=1
bench/serve.sh
```

Terminal 2 (steps 4 to 6). Run directories get explicit names with `--out`, so no later step guesses with `ls -dt`:

```bash
cd /Users/izzy/Documents/hubspot-mcp
OWNER="<owner display name>"                            # the demo owner's name, as the answers print it

# 4. Smoke (49 sessions), then 3 more reps of job 2 so the judge sample has at least 20 items to draw from
SMOKE=bench/runs/2026-11-06-smoke-intent
JOB2=bench/runs/2026-11-06-job2-intent
.venv/bin/python bench/intent_run.py --truth "$T" --dry-run
.venv/bin/python bench/intent_run.py --truth "$T" --rules-dir "$RULES_DIR" --smoke --out "$SMOKE"
.venv/bin/python bench/intent_run.py --truth "$T" --rules-dir "$RULES_DIR" --only job2 --reps 3 --out "$JOB2"

# 5. Grade both, check the report renders, build the judge validation kit (blind ids, masked answers)
for D in "$SMOKE" "$JOB2"; do .venv/bin/python bench/grade.py "$D" --truth "$T"; done
.venv/bin/python bench/intent_report.py "$SMOKE" --truth "$T" --out "$SMOKE/intent-report.md" --mask-name "$OWNER"
KIT=bench/runs/judge-kit-2026-11-06                     # must stay under bench/runs/: the packet holds answer-key values
.venv/bin/python bench/judge_kit.py sample "$SMOKE" "$JOB2" --truth "$T" --n 20 --seed 20261106 --out "$KIT" --mask-name "$OWNER"
.venv/bin/python bench/judge_kit.py judge "$KIT" --truth "$T" --mask-name "$OWNER"   # one claude -p call per item; writes $KIT/judge.csv

# 6. Hand $KIT/packet.md plus grader_A.csv and grader_B.csv to two people (never key.json). When both CSVs are back:
.venv/bin/python bench/judge_kit.py kappa "$KIT"
```

Gate before 10 Nov: judge-versus-human-consensus kappa of at least 0.8 over at least 10 consensus items, with every
sampled item labelled by both graders and the judge. The `kappa` command prints PASS, FAIL, INSUFFICIENT (kappa clears
0.8 on fewer than 10 consensus items), INVALID (a blank, missing, unknown or repeated item: finish the files) or
UNDEFINED (the judge and the consensus each used one class only, typically when no answer holds an invented fact:
draw a larger sample or add seeded items that do, and do not count it as a pass). Only PASS clears the gate. Below
0.8, fix the rubric (bump `JUDGE_RUBRIC_VERSION`), re-sample and re-validate. The judge runs on Opus and Opus is also
one of the two models under test; the kit pools both models' answers, so read the notes for any pattern confined to
the Opus answers. If it cannot clear by the morning of 10 Nov, run the full run
without `--judge` and report job 2 invented facts as not judged. The rehearsal also shows whether arm A and B sessions
authenticate headless (`mcp_needs-auth` on every A and B session means they do not): fix that before 10 Nov.

#### 10 Nov: full run (`--reps 10`)

Terminal 2 (steps 1 to 3a):

```bash
# 1. Put the portal back to a clean state, then reset and re-seed for the demo day. Each apply needs your go.
cd "$SEED"
python3 demo/seed/restore.py --expect-portal 149614891 --apply --yes      # removes what the rehearsal wrote
python3 demo/seed/reset.py --plan
python3 demo/seed/reset.py --expect-portal 149614891 --apply --yes        # archives every tagged record
sleep 90
python3 demo/seed/seed.py --portal enterprise --demo-month 2026-11 --demo-day 2026-11-10 --plan
python3 demo/seed/seed.py --portal enterprise --demo-month 2026-11 --demo-day 2026-11-10 \
    --expect-portal 149614891 --apply --yes
sleep 90

# 2. Category check (read-only): the diff must list no change.
python3 demo/seed/restore.py --expect-portal 149614891 --apply
#    Then probe notes_last_contacted and notes_next_activity_date on D01, D02 and one clean deal against the answer
#    key (seed README, "Validity window"). No script in this repo does that probe yet; do it read-only in HubSpot.

# 3a. Rules file for the server (step 3b below restarts the server so it serves the 10 Nov rules)
cp "$SEED/demo/seed/out/rules-enterprise.yaml" "$RULES_DIR/149614891.yaml"
```

Terminal 1 (step 3b): stop the rehearsal's server first, then start this one and leave it running
(`HUBSPOT_MCP_GROUPED_FORCE_PREVIEW=1` is the arm D tier):

```bash
cd /Users/izzy/Documents/hubspot-mcp
export HUBSPOT_PORTAL=149614891 HUBSPOT_MCP_RULES_DIR="$RULES_DIR"
export HUBSPOT_MCP_GROUPED_FORCE_PREVIEW=1
bench/serve.sh
```

Terminal 2 (steps 4 and 5):

```bash
cd /Users/izzy/Documents/hubspot-mcp
OWNER="<owner display name>"                            # the demo owner's name, as the answers print it

# 4. Plan, then the full run in the background (490 sessions, roughly 8-10 hours). The run directory is named
#    up front with --out; the runner creates it within seconds, and a failed pre-flight leaves only an empty
#    directory, so fix the cause and rerun with the same --out.
RUN=bench/runs/2026-11-10-full-intent
.venv/bin/python bench/intent_run.py --truth "$T" --dry-run
#    caffeinate -ims keeps the Mac awake while the run lives (the Mac must stay on mains power; closing the lid still
#    sleeps it unless an external display is attached). --stop-before is the deadline below, with a margin.
caffeinate -ims nohup .venv/bin/python bench/intent_run.py --truth "$T" --rules-dir "$RULES_DIR" --reps 10 \
    --out "$RUN" --stop-before 2026-11-11T08:30:00Z > bench/runs/full-run-2026-11-10.log 2>&1 &
jq -r '[.task_id,.arm,.model,.attempt,(.failure_class//"ok")]|@tsv' "$RUN/sessions.jsonl" | sort | uniq -c   # progress, once the first session has finished

# 5. After "done ->" in the log: grade with the judge, then the report with the verdict
.venv/bin/python bench/grade.py "$RUN" --truth "$T" --judge --mask-name "$OWNER"
.venv/bin/python bench/intent_report.py "$RUN" --truth "$T" --mask-name "$OWNER" \
    --out docs/bench-2026-11-intent-report.md
```

During the run: do not touch portal 149614891 (Search is rate limited and every session assumes seed state), and do not
edit `bench/intent_report.py`'s rule. Exit 2 is a pre-flight failure (nothing ran), 3 a restore that failed twice (the
portal is dirty: fix it with `restore.py`), 4 a skill or tool leak. Exit 5 means `--stop-before` was reached.

**Deadline.** The answer key's "today" is fixed at 2026-11-10 in the rules file, whatever the clock says; what breaks the
data is the first seeded future activity turning past, at **2026-11-11 09:00 UTC (10:00 Berlin)** (nothing is seeded on
2026-11-10 itself). Every session must start before then: hence `--stop-before 2026-11-11T08:30:00Z`.

**Resume.** If the run stops (crash, sleep, network drop, expired login, exit 3 after fixing the portal, exit 5), rerun
with `--resume "$RUN"` and the same `--truth`, `--rules-dir` and task selection (`--reps 10`, no `--only`):

```bash
caffeinate -ims nohup .venv/bin/python bench/intent_run.py --truth "$T" --rules-dir "$RULES_DIR" --reps 10 \
    --resume "$RUN" --stop-before 2026-11-11T08:30:00Z >> bench/runs/full-run-2026-11-10.log 2>&1 &
```

The resume re-runs the pre-flight checks and the restore, refuses if the tasks, cells, reps, answer key, jobs or rules
differ from the original run, or if the portal no longer matches the run's `baseline.json` (re-seeded or changed). It
skips every finished session (an upstream failure that already had its re-run counts as finished; one without it runs
again), keeps numbering sessions where the run stopped, and appends an entry to `meta.json` `resumes`. A last line cut
short by the crash moves to `sessions.jsonl.partial`. Grade and report read the one directory as usual. When the verdict is
REJECTED, stop (PLAN phase 4): the demo stays on plugin skills. `docs/bench-2026-11-intent-report.md` quotes the run
directory name and holds no record names; read it once for real names before it leaves the machine.

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
- `grade.py`, `intent_report.py`: grader and the report with the pre-registered verdict (sections above); `judge.py`, `judge_kit.py`: the job 2 LLM judge and its human-validation kit (Phase 4).

## Gotchas

- Restart the server after code changes; it does not reload. `bench/serve.sh` wraps it in a
  restart loop so a `kill $(lsof -ti tcp:8000)` brings it back with the new code and the same env.
- Do not launch it under another project's `vercel env run`: an injected `REDIS_URL` switches the
  state store to Redis and every tool fails without `HUBSPOT_MCP_STATE_KEY`.
- CONFIRM-tier writes come back `declined` in headless runs (Claude Code declines the inline
  confirmation). That still counts as reaching the gate; nothing is applied.
- The runner's cleanup rejects every pending preview on the portal after each session. Do not
  leave your own previews pending while a run is going.
