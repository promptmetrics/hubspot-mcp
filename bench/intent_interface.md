# Intent benchmark: shared interface

The three builders (runner, grader, jobs/skills) code against this file. Where a number or name is pinned here, do not
re-decide it. Anything marked UNVERIFIED needs a check by the builder who owns it, and the builder reports the result.

Scope: arms A, B, C, D, seven tasks (`job1`..`job5`, `off_phone`, `off_globex`), 7 cells (A, B, C on sonnet and opus; D on
sonnet), Claude Code 2.1.314. Truth file: `hubspot-forecast/demo/seed/out/truth-enterprise-2026-11-10.json`
(path only; nothing from it is copied into the repo).

## 0. Facts confirmed on Claude Code 2.1.314 (3 of the 5 allowed `claude -p` checks used, sonnet, no MCP, dummy skill)

- `--append-system-prompt-file <path>` exists (documented under `--bare`, absent from the flag list) and works: the model
  answered with a codeword from the file. One mechanism for `sales-rules.md`, identical for all arms.
- `--setting-sources project` loads `<cwd>/.claude/skills/*` (the dummy skill appeared in `init.skills`). `--setting-sources ""`
  hides it. User-level skills (`~/.claude/skills`) and plugin skills did not appear under `project`.
- 19 built-in skills are in `init.skills` in every configuration. Observed set (the baseline the skills check subtracts):
  `deep-research, design, design-sync, dataviz, update-config, verify, debug, code-review, simplify, batch,
  fewer-permission-prompts, doctor, loop, schedule, claude-api, workflow-authoring, run, run-skill-generator,
  plugin-authoring`.
- `--tools ""` leaves `init.tools == []` but `init.skills` still lists skills. The list shows what is discoverable, not what
  the model can invoke: without the `Skill` tool nothing loads. `--tools Skill` gives `init.tools == ["Skill"]`.
- Without `< /dev/null` the CLI waits 3 s for stdin. Use `stdin=subprocess.DEVNULL` (run.py already does).
- UNVERIFIED: the key of a Skill tool call input (the tool schema names it `skill`); reuse of the stored OAuth token for
  `hubspot-bench` when the server arrives through `--mcp-config` with `--setting-sources ""`. Pre-flight for the second:
  `claude mcp get hubspot-bench` (no model tokens) must show it authenticated; if it does not, tell the user to
  `claude mcp add --transport http hubspot-bench https://mcp.hubspot.com` and authenticate in `/mcp`.

## 1. Runner: `bench/intent_run.py` (new file; `bench/run.py` is not edited)

Imports from `run`: `summarize`, `_ts`, `_result_text`, `_walk` (after the same `sys.path.insert` run.py uses) and from
`mcp_client`: `Surface`. It does not copy them. `run.py`'s own arms keep working untouched.

```
.venv/bin/python bench/intent_run.py \
  --server http://127.0.0.1:8000 --truth <truth.json> --portal 149614891 \
  [--rules-dir DIR] [--jobs bench/jobs.jsonl] [--skills-b DIR] [--skills-cd DIR] \
  [--arms A,B,C,D] [--models sonnet,opus] [--reps 3] [--only job1,job3] \
  [--smoke] [--dry-run] [--no-restore] [--arm-d-auto-tier] \
  [--timeout 1200] [--settle-seconds 8] [--out DIR] \
  [--secret-env HUBSPOT_MCP_SERVER_SECRET] [--token-env HUBSPOT_TOKEN_149614891] \
  [--restore-cmd "python3 demo/seed/restore.py --expect-portal 149614891 --apply --yes"] [--restore-cwd DIR] \
  [--hubspot-cli ~/.hubspot/bin/hubspot]
```

Defaults: `--rules-dir` = `$HUBSPOT_MCP_RULES_DIR`; `--skills-b` = `/Users/izzy/Documents/hubspot-plugin/.claude/skills`;
`--skills-cd` = `/Users/izzy/Documents/hubspot-plugin/skills` (the thin-skills builder creates it; it does not exist yet);
`--restore-cwd` = `/Users/izzy/Documents/hubspot-forecast`; `--out` = `bench/runs/<UTC stamp>-intent`; `--reps 3`; `--truth` required.

- Cells: `CELLS = [("A","sonnet"),("A","opus"),("B","sonnet"),("B","opus"),("C","sonnet"),("C","opus"),("D","sonnet")]`, filtered by
  `--arms` and `--models` (D has no opus cell). Model ids: `{"sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}`, passed
  as `--model <id>`; the observed id is `init.model`.
- `--smoke` = `--reps 1`, all 7 tasks, all 7 cells = 49 sessions. `--dry-run` = fakes only: no `claude`, no HubSpot, no restore, no server;
  prints the plan (cells x tasks x reps, composed prompt per task, per-arm argv with the secret masked) and runs the session loop
  once with injected fakes into a temp out dir. `--no-restore` skips every restore call (fakes and manual runs); the end-state read still happens.
- Order: `for rep: for task: for cell in rotated(cells, rep)` (rotation as run.py: `cells[rep % len:] + cells[:rep % len]`).
  Strictly sequential.
- Injectable seams (so tests pass fakes, no network): `run_session(task, cell, rep, ctx, *, claude_fn, reader, restore_fn)`.
  `claude_fn(prompt, cfg_path, argv_extra, cwd, env, timeout) -> {"events","stderr","returncode","wall_ms"}`;
  `reader.end_state(since, targets) -> dict` and `reader.read_check(since) -> dict`; `restore_fn() -> {"exit_code","output_tail"}`.
- Exit codes: 0 done, 2 pre-flight abort (rules, hash, skills source, server, auth), 3 restore failed twice (the portal is dirty;
  later sessions would be wrong, so the run stops after writing the record).

### Per-arm session setup

Each session gets a fresh `cwd = tempfile.mkdtemp(prefix="hs-intent-")`, removed afterwards. Skills are copied (`shutil.copytree`)
to `<cwd>/.claude/skills/<dir>`; arm A gets no `.claude` directory. The MCP config goes to `<cwd>/mcp-config.json` (the bearer
secret stays off the command line, as run.py does).

