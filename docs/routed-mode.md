# Routed surface and the session bench

Status 2026-10-01: built on branch `feat/routed-mode`, not committed, not deployed. 1052 tests
pass. The full bench and the skill bench have run on the PromptMetrics portal; results below.

## What exists

| Piece | Where |
|---|---|
| Second MCP server (four tools), split ASGI app, hosted auth per path | `src/hubspot_mcp/server.py` (`routed`, `routed_lifespan`, `_SplitSurfaces`, `_hosted_auth(path)`) |
| Shared dispatch used by both surfaces, with trace timing | `server._run_domain_tool` / `_run_domain_tool_for` |
| The three meta-tools | `src/hubspot_mcp/routed.py` |
| Jev over HTTP, keyword fallback | `src/hubspot_mcp/jev_router.py` |
| Trace append + `tool_call` / `route_decision` events | `src/hubspot_mcp/trace.py`, `server._trace_tool_call`, `routed._emit` |
| Tests | `tests/test_routed.py` (9 tests) |
| Bench | `bench/` (README there) |

## How a routed session runs

1. Host loads `tools/list`: 3 tools, about 1.6 KB.
2. Model calls `find_capabilities(task)`. One Jev request: a `choice` over 44 charters + `none`,
   44 charter booleans, 79 tool booleans. Result: charters with probabilities, tools with input
   schemas inline (router picks at p >= 0.5, plus every tool of each chosen charter, plus the
   approve/reject/undo/list-pending tools whenever a picked tool writes).
3. Model calls `load_charter(primary)`, gets the charter with a runtime header that maps the
   charter's `hubspot approve <id>` wording onto `call_hubspot(tool="hubspot_approve_write", ...)`.
4. Model calls `call_hubspot(tool, args)`. Args are validated against the tool's JSON schema,
   then dispatched through `_run_domain_tool`: not-connected guidance, entitlement check, MRTR
   resume, `handle_tool` write gate, inline confirmation. Same code as `/mcp`.

## First numbers (portal-less dry run, Haiku 4.5, Claude Code 2.1.281)

| Surface | tools/list bytes | First-call context tokens |
|---|---|---|
| none | - | 7,082 |
| `/mcp` (86 tools) | 32,703 | 19,294 (+12,212) |
| `/mcp/routed` (3 tools) | 1,637 | 8,353 (+1,271) |

The full surface adds about 9.6x more context than the routed one before the model has done
anything. Everything else (tokens per task, cost, time, completion, tool discipline) needs the
real-portal run.

## What the dry runs on the real portal found (2026-09-30)

Both surfaces completed the read tasks; the pipeline snapshot took 13 to 15 tool calls in
either arm (per-stage deal searches), so tool count is a property of the task and the
charter, not of the surface. Three findings changed the bench and the server:

1. **Headless hosts decline inline confirmations.** Claude Code declares form elicitation, so
   a CONFIRM-tier write returns an MRTR confirmation request; in `-p` mode the client declines
   it and the gate records a reject. No CONFIRM write can leave a pending preview in a headless
   run, on either surface. The runner scores that `declined` outcome as "reached the gate";
   the proxy surface behaves identically, which is the parity evidence H5 asked for.
2. **Engagement creates were posting to the wrong endpoint.** `hubspot_create_note` and its
   siblings sent `/crm/v3/objects/engagements` with `hs_engagement_type`; HubSpot rejected the
   association ("Object type ENGAGEMENT is not supported"). They now post to the typed
   endpoints (`/notes`, `/tasks`, `/emails`, `/meetings`, `/calls`) and default `hs_timestamp`,
   which HubSpot requires. Found because the AUTO-tier note write failed in both arms.
3. **The validator rejected numeric strings.** `amount: "12500"` failed `type_mismatch` although
   HubSpot stores and accepts numbers as strings and models send them that way. Numeric strings
   now pass; non-numeric strings still fail.

4. **Engagement objects were invisible to the object tools and to undo.** `notes`, `tasks`,
   `calls`, `emails` and `meetings` failed the object-type validator, so the model could not
   read back a note it had just created, and undo of an engagement create could not delete
   it (the snapshot had no object type). The five types are valid now, the gate records the
   engagement type on create, and undo falls back to the tool name for older snapshots.
5. **Validator errors were invisible.** A `ValueError` from tool-side validation surfaced as
   the SDK's bare "Error executing tool"; it now reaches the model as the validator's message.

