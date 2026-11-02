# demo: two-portal side-by-side kit

`kit.py` sets up, runs and scores a side-by-side demo of HubSpot's official MCP (left) against the Pronova intent server (right). Each side has its own portal with identical seeded data, so a write act on one side never changes what the other side sees.

It does no HubSpot writes itself. The seed scripts in `hubspot-forecast/demo/seed` (`preflight.py`, `reset.py`, `seed.py`, `restore.py`) do those; the kit calls them. It reads the portals only through `bench/portal_read.PortalReader`, which refuses any request that could write.

## One-off setup

1. Create the right-hand portal and finish the hand steps in `hubspot-forecast/docs/phase-0-hand-steps.md` (EUR home currency, USD enabled, Sales > Forecast opened once, the "Demo Sales Team" team, a private app with the seed's scopes).
2. Register the official HubSpot MCP with Claude Code under the name the left side will use (default `hubspot-bench`) and sign in to it once.
3. Export both tokens in the shell that runs the kit, plus `SEED_DEMO_USER_EMAIL` (the demo user's address on both portals) and `SEED_OWNER_EMAIL_BASE` (an address with an `@`, from which the seed derives the other owners). The kit stores variable names only; `check` and `prepare` refuse without the two seed variables.

```bash
export HUBSPOT_TOKEN_<left id>=...      # set in your shell, never in a file
export HUBSPOT_TOKEN_<right id>=...
uv run python demo/kit.py setup --left-portal <left id> --right-portal <right id>
```

`setup` writes `~/hubspot-demo/portals.json`: both portal ids, the labels `left` and `right`, the token variable names (`HUBSPOT_TOKEN_<id>`) and the left side's MCP server name (`--left-server`, default `hubspot-bench`). Use `--home DIR` on any command to keep the folder somewhere else.

## A demo, start to finish

```bash
uv run python demo/kit.py check --day today          # read-only preflight on both portals
uv run python demo/kit.py prepare --day today        # prints what it would do, then refuses
uv run python demo/kit.py prepare --day today --yes  # reset and seed both portals, write ~/hubspot-demo
~/hubspot-demo/left/launch.sh                        # window 1
~/hubspot-demo/right/launch.sh                       # window 2 (needs HUBSPOT_TOKEN_<right id> exported)
# follow ~/hubspot-demo/script.md, Acts 1 to 5
uv run python demo/kit.py restore --yes              # save both end states, then restore both portals
uv run python demo/kit.py compare                    # ~/hubspot-demo/report-<day>.md
```

| Command | What it does |
|---|---|
| `check --day D` | Runs `preflight.py` on both portals and prints one row per side, the failed checks and any warnings. Exit 0 only when both pass. |
| `prepare --day D --yes` | Validates the day on both portals with `seed.py --plan` and `preflight.py` (both must pass; a failed `leftovers` check alone is tolerated, because `reset.py` clears it), deletes `state.json`, then for each portal runs `reset.py` and `seed.py --apply`. Stops on the first failure. Then requires both seed manifests to hold the same deal, company, contact and goal counts, and writes the folders below. Without `--yes` it prints the plan and exits 2. |
| `restore --yes` | Reads each portal's end state against the seed snapshot and saves it to `runs/<day>/endstate-<side>.json` (emails masked). Then runs `restore.py` on both portals. A failed read stops before any restore (`--skip-capture` overrides). A failed restore on one portal does not stop the other. A repeated `restore` keeps the end states this prepare already saved; `--recapture` reads them again. |
| `compare` | Reads both sessions' transcripts and writes `report-<day>.md`. It ignores an end state captured before the latest `prepare`. |

`D` is `YYYY-MM-DD` or `today`. The seed refuses the first and last day of a quarter.

## What `prepare` writes to `~/hubspot-demo`

- `left/mcp.json`: the official HubSpot MCP (`type http`, `https://mcp.hubspot.com`) under the configured server name. `left/` has no skills.
- `right/mcp.json`: the Pronova stdio server (`uv --directory <repo> run hubspot-mcp --portal <right id> --mode token --surface intent`) with `HUBSPOT_MCP_RULES_DIR` set. The token is not in the file: the server inherits `HUBSPOT_TOKEN_<right id>` from the shell.
- `right/.claude/skills/`: a copy of every folder in `hubspot-plugin/skills`.
- `left/launch.sh`, `right/launch.sh`: `claude --strict-mcp-config --mcp-config mcp.json --setting-sources project --model claude-sonnet-5-5 --append-system-prompt-file ../sales-rules.md --allowedTools "mcp__<server>"` (the right side adds `"Skill"`). Each script unsets the other portal's token variable first.
- `sales-rules.md`: the same file for both sides, rendered from the rules YAML with `hubspot_mcp.intent.rules.render_sales_rules_md`.
- `rules/<right id>.yaml`: the day's rules file. The Pronova server rereads it on every call, which is what Act 5 relies on.
- `script.md`: Acts 1 to 5. The prompts come from the day's answer key: triage (job 1), forecast (job 4), log the meeting (job 3), bulk fix (job 5), and Act 5, the triage prompt again after a rule change. The forecast comes before the write acts because its key describes the seeded state; Acts 3 and 4 move deal amounts and close dates. The script starts with a step that checks the left window's OAuth login names the left portal.
- `state.json`: the day, the prepare time, portal ids, seed folder and the answer-key paths. `compare` uses the prepare time to ignore older sessions.

Run the write acts (3 and 4) one side at a time. A side that waits on a confirmation form while the other side writes breaks the comparison.

## What `compare` reports

It reads `~/.claude/projects/-Users-<you>-hubspot-demo-<side>/*.jsonl` (sessions that started after the prepare time), splits the turns into acts by matching each typed prompt against `script.md`, and attaches follow-ups such as "yes" to the act before them.

Per act and side:

- wall time, split into form wait (the latency of `pronova_apply_changes` and `pronova_undo_changes`, which is the time the confirmation form waited for the person) and working time;
- model calls, HubSpot tool calls, tokens (input, cache write, cache read, output), first and last context size, tool errors;
- cost at sonnet-5.5 list prices (`bench/report.py` `PRICES`).

Quality against the day's answer key:

- **Triage (Act 1):** how many flagged deals the answer names, and how many of their codes appear on the deal's line or under its section header.
- **Forecast (Act 2):** how many of the six figures appear in the text within the key's tolerance, and how many at-risk deals it names.
- **Write acts (3 and 4):** `bench/grade.py` `grade_job3` and `grade_job5` on the captured end state. One capture holds both acts, so records are attributed to Act 4 when they are attached to a past-due deal and to Act 3 otherwise.
- **Act 5:** not graded; no key exists for a changed rule.

The kit fails with a message naming the file and line when a transcript record lacks a timestamp, message id or usage field. Console output and the report mask emails and never print a token.

## Tests

```bash
env -u HUBSPOT_TOKEN_149614891 -u HUBSPOT_PRIVATE_APP_TOKEN uv run pytest -q tests/test_demo_kit.py
uv run ruff check src tests bench demo
```