| Arm | server name | MCP url | `--tools` | `--setting-sources` | skills copied from |
|---|---|---|---|---|---|
| A | `hubspot-bench` | `https://mcp.hubspot.com` (no headers; OAuth via stored token) | `""` | `""` | none |
| B | `hubspot-bench` | same as A | `Bash,Skill,Read` | `project` | `--skills-b` |
| C | `hubspot-intent` | `<server>/mcp/intent` + `Authorization: Bearer <secret>` | `Skill` | `project` | `--skills-cd` |
| D | `hubspot-grouped` | `<server>/mcp/grouped` + bearer | `Skill` | `project` | `--skills-cd` |

Common argv: `claude -p <composed prompt> --output-format stream-json --verbose --no-session-persistence --strict-mcp-config
--mcp-config <cwd>/mcp-config.json --permission-mode bypassPermissions --model <id> --append-system-prompt-file <run dir>/sales-rules.md
--setting-sources <..> --tools <..>`. Run with `stdin=DEVNULL`, `cwd=<cwd>`, `timeout=--timeout`.

Session environment: `os.environ` copy minus `CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`,
`HUBSPOT_TOKEN_149614891` (and any `HUBSPOT_TOKEN_*`), `HUBSPOT_PRIVATE_APP_TOKEN`, `SEED_DEMO_USER_EMAIL`, the `--secret-env` variable
(the model's Bash tool in arm B inherits this environment, so no secret may be in it). Arm B adds `HUBSPOT_NO_AUTO_UPGRADE=1` and
prepends `~/.hubspot/bin` to `PATH`. `meta.env_stripped` lists the names.

Arm D write tier: the grouped surface reaches the write gate through `server._run_domain_tool(..., mode="grouped")`, so forcing preview
for D is a server-side switch. The server builder owns the env var; the runner constant is `D_FORCE_PREVIEW_ENV =
"HUBSPOT_MCP_GROUPED_FORCE_PREVIEW"` (value `1`, off by default in the server, tested there). The runner cannot change a running
server: it prints which env the server must have been started with and records `meta.arm_d_tier = "auto"` when
`--arm-d-auto-tier` is passed, else `"preview"`. It verifies the running server: `get_sales_rules` on `/mcp/grouped` returns `force_preview`
(true when the variable is `1` in the server process); a value that differs from the requested tier aborts pre-flight, and the value seen is
recorded as `meta.arm_d_force_preview_observed` and in `tools_list.json`.

### Pre-flight (abort with exit 2 and a one-line reason)

1. `claude --version` recorded; `hubspot --version` recorded for arm B (expect 0.17.0; mismatch with `meta.expected_cli` is a warning, not an abort).
2. Skills sources exist and each contains `*/SKILL.md`. `arm_expected_skills[X]` = the `name:` from each SKILL.md frontmatter (directory name if absent).
3. Rules: section 6. 4. `Surface(<server>/mcp/intent).initialize()` and `Surface(<server>/mcp/grouped).initialize()`; `tools/list` counts go to `tools_list.json`
   (expect 13 `pronova_*` on C, 12 on D); a connection error or a missing `claude` binary is an abort, not a traceback. 5. Unless `--no-restore`: one restore, then the baseline read (section 2).
6. Truth file `meta.as_of` equals the rules `as_of`. 7. Portal identity (arms A and B): one read-only session asks the official MCP (`get_organization_details`) and, for B, the CLI
(`hubspot whoami`, only `portalId` printed) which portal they write to; any id other than `--portal` aborts. The ids go to `meta.portal_identity`.
8. Arm B isolation: its session directory carries `.claude/settings.json` with Read deny rules and a sandbox `denyRead` over the workspace, `~/.claude`, `~/.ssh`, `~/.aws`; its environment is an allowlist (see README).
Run exit codes: 0 done, 2 pre-flight abort, 3 restore failed twice, 4 a session saw skills or tools its arm must not have (stop after that session's restore).

### Skills and tools check (per session, from the `init` event; a violation is `failure_class="harness"`, session not graded)

- `skills_unexpected = init.skills - BUILTIN_SKILLS - arm_expected_skills[arm]` must be empty (A: expected set is empty, so `init.skills == BUILTIN_SKILLS`).
- `skills_missing = arm_expected_skills[arm] - init.skills` must be empty (B, C, D).
- `tools_ok`: non-`mcp__` tools in `init.tools` equal A `[]`, B `{Bash, Skill, Read}`, C and D `{Skill}`; every `mcp_servers` entry has status `connected`
  (anything else: `failure_class="upstream"`, detail `mcp_<status>`; OAuth expiry shows up here).
- `BUILTIN_SKILLS` is the 19-name set in section 0, a constant in `intent_run.py` keyed to the pinned CLI version. A change in CLI version fails loudly with the diff.
- `skills_loaded` = names invoked: every `tool_use` named `Skill`, `input.skill` (UNVERIFIED key; fall back to `input.name`). Order preserved, duplicates kept.

## 2. Session record (`sessions.jsonl`, one JSON object per line, one line per attempt)

```
{
 "n": 12, "rep": 1, "task_id": "job3", "kind": "write", "truth_key": "job3",
 "arm": "C", "model": "sonnet", "model_id": "claude-sonnet-5-5", "model_observed": "claude-sonnet-5-5",
 "attempt": 1, "rerun_of": null,                         // attempt 2 carries rerun_of = n of attempt 1
 "since": "<UTC iso, taken just before launching claude>", "wall_ms": 0, "returncode": 0,
 "prompt_sent": "<exact composed text>",
 "init": {"tools": [], "mcp_servers": [], "skills": [], "model": ""},
 "skills_expected": [], "skills_unexpected": [], "skills_missing": [], "skills_loaded": [], "tools_ok": true,
 "summary": { ...summarize(events), unchanged },
 "mcp_calls": [{"server": "hubspot-intent", "tool": "pronova_get_deal", "is_error": false, "latency_ms": 0}],   // name split on "__"
 "cli_calls": [{"command": "<first 300 chars>", "is_error": false}],                                          // arm B Bash calls only
 "answer_block": {"parsed": null, "raw": null, "block_count": 0, "error": null},
 "confirmations": {"requested": 0, "write_tool_calls": 0},
 "end_state": null,                                       // write tasks: section 2.2
 "read_check": null,                                      // read tasks: section 2.3
 "restore": null,                                         // {"ran": true, "exit_code": 0, "output_tail": "<last 2000 chars>", "attempts": 1}
 "failure_class": null, "failure_detail": "",
 "stderr_tail": "<last 4000 chars>"
}
```

Events go to `events-<n:03d>-<task>-<arm>-<model>-r<rep>-a<attempt>.jsonl` as run.py does. `summary["final_text"]` is capped at 8000 chars
and must not be the source of the answer block: parse from the full `result` event's `result` string (fallback: the text of the last assistant message).
The record also carries that full text as `result_text` (capped at 60000 chars); the grader scans it, not `final_text`, for the wrong Jane's phone number.
`summary.hubspot_tool_names` and `summary.hubspot_tool_errors` are rebuilt from `mcp_calls` (run.py's own prefix, `mcp__hubspot__`, never matches this runner's server names).

### 2.1 Answer block parsing (deterministic, function `extract_answer_block(text) -> dict`)

Take every fenced block opened by exactly ```` ```json ````. `block_count` = how many. Use the LAST one. `raw` = its inner text. `parsed` = `json.loads(raw)`.
`error` is `"no_block"` (none found), `"invalid_json"` (parse failed), or `"schema: <first violation path>"` (parsed, but the task's `answer_schema` fails).
On `schema` errors `parsed` is still set; the grader scores it and reports `answer_ok=false`. No fallback to earlier blocks.
The schema check is a subset validator (`type`, `required`, `properties`, `items`, `enum`) in `intent_run.py`, reused by `grade.py` by import; no new dependency.

### 2.2 `end_state` (write tasks only; read after the session, before restore)

Reader module: `bench/portal_read.py`, `PortalReader`, built on `httpx`, token from `--token-env`, base `https://api.hubapi.com`
(portal 149614891 is EU-hosted; the builder takes the base URL and the seed tag constants from `demo/seed/seed.py` and `portal_state.py` by reading them, not importing
`seed`). It issues GETs, batch-read POSTs and search POSTs only. A write method on this client raises. Search rate: at most 4 requests per second.

Baseline: after the pre-flight restore, `PortalReader.baseline()` reads every seeded deal, contact and company (found by seed-tag search, as `portal_state.search_all`) with the
property sets below and stores `baseline.json` in the run dir: `{"deals": {<dealname>: {"id", "props"}}, "contacts": {<email>: {...}}, "companies": {<name>: {...}}}`.
Natural keys: deals by `dealname`, contacts by `email`, companies by `name`. Owner ids stay raw (no email is written).

Property sets: deals `dealname, amount, deal_currency_code, closedate, pipeline, dealstage, hubspot_owner_id, hs_manual_forecast_category, hs_next_step, description, dealtype`
(`pipeline` and `dealstage` mapped to labels); contacts `email, firstname, lastname, jobtitle, phone, company, hubspot_owner_id`; companies `name, domain, hubspot_owner_id`.
Engagement properties: tasks `hs_task_subject, hs_task_body, hs_task_status, hs_task_priority, hs_timestamp, hubspot_owner_id`; notes `hs_note_body, hs_timestamp`;
meetings `hs_meeting_title, hs_meeting_body, hs_meeting_outcome, hs_meeting_start_time, hs_meeting_end_time, hs_timestamp`; calls `hs_call_title, hs_call_body, hs_call_status, hs_call_direction, hs_timestamp`;
emails `hs_email_subject, hs_email_text, hs_email_direction, hs_timestamp`.

`end_state` shape. Values are HubSpot's raw strings (no date math in the reader); the grader normalises dates in `tz`.

```
{
 "read_at": "<UTC iso>", "tz": "Europe/Berlin", "rounds": 2, "stable": true,
 "target_names": ["Acme Corp renewal"],                    // from the truth file for this task (job5: its 31 deals; job1, job2, job4, off_phone: [])
 "deals_target": {"<dealname>": {"<prop>": "<value|null>"}},       // current props of each target deal, batch-read by id (index-independent)
 "deals_changed": {"<dealname>": {"changes": {"<prop>": {"from": "<baseline|null>", "to": "<now|null>"}}}},   // every baseline deal whose props differ
 "deals_missing": ["<dealname>"],                          // archived or deleted
 "deals_created": [{"dealname": "", "props": {}}],         // deals created after `since`
 "contacts_changed": {"<email>": {"name": "Jane Doe", "company": "Acme Corp", "changes": {}}},
 "companies_changed": {"<name>": {"changes": {}}},
 "created": {
   "tasks":    [{"id": "", "title": "", "body": "", "status": "", "priority": "", "due": "<hs_timestamp raw>", "owner_id": "", "associations": {"deals": ["<dealname>"], "companies": ["<name>"], "contacts": ["<first last>"]}}],
   "notes":    [{"id": "", "body": "", "timestamp": "", "associations": {}}],
   "meetings": [{"id": "", "title": "", "body": "", "outcome": "", "start": "", "end": "", "timestamp": "", "associations": {}}],
   "calls":    [{"id": "", "title": "", "body": "", "status": "", "direction": "", "timestamp": "", "associations": {}}],
   "emails":   [{"id": "", "subject": "", "body": "", "direction": "", "timestamp": "", "associations": {}}],
   "contacts": [{"id": "", "firstname": "", "lastname": "", "email": "", "jobtitle": "", "phone": "", "company": "", "associations": {"companies": ["<name>"], "deals": ["<dealname>"]}}]
 },
 "counts": {"tasks": 0, "notes": 0, "meetings": 0, "calls": 0, "emails": 0, "contacts": 0, "deals_created": 0}
}
```

- `created.*` = search with `hs_createdate >= since - 60 s` (`createdate` for contacts), then association reads to deals, companies and contacts, translated to the natural keys.
  Restore runs after every write session, so the portal is at seed state when a session starts and the margin only covers clock skew. Search is eventually consistent:
  wait `--settle-seconds`, read, wait again, read; repeat until two consecutive reads give identical `counts` (max 6 rounds); `stable=false` if it never settled (the grader notes it).
- `deals_target`, `deals_changed`, `contacts_changed`, `companies_changed` come from batch-read by baseline id, which does not depend on the search index.
- Record `{"error": "<text>"}` in place of `end_state` content if the read fails after 3 retries with backoff (429, 5xx): `failure_class="upstream"`, `failure_detail="end_state_read"`.

### 2.3 `read_check` (read tasks)

Same reader, cheaper: `{"ok": true, "deals_changed": 0, "contacts_changed": 0, "companies_changed": 0, "created": {"tasks": 0, "notes": 0, "meetings": 0, "calls": 0, "emails": 0, "contacts": 0}, "changed_names": []}`.
Batch-read by baseline id (deals, contacts, companies), plus the created-since searches. `ok=false` if any count is non-zero: the arm wrote on a read task. That session is
`failure_class="model"`, `failure_detail="wrote_on_read_task"`, and a restore runs. `deals_changed` here compares only the property sets above, so engagement associations
(which move HubSpot's computed fields) do not count.

### 2.4 Restore

After every write session (after `end_state` is stored) and after a read session whose `read_check.ok` is false: run `--restore-cmd` in `--restore-cwd` with the token env,
`subprocess.run(shlex.split(cmd), cwd=..., capture_output=True, text=True, timeout=600)`. Record `{"ran": true, "exit_code", "output_tail": last 2000 chars of stdout+stderr, "attempts"}`.
restore.py exit codes: 0 restored, 1 stopped or deals missing, 2 no snapshot or no token, 3 no `--yes`, 5 portal mismatch. Anything but 0: retry once, then exit the run with code 3.
The output can name records, so it stays in the gitignored run dir. `--no-restore` writes `"restore": {"ran": false, ...}`.

### 2.5 `confirmations`

`requested` = tool results (any arm) that are a pending-confirmation envelope: JSON with `status == "preview"` or a `confirmation_id`/`action_id` (arms C and D, and `/mcp`-style
gates, same detection as `run._write_outcomes`); for arms A and B, results whose text matches `confirm` on a write tool. UNVERIFIED for A and B: the runner builder reads the first real
official-MCP write result and pins the detection, and says so in the report. `write_tool_calls` = count of calls to write-capable tools (A and B: the official MCP's `manage_*`
tools and Bash `hubspot` commands containing `create|update|delete|batch|associate`; C: `pronova_apply_changes`, `pronova_propose_changes`, `pronova_log_meeting_outcome`,
`pronova_fix_past_due_close_dates`; D: the grouped write tools). Reported, never scored.

### 2.6 Failure classes (`failure_class`, null for a clean session; wrong answers are graded, not failures)

- `upstream`: any of `429`, `5xx`, `overloaded`, `rate limit`, `ECONNRESET`, `invalid_grant`, `token expired`, `needs-auth` in `stderr`, in `result.result`, or in 2 or more MCP/Bash tool results;
  an `mcp_servers` status other than `connected`; end-state read failure; baseline fingerprint mismatch at session start (stale state). Re-run once (attempt 2, after a restore for write tasks).
  Both attempts are kept; the grader uses the last attempt and lists upstream failures separately.
- `harness`: skills or tools check failed (`skills_unexpected`, `tools:` also stop the run, exit 4); the init event's model does not start with the requested id (`model_mismatch`); claude not found; rules fingerprint mismatch; reader or restore exception. A timeout with no upstream signature is NOT harness (below).
- `model`: timeout without upstream signature (`failure_detail="timeout"`); `answer_block.error` is `no_block` or `invalid_json`; wrote on a read task.
  A `schema:` error is a graded miss, not a failure class.

## 3. Shared text

Composed prompt = `<prompt verbatim from truth.json>` + `"\n\n"` + `<suffix for kind>` + `"\n\n"` + `<answer instruction for task>`. Identical for every arm. Recorded in `meta.suffix_read`,
`meta.suffix_write`, `meta.answer_prefix`, `meta.answers[<task id>]` and per session in `prompt_sent`.

```python
SUFFIX_READ = "Do not change anything in HubSpot."
SUFFIX_WRITE = "You have my approval to make these changes in HubSpot; if a tool asks for confirmation, confirm it."
ANSWER_PREFIX = (
    "When you are done, end your reply with one fenced ```json block and nothing after it, in exactly this shape. "
    "Dates are YYYY-MM-DD, amounts are plain numbers, and deals are named by their exact deal name."
)
# answer instruction = ANSWER_PREFIX + " " + job["answer_rule"] + "\n```json\n" + job["answer_example"] + "\n```"
```

`answer_rule` and `answer_example` per task live in `bench/jobs.jsonl` (section 4). The three write tasks share one block. The grader reads what the CRM holds
(`end_state`), not the block, for correctness; the block feeds the honesty checks.

Schema summary (the full JSON Schema is `answer_schema` in each jobs line; deals are identified by deal name everywhere):

| task | top-level keys |
|---|---|
| job1 | `flagged_deals: [{deal, codes[], reason}]` |
| job2 | `contact{name,company,phone,job_title}`, `call_tomorrow{title,date}`, `open_deals[{deal,stage,amount,currency,close_date}]`, `open_tasks[{title,due}]`, `buying_group[{name,job_title}]`, `never_contacted[name]`, `never_contacted_outside_deal[name]`, `recent_activity[{date,type,title}]` |
| job4 | `period`, `currency`, `figures{closed_won,commit,best_case,pipeline,weighted,supported_commit}`, `at_risk_commit_deals[{deal,codes[]}]`, `slipped_count` |
| job3, job5, off_globex | `deals_updated[{deal, fields{}}]`, `records_created[{type,title,deal,company}]`, `not_done[]` |
| off_phone | `needs_clarification`, `question`, `answers[{name,company,phone,colleagues[{name,job_title}]}]` |

## 4. `bench/jobs.jsonl` (7 lines; prompts verbatim from truth.json; one JSON object per line, UTF-8, `ensure_ascii=False`)

Fields: `id`, `kind` (read|write), `truth_key` (dotted path into the truth file), `prompt`, `answer_rule`, `answer_example`, `answer_schema`.
A test asserts each `prompt` equals the truth file's `prompt` at `truth_key` (skipped when the truth file is absent) and that each `answer_example` parses and validates against its own `answer_schema` once its placeholders are replaced by type-correct values.

```jsonl
{"id":"job1","kind":"read","truth_key":"job1","prompt":"Which of my deals need attention today, and why?","answer_rule":"List every deal that needs attention, without cutting the list. codes use only the codes the Triage section of the sales rules reports.","answer_example":"{\"flagged_deals\":[{\"deal\":\"Example Deal\",\"codes\":[\"OVERDUE\"],\"reason\":\"one line\"}]}","answer_schema":{"type":"object","required":["flagged_deals"],"properties":{"flagged_deals":{"type":"array","items":{"type":"object","required":["deal","codes"],"properties":{"deal":{"type":"string"},"codes":{"type":"array","items":{"type":"string"}},"reason":{"type":"string"}}}}}}}
{"id":"job2","kind":"read","truth_key":"job2","prompt":"Prep me for my call with Jane Doe at Acme tomorrow.","answer_rule":"contact is the Jane Doe you are calling. never_contacted lists people on the open deal with no logged contact; never_contacted_outside_deal lists contacts at the company with no logged contact who are not on the open deal. Use [] or null for anything you did not find.","answer_example":"{\"contact\":{\"name\":\"\",\"company\":\"\",\"phone\":\"\",\"job_title\":\"\"},\"call_tomorrow\":{\"title\":\"\",\"date\":\"YYYY-MM-DD\"},\"open_deals\":[{\"deal\":\"\",\"stage\":\"\",\"amount\":0,\"currency\":\"\",\"close_date\":\"YYYY-MM-DD\"}],\"open_tasks\":[{\"title\":\"\",\"due\":\"YYYY-MM-DD\"}],\"buying_group\":[{\"name\":\"\",\"job_title\":\"\"}],\"never_contacted\":[\"\"],\"never_contacted_outside_deal\":[\"\"],\"recent_activity\":[{\"date\":\"YYYY-MM-DD\",\"type\":\"call\",\"title\":\"\"}]}","answer_schema":{"type":"object","required":["contact","open_deals","buying_group","never_contacted"],"properties":{"contact":{"type":"object","required":["name","company","phone"],"properties":{"name":{"type":"string"},"company":{"type":"string"},"phone":{"type":"string"},"job_title":{"type":["string","null"]}}},"call_tomorrow":{"type":["object","null"]},"open_deals":{"type":"array","items":{"type":"object","required":["deal"],"properties":{"deal":{"type":"string"},"stage":{"type":["string","null"]},"amount":{"type":["number","null"]},"currency":{"type":["string","null"]},"close_date":{"type":["string","null"]}}}},"open_tasks":{"type":"array","items":{"type":"object","required":["title"],"properties":{"title":{"type":"string"},"due":{"type":["string","null"]}}}},"buying_group":{"type":"array","items":{"type":"object","required":["name"],"properties":{"name":{"type":"string"},"job_title":{"type":["string","null"]}}}},"never_contacted":{"type":"array","items":{"type":"string"}},"never_contacted_outside_deal":{"type":"array","items":{"type":"string"}},"recent_activity":{"type":"array","items":{"type":"object","required":["date","type"],"properties":{"date":{"type":"string"},"type":{"type":"string"},"title":{"type":["string","null"]}}}}}}}
{"id":"job3","kind":"write","truth_key":"job3","prompt":"Just finished with Acme: they'll pilot, close moves to Dec 15, amount €24k, I owe them the security doc by Friday, add their CFO Maria Lopez.","answer_rule":"deals_updated has one entry per deal you changed, with the values you set (close_date as YYYY-MM-DD, amount as a number). records_created has one entry per task, note, meeting, call or contact you created, with the deal and company it is attached to (null when none). not_done lists anything you did not do, with the reason.","answer_example":"{\"deals_updated\":[{\"deal\":\"Example Deal\",\"fields\":{\"close_date\":\"YYYY-MM-DD\",\"amount\":0}}],\"records_created\":[{\"type\":\"task\",\"title\":\"\",\"deal\":null,\"company\":null}],\"not_done\":[]}","answer_schema":{"type":"object","required":["deals_updated","records_created","not_done"],"properties":{"deals_updated":{"type":"array","items":{"type":"object","required":["deal","fields"],"properties":{"deal":{"type":"string"},"fields":{"type":"object"}}}},"records_created":{"type":"array","items":{"type":"object","required":["type","title"],"properties":{"type":{"type":"string","enum":["task","note","meeting","call","contact"]},"title":{"type":"string"},"deal":{"type":["string","null"]},"company":{"type":["string","null"]}}}},"not_done":{"type":"array","items":{"type":"string"}}}}}
{"id":"job4","kind":"read","truth_key":"job4","prompt":"What will I close this quarter, and what's at risk?","answer_rule":"Report the figures for this fiscal quarter in your home currency, following the Forecast figures section of the sales rules. at_risk_commit_deals lists the Commit deals you remove to get supported_commit, with their reason codes. slipped_count is the number of open deals slipped under the rules.","answer_example":"{\"period\":\"YYYY-Qn\",\"currency\":\"XXX\",\"figures\":{\"closed_won\":0,\"commit\":0,\"best_case\":0,\"pipeline\":0,\"weighted\":0,\"supported_commit\":0},\"at_risk_commit_deals\":[{\"deal\":\"\",\"codes\":[\"\"]}],\"slipped_count\":0}","answer_schema":{"type":"object","required":["figures"],"properties":{"period":{"type":["string","null"]},"currency":{"type":["string","null"]},"figures":{"type":"object","required":["closed_won","commit","best_case","pipeline","weighted","supported_commit"],"properties":{"closed_won":{"type":"number"},"commit":{"type":"number"},"best_case":{"type":"number"},"pipeline":{"type":"number"},"weighted":{"type":"number"},"supported_commit":{"type":"number"}}},"at_risk_commit_deals":{"type":"array","items":{"type":"object","required":["deal"],"properties":{"deal":{"type":"string"},"codes":{"type":"array","items":{"type":"string"}}}}},"slipped_count":{"type":["integer","null"]}}}}
{"id":"job5","kind":"write","truth_key":"job5","prompt":"Move every past-due close date on my open deals to the end of next month and add a task on each to confirm timing.","answer_rule":"deals_updated has one entry per deal you changed, with the values you set (close_date as YYYY-MM-DD, amount as a number). records_created has one entry per task, note, meeting, call or contact you created, with the deal and company it is attached to (null when none). not_done lists anything you did not do, with the reason.","answer_example":"{\"deals_updated\":[{\"deal\":\"Example Deal\",\"fields\":{\"close_date\":\"YYYY-MM-DD\",\"amount\":0}}],\"records_created\":[{\"type\":\"task\",\"title\":\"\",\"deal\":null,\"company\":null}],\"not_done\":[]}","answer_schema":{"type":"object","required":["deals_updated","records_created","not_done"],"properties":{"deals_updated":{"type":"array","items":{"type":"object","required":["deal","fields"],"properties":{"deal":{"type":"string"},"fields":{"type":"object"}}}},"records_created":{"type":"array","items":{"type":"object","required":["type","title"],"properties":{"type":{"type":"string","enum":["task","note","meeting","call","contact"]},"title":{"type":"string"},"deal":{"type":["string","null"]},"company":{"type":["string","null"]}}}},"not_done":{"type":"array","items":{"type":"string"}}}}}
{"id":"off_phone","kind":"read","truth_key":"offscript.jane_doe_phone_and_colleagues","prompt":"What's Jane Doe's phone and who else is at her company?","answer_rule":"If you need to ask me something, put the question in question, set needs_clarification to true, and still list what you found.","answer_example":"{\"needs_clarification\":false,\"question\":null,\"answers\":[{\"name\":\"\",\"company\":\"\",\"phone\":\"\",\"colleagues\":[{\"name\":\"\",\"job_title\":\"\"}]}]}","answer_schema":{"type":"object","required":["needs_clarification","answers"],"properties":{"needs_clarification":{"type":"boolean"},"question":{"type":["string","null"]},"answers":{"type":"array","items":{"type":"object","required":["name","company","phone"],"properties":{"name":{"type":"string"},"company":{"type":"string"},"phone":{"type":"string"},"colleagues":{"type":"array","items":{"type":"object","required":["name"],"properties":{"name":{"type":"string"},"job_title":{"type":["string","null"]}}}}}}}}}}
{"id":"off_globex","kind":"write","truth_key":"offscript.globex_follow_up_task","prompt":"Create a follow-up task for the Globex deal.","answer_rule":"deals_updated has one entry per deal you changed, with the values you set (close_date as YYYY-MM-DD, amount as a number). records_created has one entry per task, note, meeting, call or contact you created, with the deal and company it is attached to (null when none). not_done lists anything you did not do, with the reason.","answer_example":"{\"deals_updated\":[{\"deal\":\"Example Deal\",\"fields\":{\"close_date\":\"YYYY-MM-DD\",\"amount\":0}}],\"records_created\":[{\"type\":\"task\",\"title\":\"\",\"deal\":null,\"company\":null}],\"not_done\":[]}","answer_schema":{"type":"object","required":["deals_updated","records_created","not_done"],"properties":{"deals_updated":{"type":"array","items":{"type":"object","required":["deal","fields"],"properties":{"deal":{"type":"string"},"fields":{"type":"object"}}}},"records_created":{"type":"array","items":{"type":"object","required":["type","title"],"properties":{"type":{"type":"string","enum":["task","note","meeting","call","contact"]},"title":{"type":"string"},"deal":{"type":["string","null"]},"company":{"type":["string","null"]}}}},"not_done":{"type":"array","items":{"type":"string"}}}}}
```

## 5. `bench/grade.py`

```
.venv/bin/python bench/grade.py <run dir> --truth <truth.json> [--out <run dir>/grades.jsonl] [--include-superseded]
```

Reads `<run dir>/sessions.jsonl` and `<run dir>/jobs.jsonl` (the runner copies the jobs file there). Writes `grades.jsonl` (one row per graded session) and
`consistency.json`, and prints a table per task x arm x model: n, mean score, exact count, failure counts by class. Exit 0.
Pure functions, no network: `grade_job1(answer, truth)`, `grade_job2(answer, truth, raw_text)`, `grade_job3(end_state, answer, truth)`, `grade_job4(answer, truth)`,
`grade_job5(end_state, answer, truth)`, `grade_off_phone(answer, truth)`, `grade_off_globex(end_state, answer, truth)`, `grade_session(record, truth, job)`,
`consistency(rows)`, plus helpers `norm_name`, `digits`, `to_date(raw, tz)`, `jaccard`. Truth is read for owner `demo` (`meta.demo_owner`).

Helpers: `norm_name(s)` = casefold, strip, collapse whitespace. `digits(s)` = only the digits. `to_date(raw, tz)` accepts `YYYY-MM-DD`, ISO datetime (with or without offset; naive = UTC),
or epoch milliseconds as a string, converts to `tz` and returns a `date`. All date comparisons go through it, so a task due at midnight Berlin time still counts as that day.
Failed or superseded sessions: a session with `failure_class` of `upstream` or `harness` and a later attempt is `superseded=true` and skipped unless `--include-superseded`;
the last attempt is graded. If the last attempt still has `upstream` or `harness`, the row has `score=null` and is counted in the failure table, not in the arm's mean.
`model` failures score 0 (`answer_ok=false`), except a write task whose only failure is a missing or invalid answer block: it scores the CRM end state with an empty
answer, keeps `failure_class="model"`, `answer_ok=false`, `exact=false` and `details.block_missing=true`. A timeout still scores 0.

### `grades.jsonl` row

```
{"n": 12, "rep": 1, "task_id": "job3", "arm": "C", "model": "sonnet", "attempt": 1, "superseded": false,
 "failure_class": null, "answer_ok": true, "answer_error": null,
 "score": 0.0-1.0 or null, "exact": false, "checks": {"<name>": true|false|number}, "details": {},
 "violations": ["<text>"], "confirmations": 0, "write_tool_calls": 0,
 "cost_usd": 0.0, "duration_ms": 0, "num_turns": 0, "llm_judge": null}
```

`exact` = `score == 1.0` and `violations == []` and the grader's extra condition holds (job1: `code_agreement == 1`; off_phone: `colleague_recall == 1`). `clean` = `violations == []`
(null when the row has no score). `score` itself ignores violations; the summary table shows `mean`, `exact` and `clean` side by side. `llm_judge` is a hook `llm_judge(task_id, answer, final_text, truth) -> dict | None` (default `None`, never called in Phase 3).

### Scoring rules and truth paths

**job1** (`truth.job1.owners.demo.flagged[*]`: `dealname`, `codes`). `T = {norm_name(dealname)}`, `A = {norm_name(d.deal) for d in answer.flagged_deals}`.
`precision = |T∩A|/|A|`, `recall = |T∩A|/|T|`, `f1`; `score = f1`; `exact` also needs `code_agreement == 1`. `code_agreement` = share of `T∩A` deals where `set(answer.codes) ∩ TRIAGE == set(truth.codes)`,
`TRIAGE = truth.meta.thresholds.triage_codes`; codes outside `TRIAGE` (INFLATED, SANDBAG) are listed in `details.extra_codes` and ignored. `details`: `fp`, `fn`, `duplicates`, `truncated` (`|T|` > `truth.job1.owners.demo.default_list.tool_default_limit` and recall under 1.0, informational).
Empty `A` gives 0.

**job2** (`truth.job2`). Checks, each scored 0..1, `score` = their mean:
`right_jane` = `digits(contact.phone) == digits(right_jane.phone)` and `norm_name(contact.company) == norm_name(checklist.company)`; `jobtitle` = `norm_name(contact.job_title) == norm_name(checklist.jobtitle)`;
`call_tomorrow` = `to_date(call_tomorrow.date) == checklist.call_tomorrow.date`; `open_deals` = share of `checklist.open_deals[*].dealname` named in `answer.open_deals`;
`open_tasks` = share of `checklist.open_tasks[*].title` matched in `answer.open_tasks` (the answer title contains the truth title, or is contained in it and is at least 60 percent of its length); `buying_group` = share of `checklist.buying_group[*].name` named;
`never_contacted` = `|A∩T|/|A∪T|` (Jaccard) against `checklist.buying_group_never_contacted[*].name`; `timeline` = share of the past entries of `checklist.timeline[*]` (`future` is not true) with an `answer.recent_activity` entry of the same `date` and `type`; every email variant counts as `email`, and scheduled entries are neither required nor penalised (they are the `call_tomorrow` and `open_tasks` checks). The job2 answer schema fixes `recent_activity.type` to `call|meeting|email|note|task`.
Violations (`must_not_include`): the digits of `wrong_jane_phone` appear in the raw block or in `final_text`; `contact.company` normalises to `wrong_jane_company`; a deal of `wrong_jane.open_deals` is in `answer.open_deals`;
any name in `never_contacted_but_not_in_buying_group[*].name` appears in `answer.buying_group` or `answer.never_contacted` (it is allowed in `never_contacted_outside_deal`). `details.company_level_names` records what the answer put outside the deal.

**job3** (`truth.job3.expected_end_state`, target = `truth.job3.target_deal.dealname`, `as_of = truth.meta.as_of`). Each check is a bool; `score` = passed / total:
`deal_closedate` (`to_date(deals_target[target].closedate) == expected.deal.closedate`), `deal_amount` (`float(amount) == expected.deal.amount`), `deal_currency` (`deal_currency_code == expected.deal.currency`),
`deal_unchanged` (none of `expected.deal.unchanged` in `deals_changed[target].changes`; `dealstage`, `pipeline`, `hs_manual_forecast_category`, `hubspot_owner_id`, `dealname`),
`meeting` (exactly one in `created.meetings`, `to_date(start or timestamp) == expected.meeting.date`, associated with the target deal and a company whose `norm_name` starts with the lowercase company key in `expected.meeting.associated_with`, here `acme`),
`note` (exactly one in `created.notes`, body contains every `expected.note.mentions` entry case-insensitively, same associations), `task` (exactly one in `created.tasks`, title contains `security doc`, `to_date(due) == expected.task.due`, `status == NOT_STARTED`, same associations),
`contact` (exactly one in `created.contacts`, `firstname`/`lastname` match, `norm_name(jobtitle)` in `{cfo, chief financial officer}`, associated with the Acme company and the target deal).
Violations: any deal in `deals_changed` other than the target; `created.calls`, `created.emails`, a second contact, task, note or meeting; any `contacts_changed` entry named Jane Doe; any `deals_created`; `deals_missing`.
`details`: `confirmations` (from the session), `claim_mismatch` (names in `answer.deals_updated` not present in `deals_changed`, and records claimed but absent from `created`), `meeting_logged_as_call` (a call exists and no meeting does: no credit).

**job4** (`truth.job4.owners.demo`). `figures` map: `closed_won`->`closed_won.total`, `commit`->`hubspot_commit.total`, `best_case`->`best_case.total`, `pipeline`->`pipeline.total`,
`weighted`->`hubspot_weighted.total`, `supported_commit`->`supported_commit.total`. Per-figure pass = `abs(a - t) <= 0.005 * abs(t)` (`t == 0`: `a == 0`); tolerance read from the constant `TOLERANCE = 0.005`
(the truth text says ±0.5%). Plus `slipped_count == slipped_by_arm_rules.count`, and `at_risk_commit_deals` names versus `supported_commit_removed[*].dealname` as F1 (code agreement in `details`).
`score = (figures_passed + slipped_ok + at_risk_f1) / 8`; `details` lists each figure's answer, truth and relative error. `currency` and `period` are recorded (`period == truth.job4.period.label`), not scored.

**job5** (`truth.job5`: `deals[*].dealname`, `new_close_date`, `count`, `tasks_expected`). `E = {norm_name(dealname)}` (31 deals).
`deals_correct` = names in `E` with `to_date(deals_target[name].closedate) == new_close_date`; `wrong_date` = in `E` and changed to another date; `not_changed` = `E` minus `deals_changed` names.
`tasks_ok` = names in `E` having exactly one `created.tasks` entry associated with that deal and whose title plus body has every word of `confirm timing` starting a word, in any order (case-insensitive); `task_dupes` = deals with 2 or more; `task_orphans` = created tasks not associated with any deal in `E`.
`score = (deals_correct + tasks_ok) / (2 * |E|)`. Violations: a `deals_changed` name not in `E` (another owner's deal, a non-past-due deal, a closed deal; the count is `details.extra_deals_touched`); a property other than `closedate` changed on a deal in `E`;
`task_orphans > 0`; any created record of another type. `details`: `deals_changed_count`, `expected_count`, `partial_report_honest` = (`deals_correct == |E|` and `tasks_ok == |E|`) or `not_done` non-empty or the count claimed in `answer.deals_updated` equals `deals_correct`.
The official MCP write cap (`truth.job5.official_mcp_write_cap`, 10) is recorded in `details`, not scored.

**off_phone** (`truth.offscript.jane_doe_phone_and_colleagues.janes[*]`: `phone`, `company`, `other_contacts_at_company[*].name`). Each `answer.answers` entry matches a Jane by `digits(phone)`.
Behaviour (`details.behaviour`): `both` (every Jane matched once, each with her own company; no colleague of one company listed under the other); `asked` (`needs_clarification == true`, `question` non-empty, and every listed entry is consistent);
`one_undisclosed` (one Jane matched, `needs_clarification == false`); `mixed` (a phone or colleague under the wrong company); `none`. `score = 1` for `both` or `asked`, else 0. `details.colleague_recall` = share of the colleague names of each answered Jane found. `exact` additionally needs `colleague_recall == 1`.

**off_globex** (`truth.offscript.globex_follow_up_task`, target = `target_deal.dealname`). Checks: `task` (exactly one in `created.tasks`, title contains `follow-up` or `follow up`, `status == NOT_STARTED`, associated with the target deal),
`deal_unchanged` (target not in `deals_changed`). Violations: any other created record; any other deal in `deals_changed`; a second task. `score = checks passed / 2`.

### Consistency (`consistency.json`, rows per task x arm x model with 2 or more graded reps)

`{"task_id","arm","model","n_reps","metric","value"}`. job1: `jaccard_flagged_mean` (mean pairwise Jaccard of the parsed `flagged_deals` name sets); job4: for each figure `mean`, `stdev` (population), `cv` (`stdev/mean`, null when mean is 0);
all tasks: `score_mean`, `score_stdev`, `exact_rate`.

## 6. `sales-rules.md` and the rules YAML

Mechanism: one file, `--append-system-prompt-file <run dir>/sales-rules.md`, in every arm (verified, section 0). Its text is rendered from the YAML the intent server reads, so no arm is graded against a rule it was not given.

How the intent server finds the YAML (`src/hubspot_mcp/intent/rules.py::rules_path`): `$HUBSPOT_MCP_RULES_DIR/<portal>.yaml`, then bundled `<portal>.yaml`, then bundled `default.yaml`; the portal id must be all digits.
The seed writes `demo/seed/out/rules-enterprise.yaml`, which is not named by portal, so the operator copies it:

```bash
RULES_DIR=$(mktemp -d)                                   # outside any repo
cp /Users/izzy/Documents/hubspot-forecast/demo/seed/out/rules-enterprise.yaml "$RULES_DIR/149614891.yaml"
HUBSPOT_MCP_RULES_DIR="$RULES_DIR" HUBSPOT_PORTAL=149614891 bench/serve.sh        # the server re-reads the file on every call
.venv/bin/python bench/intent_run.py --rules-dir "$RULES_DIR" --portal 149614891 --truth <truth.json> --smoke
```

The YAML holds `default_owner` (the demo user's email), so neither the YAML, the rendered text nor any excerpt of them goes into a repo file. The rendered `sales-rules.md` lives only in the run dir (gitignored).

Runner steps (pre-flight 3):
1. `<rules-dir>/<portal>.yaml` must exist; its `as_of` must equal `truth.meta.as_of` (`2026-11-10`).
2. Render: `subprocess.run([sys.executable, "-m", "hubspot_mcp.intent.rules", "--portal", "149614891", "--out", "<run dir>/sales-rules.md"], env={..., "HUBSPOT_MCP_RULES_DIR": rules_dir})`
   (CLI: `--portal`, `--out`; stdout when `--out` is absent; exit 1 with `error:` on stderr when the file is invalid).
3. Parse `Rules fingerprint: <12 hex>` from the rendered text. Call `pronova_get_sales_rules` on `/mcp/intent` and `get_sales_rules` on `/mcp/grouped` (both return `rules_hash`); each must equal the rendered fingerprint, else abort (the server runs different rules, or a stale process).
4. `meta` records `rules_hash`, `sales_rules_sha256`, `as_of`, and the rules YAML path (not its contents).

The rendered text states the as-of date, the six reason codes with their scopes, the thresholds, the triage weights, the forecast figure definitions, the currency handling, and who "my" means. The runner adds nothing to it.

## 7. Run directory

```
bench/runs/<stamp>-intent/
  meta.json            started, argv (secrets masked), claude_version, hubspot_cli_version, models, cells, tasks, reps, portal, truth_path, truth_sha256, jobs_sha256,
                       rules_hash, sales_rules_sha256, suffix_read, suffix_write, answer_prefix, answers{task: text}, builtin_skills, arm_expected_skills,
                       arm_d_tier, env_stripped[], restore_cmd, timeout
  jobs.jsonl           copy of the jobs file used
  sales-rules.md       rendered rules (contains the owner email; stays here)
  baseline.json        seeded state read after the pre-flight restore
  tools_list.json      tools/list measurements for C and D
  sessions.jsonl       section 2
  events-*.jsonl       raw stream-json per attempt
  grades.jsonl, consistency.json    written by grade.py
```

## 8. Open items

- `HUBSPOT_MCP_GROUPED_FORCE_PREVIEW` is a proposed name; the server builder confirms or renames, and the runner constant follows.
- Confirmation detection for arms A and B (section 2.5) and the Skill-call input key (section 0) need a first real sample.
- `hubspot-plugin/skills/` does not exist yet; pre-flight fails with exit 2 until the thin skills land.
- HubSpot search is eventually consistent; `--settle-seconds` and the stability loop mitigate it, and `stable=false` rows get a note in the report.
- Two of the five allowed `claude -p` checks remain.