Also fixed along the way: the disk redactor hashed tool names over 20 characters as personal
names (`hubspot_create_object` became `<name:...>` in traces and the audit log), so per-tool
aggregates were unreadable. Identifiers with underscores are exempt now.

Live fallback check: with an expired gateway credential the routed surface answered from the
keyword router in 447 ms with a `note` naming the 401, as designed.

Operational note: do not start the server under `vercel env run` from another project. It
injects that project's `REDIS_URL`, which switches `state.get_store()` to Redis and requires
`HUBSPOT_MCP_STATE_KEY`; every tool then fails.

## Results: full run, 2026-10-01

12 tasks x 3 reps x 3 arms on the PromptMetrics portal, Claude Code 2.1.281 headless, model
`claude-opus-5-5[1m]` (the CLI default that day). 106 valid sessions: 36 full, 36 routed, 34
control (six control sessions hung and were rerun; two prompts hung again and were dropped).
Full table: `docs/bench-2026-10-01-report.md`. Raw transcripts: `bench/runs/2026-10-01T06-25-37Z/`
(gitignored).

| Measure (medians) | control | full (80 tools) | routed (3 tools) |
|---|---|---|---|
| tools/list bytes | - | 30,534 | 1,637 (18.7x smaller) |
| First-call context tokens | 3,304 | 17,944 (+14,640) | 4,517 (+1,213) |
| Context tokens per API call | 3,304 | 19,242 | 12,906 |
| API calls per task | 2 | 7 | 12 |
| Context tokens, all calls per task | 6,608 | 132,414 | 152,870 |
| Cache-read share of context | 40% | 94% | 78% |
| Output tokens per task | 12 | 85 | 153 |
| Cost per task | $0.024 | $0.067 | $0.147 |
| Cost, all 36 sessions | $1.00 (34) | $4.29 | $7.07 |
| Duration per task | 8 s | 20 s | 29 s |
| HubSpot tool calls per task (excl. routing) | 0 | 4 | 4 |
| Task completion | n/a | 36/36 | 36/36 |
| Tool errors (server-side) | 0 | 0 | 0 |
| Write-gate outcomes | - | identical pattern across arms | identical pattern across arms |
| Jev routes answered / median latency / cost | - | - | 29/29, 698 ms, $0.0003 |

### Hypotheses

- **H1 context, confirmed.** The routed surface adds 1.2k tokens before the first request against
  14.6k for the full one: 12x less, 18.7x by tools/list bytes.
- **H2 tokens per task, a wash.** Summed over all 36 sessions per arm, routed used 6.64M context
  tokens against 7.15M for full (7% less); the per-task medians point the other way (153k vs
  132k) because a few long tasks dominate the sums. Per API call the routed arm carries 33% less
  context, but it makes 12 calls instead of 7: two extra round trips (`find_capabilities`,
  `load_charter`) and the verification calls the charters prescribe. `find_capabilities` returns
  about 15.7k characters of tool schemas and `load_charter` about 6.5k characters of charter, all
  of which enters the conversation as newly cached context.
- **H3 time, killed.** Routed is 1.4x slower per task (29 s vs 20 s). Jev itself is 0.7 s of that.
- **H4 cost, killed.** Routed costs 1.65x more ($7.07 vs $4.29 over 36 sessions). The reason is
  prompt caching. Of the full surface's context, 89% was cache reads (6.37M tokens) and 11% cache
  writes (0.78M); the routed arm read 5.19M and wrote 1.45M, plus 24% more output tokens. Cache
  reads cost about a tenth of list price and cache writes about 1.25x, so routing saved tokens
  where they were cheapest and spent them where they were dearest. The 80-tool list is written to
  the cache once per session and read cheaply on every later turn.
- **H5 completion parity, confirmed.** 36/36 both arms, zero server-side tool errors, and the
  write-gate outcomes match arm for arm: AUTO creates applied and undone, CONFIRM updates
  declined through the inline confirmation on both surfaces.
- **H6 tool discipline, no difference.** Tool calls excluding routing are equal on most tasks
  (13→13, 3→3, 1→1). The routed arm made more on two write tasks (4→7, 5→7) because the
  charters tell the model to re-fetch and verify after a write.

### What the routed shape does buy, on this evidence

- **Context-window headroom.** A fixed 1.2k instead of 14.6k per session. Irrelevant for one
  task against one server; decisive when several MCP servers share a host, or on long sessions
  where the tool list competes with working context.
- **Behaviour you can ship.** The charters reached the model and changed what it did (post-write
  verification). The full surface ships the same charters as MCP prompts that Claude Code never
  pulls.
- **Server-side intent telemetry.** Every routed session leaves a `route_decision` event with the
  charter and tools chosen; the full surface can only see tool names.
- **Jev is cheap and reliable here.** 29/29 answered, 698 ms median, $0.0003 per route. Two
  arguable charter picks (`analytics` for the pipeline snapshot; `users`/`triage` on a second
  routing call in one task) did not stop completion.

### Design follow-ups the numbers point at

1. Return the primary charter inside `find_capabilities` and drop the `load_charter` round trip.
2. Return input schemas for the top few tools only, or on demand; 15.7k characters per route is
   most of the context the routed arm "saved".
3. Trim the charter boilerplate (the reflection block drives the extra calls) or make it opt-in.
4. Re-measure with caching in mind: the comparison that matters is per-turn context on a
   multi-server host, not per-task cost on a single server.

### Operational notes from the run

- Six no-MCP control sessions hung until the runner's timeout; a direct rerun of the same prompt
  completed in 7 s. The headless run reported five-hour rate-limit utilisation near 70% with
  overage disabled, so the hangs are most likely Claude Code waiting on rate limiting. Not a
  server effect.
- Both arms wasted calls on `hubspot_get_property` with guessed property names (404 from
  HubSpot). That is model behaviour, equal across arms, not a server defect.
- The portal was clean after the run: 23 AUTO writes applied and 23 undone, no pending previews,
  no engagements left on the test contact, the test deal unchanged. The two test records
  (contact and deal) are still there for manual spot checks.

## Skills served from the server (2026-10-01)

HubSpot's "HubSpot Sales" plugin 2.3.0 ships nine skills (Apache 2.0). They now live in
`src/hubspot_mcp/skills/`, byte-identical, with LICENSE and NOTICE, plus the plugin's two
sub-agent recipes as loadable helpers. Both surfaces serve them:

| Surface | Lookup | Load | Tool count |
|---|---|---|---|
| `/mcp` | `hubspot_find_skills(request_text)` | `hubspot_load_skill(name)` | 88 (86 + 2) |
| `/mcp/routed` | `find_capabilities(task)` now returns `primary_skill`, `skills[]` and prefers a skill in `next_step` | `load_skill(name)` | 4 (3 + 1) |

The skills name HubSpot's official connector tools (`search_crm_objects`, `manage_crm_objects`,
`get_organization_details`, `get_crm_objects`, `get_user_details`, `get_properties`,
`search_properties`). `skills/mapping.py` maps each onto this server's tools and renders the
mapping into a header prepended at serve time, together with the write-gate rules and a note
that Gmail, calendar and Slack are not part of this server. The text after the header is
HubSpot's, unchanged; a test diffs it against the vendored file.

Routing: Jev's request gains a `skill` choice over the nine skills plus "none" and a boolean per
skill (134 questions on the routed surface, 10 on `hubspot_find_skills`, which routes skills
only). The keyword fallback scores the skills' own "ALWAYS use this skill when…" phrases
(weight 3) and their distinctive words (weight 1), with a minimum score so a stray word is not a
match.

### Skill routing eval, 40 labelled prompts (`bench/skill_eval.py`)

| Router | Top-1 | Clear | Boundary | Multi | None | Precision / recall | p50 / p95 | Cost per route |
|---|---|---|---|---|---|---|---|---|
| Jev | **90%** | 12/13 | 13/16 | 6/6 | 5/5 | 67% / 95% | 484 / 849 ms | $0.00014 |
| Keyword fallback | 42% | 8/13 | 4/16 | 0/6 | 5/5 | 52% / 54% | ~0 ms | $0 |

Gate: Jev must beat keywords by 15 points on the 16 boundary cases. It did by 56. Jev's four
misses: "Add a new contact: …" went to `import-contacts` where the label says `hubspot`; "How
many open deals and what are they worth" went to `pipeline-pulse` where the label says
`hubspot`; "Which contacts haven't heard from me in two weeks?" and "Show me the Wonka deal"
returned no skill where the labels say `daily-brief` and `hubspot`. The first two are
arguable labels. The last two show the generic `hubspot` skill losing to "none" on plain
lookups, which is harmless on the routed surface (the charter path takes over) and costs
nothing on the full surface (the model uses the tools directly). Run through the live server
with `--via-server http://127.0.0.1:8000`; results in `bench/runs/skill-eval-*.json`.

### Skill bench, four tasks on the real portal (`docs/bench-2026-10-01-skills-report.md`)

Daily brief, call prep, log a call, follow-up draft; 3 reps; full and routed arms; Claude Code
2.1.281 headless on `claude-opus-5-5[1m]`; 24 valid sessions (three were cut off by a Claude
Code session limit and rerun). Raw transcripts in `bench/runs/2026-10-01T10-13-22Z/`.

| Medians per task | Full, 88 tools | Routed, 4 tools |
|---|---|---|
| Right skill picked by the lookup | 12/12 | 12/12 |
| That skill loaded by the model | 12/12 | 12/12 |
| Tasks completed | 12/12 | 12/12 |
| First-call context tokens | 18,480 | 4,770 |
| Context tokens, all calls | 399,956 | 360,974 |
| API calls | 12.5 | 20 |
| HubSpot tool calls, excluding routing | 9.5 | 17 |
| Cost | $0.176 | $0.245 |
| Duration | 35 s | 53 s |

A caveat first: during this run `find_capabilities` had a bug (fixed the same day, in review)
that left the skill's own tools out of its response; only charter tools and the router's direct
picks came back with schemas. The model still completed every task because the skill header maps
the connector names onto this server's tools and `call_hubspot` accepts any tool by name. With
the fix, routed responses carry a few more schemas, so routed context per task will be slightly
higher than measured here.

What held:
- Routing worked on every session in both arms, and the model loaded the routed skill every
  time. On the full surface it called `hubspot_find_skills` first in all 12 sessions once the
  server instructions said to. In the first attempt, before those instructions existed, it
  skipped the lookup and guessed the skill name (`daily brief`, then `daily-brief`). An
  instruction line and a forgiving name match fixed both.
- The served skills changed behaviour the same way on both surfaces: call prep saved a prep
  note, log-call logged the call as a note, created a follow-up task and set the lead status,
  exactly as HubSpot's workflow says. Every write was AUTO-tier and the runner undid it.
- The routed proxy rejected four malformed `hubspot_list_associated_records` calls with the
  exact missing-field message; the model corrected its arguments and continued.

What did not:
- Same pattern as the main bench: the routed arm costs about 1.4x more and takes 1.5x longer per
  skill task, with 60% more turns and nearly twice the HubSpot tool calls. The skill text is the
  same on both surfaces; the difference is the routed header's extra round trips plus the charter
  the model also loads, and the per-call schema validation that turns a bad guess into a retry.
- Undo did not restore a property that had been blank before the write: the log-call skill set
  `hs_lead_status`, undo replayed the original (empty) value, and HubSpot left `CONNECTED` in
  place. Reset by hand on the test contact. Worth a fix in `undo_action`: send an explicit empty
  string for properties whose original value was empty.

### Verdict on skills

Serving HubSpot's skills from the server works on both surfaces: 90% routing accuracy on the
labelled prompts, 100% pick and load on the live tasks, and the workflows ran as written against
the real portal with the write gate intact. The cost picture is the one the main bench found:
on a single-server host with prompt caching, the full surface plus two skill tools is the
cheaper way to get the same skills, and the routed surface pays for its context headroom in
turns. The win over installing skill files is identical on both: nobody installs anything, and
the server can update a skill for everyone at once.

## Running the bench

See `bench/README.md`. Server in token mode on 127.0.0.1:8000 serving both surfaces; runner
does 12 tasks x 3 reps x 3 arms (about 108 sessions, ~40 s each), rejecting pending previews
and undoing AUTO writes between reps; `bench/report.py` writes `report.md`.

## Inputs still needed

- `HUBSPOT_PORTAL`: which of the portals configured under `~/.claude/hubspot/` is the PromptMetrics portal.
- `AI_GATEWAY_API_KEY` with paid credits (Jev is not on the free tier).
- `HUBSPOT_MCP_SERVER_SECRET` for the local bearer.
- A test contact and a test deal in that portal for the write tasks (created before the run,
  deleted after), passed as `--test-contact` / `--test-deal`.

## Open questions after the run

- Ship `/mcp/routed` to the hosted deployment? Needs the routed OAuth resource in AuthKit and a
  Claude connector pointed at the new path.
- Replace `/mcp` or keep both? Depends on completion parity in the report.
- Tune `THRESHOLD` (0.5) and whether tool booleans earn their share of the Jev payload.
