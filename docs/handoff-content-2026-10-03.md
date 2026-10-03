# Handoff for the content team: routed selection in hubspot-mcp, three follow-up questions

Prepared 2026-10-03 by the engineering side for the content team. Everything here is measured or
cited; nothing is estimated. Names of records and people are placeholders. Numbers you can lift
are in section 4. If a number is not in this document, do not use it.

## 1. Brief

hubspot-mcp is PromptMetrics' production MCP server for HubSpot: 88 tools, 44 specialist
charters, HubSpot's nine Sales skills served from the server, and a second "routed" surface
with four tools (`find_capabilities`, `load_skill`, `load_charter`, `call_hubspot`) that picks
the skill, charter and tools per request with TypeSafe's Jev decision model on Vercel's AI
Gateway. Between 2026-09-30 and 2026-10-03 we ran 334 headless Claude Code sessions against the
real PromptMetrics portal to measure that design. The earlier runs (274 sessions) are written up
in `docs/routed-mode.md` and the draft izzy.build article `routed-mcp-ab-hubspot`. This handoff
covers three follow-up questions asked on 2026-10-03:

1. **Reuse within a session.** When one skill is needed several times in one conversation, does
   the model ask the server again each time, or reuse what it already has? What does each later
   turn cost? (New measurement: 60 sessions.)
2. **Context size and accuracy.** Is there evidence that a smaller context, fewer tool
   definitions and one skill instead of nine, makes the model more accurate and less likely to
   invent things? (Literature review plus mining of our 334 transcripts.)
3. **Tool-call budgets.** Some MCP servers and hosts cap tool calls or the API underneath. Which
   ones, and does routed selection help? (Vendor documentation review plus our numbers.)

## 2. Plain-language explanations

### 2.1 How a model "remembers" inside a session

A model has no memory of its own between requests. What it has is the conversation: every turn,
the host (Claude Code) sends the whole conversation so far, including every tool result, back to
the API. So once `find_capabilities` has returned the list of tools and `load_skill` has
returned the skill text, both are part of the conversation and travel with every later request.
The model does not need to ask the server again, and nothing in MCP makes it. The two questions
worth measuring are whether the model nevertheless calls the lookup again out of habit, and what
it costs to carry that text along on every later turn.

### 2.2 Why carrying text along is cheap: prompt caching

Sending the same long prefix on every request would be expensive, so the API caches it. A token
that was already sent and cached costs a tenth of a fresh token to send again ($0.20 vs $4 per
million on Opus 5.5; $0.20 vs $2 on Sonnet). Writing new text into the cache costs more than a
fresh token ($8 per million for the one-hour cache Claude Code uses). So the first turn, where
the skill and the tool list arrive, pays the write; later turns pay the cheap read. This is also
why the routed surface did not save money in the single-task benchmark: it removed tokens that
were already cheap cache reads (the big tool list) and added tokens that were expensive cache
writes (the lookup result and the skill text).

### 2.3 Fewer tokens or fewer options: which one matters

Two different things get called "smaller context". One is fewer tokens: a shorter prompt. The
other is fewer irrelevant options: fewer tools, documents or skills to choose between. The
research reviewed in section 3.3 shows both matter, for different reasons. Long inputs make
models worse at finding and using information that is present ("context rot"), and more
look-alike options make them worse at picking the right one. Every tool-selection study that
reports a gain from loading fewer tools changes both at once, so nobody has shown how much of the
gain is from fewer tokens and how much from fewer choices. Our own data says the choice effect is
what shows up at our scale: the model with 88 tools in view did not invent property names more
often than the model with four, but the keyword router's wrong first picks did cost errors, turns
and money, and the model with no schema for a tool guessed its arguments 80 times in 12 sessions.

### 2.4 Tool-call budgets and where routing helps

A tool-call budget is a cap on how often an agent may call a vendor: Notion caps calls on its MCP
server per 10 seconds, Zapier bills two tasks per call, Pipedrive and HubSpot cap the API
underneath (tokens or requests per day per company). Routed selection helps in three specific
ways and not in a fourth. The routing calls (`find_capabilities`, `load_skill`) go to our server
and to Jev, not to the vendor, so they cost nothing from the vendor's budget. A right first pick
avoids the extra calls and errors that follow a wrong one. And every vendor call passes through
one function on our server, which is the natural place to count calls per user or per portal
and refuse or queue when a quota is close. What routing does not do is reduce the number of
vendor calls a task needs: in the single-task benchmark the routed surface made the same
number of HubSpot calls as the full one.

## 3. The numbers

Cost basis for every dollar figure: Anthropic list API prices for the model that ran, as Claude
Code reports them per session (`total_cost_usd`, basis "list"), pricing page read 2026-10-02.
The sessions ran on a Claude subscription, so these are API-equivalent figures, not money spent.
Opus 5.5: $4 input, $8 one-hour cache write, $0.20 cache read, $20 output per million tokens.
Sonnet 5 and 5.5: $2 / $4 / $0.20 / $10.

### 3.1 Reuse within a session (Part A)

Run `bench/runs/2026-10-03T05-50-52Z-multiturn` (local, gitignored; report copied to
`docs/bench-2026-10-03-reuse-report.md`), 2026-10-03, Claude Code 2.1.288, model
`claude-opus-5-5` (CLI default). Two scenarios, three user turns each, on the test contact: log a
call three times (writes, AUTO tier, undone after each session) and look a contact up then ask
two follow-ups about them (reads). Two arms on the same server: routed (`/mcp/routed`, 4 tools,
Jev) and full (`/mcp`, 88 tools, `hubspot_find_skills` + `hubspot_load_skill`). 3 multi-turn
sessions per scenario and arm (12 sessions, 36 turns) plus each prompt as its own one-turn
session, 2 reps (24 sessions). Each user turn ran as its own Claude Code process resumed into
the same session, so per-turn token usage is exact.

**Table A1. Per turn, medians over the 6 multi-turn sessions per arm (both scenarios).**

| Arm | Turn | Lookup calls | Load calls | HubSpot calls | API calls | Context tokens | Cache read | Cache write | Output tokens | Cost | Seconds |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Routed, 4 tools | 1 | 1 | 1 | 6.5 | 14 | 204,138 | 178,302 | 33,262 | 225 | $0.182 | 26 |
| Routed, 4 tools | 2 | 0 | 0 | 1.5 | 3.5 | 75,080 | 72,516 | 2,556 | 177 | $0.036 | 9 |
| Routed, 4 tools | 3 | 0 | 0 | 2.5 | 5.5 | 127,954 | 123,616 | 4,327 | 34 | $0.043 | 10 |
| Full, 88 tools | 1 | 0.5 | 0.5 | 7 | 14.5 | 308,110 | 283,926 | 18,778 | 214 | $0.118 | 19 |
| Full, 88 tools | 2 | 0 | 0 | 1.5 | 3.5 | 88,063 | 85,644 | 2,412 | 120 | $0.034 | 8 |
| Full, 88 tools | 3 | 0 | 0 | 2.5 | 5 | 132,050 | 128,488 | 3,596 | 41 | $0.041 | 9 |

What this shows: on both surfaces the model called the lookup and the loader on turn 1 only. Not
one of the 24 later turns (12 per arm) called `find_capabilities`, `hubspot_find_skills`,
`load_skill` or `hubspot_load_skill` again. A later turn cost a fifth of the first ($0.036 to
$0.043 against $0.118 to $0.182) and wrote a tenth of the cache (2,412 to 4,327 tokens against
18,778 to 33,262), because the skill text and the tool list were already cached and came back
as reads. The full surface's 0.5 on turn 1 is the contact-lookup scenario: the full surface's
instructions tell the model to use tools directly for a plain lookup, so it skipped the skill in
all three of those sessions and used it in all three log-call sessions.

**Table A2. One three-turn session against the same three requests as separate sessions
(medians; singles summed per prompt).**

| Arm | Scenario | 3-turn session: cost | 3 singles: cost | Session: seconds | Singles: seconds | Session: API calls | Singles: API calls | Session: cache write | Singles: cache write | Session: lookups + loads | Singles: lookups + loads |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Routed | log a call x3 | $0.381 | $0.674 | 57 | 85 | 30 | 48.5 | 54,812 | 113,061 | 2 | 6 |
| Routed | contact lookup + 2 follow-ups | $0.140 | $0.403 | 33 | 55 | 21 | 38 | 17,142 | 76,949 | 2 | 5 |
| Full | log a call x3 | $0.310 | $0.441 | 55 | 68 | 30 | 41 | 39,719 | 69,960 | 2 | 4 |
| Full | contact lookup + 2 follow-ups | $0.107 | $0.230 | 25 | 46 | 18 | 33 | 16,827 | 42,490 | 0 | 0 |

What this shows: keeping the work in one session cost 30% to 65% less than three separate
sessions for the same requests, and finished in a third less time, on both surfaces. The
saving is larger on the routed surface (43% and 65%) than on the full one (30% and 53%),
because the routed surface pays more up front (lookup result plus skill text as cache writes)
and that payment is made once per session instead of once per request. Lookups and loads fell
from 5 or 6 to 2.

**Table A3. Checks, re-lookups and errors.**

| Arm | Mode | Sessions | Turn checks passed | Re-lookups on turns 2–3 | Tool errors |
|---|---|---|---|---|---|
| Routed | 3-turn | 6 | 15/18 | 0 | 9 |
| Routed | single | 12 | 12/12 | 0 | 10 |
| Full | 3-turn | 6 | 13/18 | 0 | 0 |
| Full | single | 12 | 10/12 | 0 | 0 |

The eight failed turn checks are not task failures. Five are follow-up turns where the model
answered "which deals are they on" and "any open tasks or notes" with zero HubSpot calls, from
the associations it had already fetched on turn 1; the answers were correct (the test contact
has no deals, tasks or notes), and the check had demanded a tool call. Three are the full
surface skipping the contact-lookup skill on a plain lookup, as its instructions say. The 19
routed tool errors are all proxy argument rejections on one association tool during the
contact-lookup skill: the model left out the two required fields, the proxy named them, the
model corrected and continued. The full surface's registered tool accepts the same call
silently, which is why its count is zero.

### 3.2 Our earlier runs, for reference (already in `docs/routed-mode.md`)

**Table R1. Surface A/B, 2026-10-01, run `2026-10-01T06-25-37Z`, Opus 5.5, 106 sessions
(36 full, 36 routed, 34 control), 12 tasks x 3 reps.**

| Per task, medians | No MCP | Full, 80 tools | Routed, 3 tools |
|---|---|---|---|
| tools/list bytes | - | 30,534 | 1,637 |
| Context in the first request (tokens) | 3,304 | 17,944 | 4,517 |
| Context per API call | 3,304 | 19,242 | 12,906 |
| API calls | 2 | 7 | 12 |
| HubSpot tool calls (excl. routing) | 0 | 4 | 4 |
| Output tokens | 12 | 85 | 153 |
| Cost | $0.024 | $0.067 | $0.147 |
| Duration | 8 s | 20 s | 29 s |
| Tasks completed | n/a | 36/36 | 36/36 |
| Jev routes answered / median latency / cost | - | - | 29/29, 698 ms, $0.0003 |

Sums over 36 sessions per arm: full 6.37M cache-read tokens, 0.78M cache-write, 5,851 output,
$4.29; routed 5.19M read, 1.45M write, 7,269 output, $7.07. 89% of the full arm's context tokens
were cache reads. What this shows: 12x less context before the first request and a third less
per call, same completion, 1.65x the cost per task because of prompt caching.

**Table R2. Router comparison, 2026-10-02, run `2026-10-02T05-58-55Z`, Opus 5.5, 120 sessions,
20 tasks x 3 reps, same routed surface with Jev (port 8000) vs the keyword fallback (port 8001).**

| Per session, medians | Jev | Keyword |
|---|---|---|
| Skill right, first lookup (24 skill-labelled sessions) | 24/24 | 14/24 |
| Charter right, first lookup (42 charter-labelled sessions) | 35/42 | 32/42 |
| Tasks completed (routing picks excluded) | 60/60 | 57/60 |
| Context tokens, all calls | 234,636 | 190,898 |
| Cost | $0.188 | $0.177 |
| Cost, all 60 sessions | $14.97 | $13.84 |
| Duration | 29 s | 29 s |
| Turns | 11 | 10.5 |
| HubSpot tool calls | 8 | 8 |
| Argument rejections by the proxy | 0 | 4 |
| Re-lookups (sessions with a second `find_capabilities`) | 0 | 9 |
| Routing latency, median / p95 | 651 ms / 1,079 ms | 0 |
| Routing cost per route / per run | $0.0005 / $0.027 | $0 |

Six of Jev's seven charter "misses" picked a skill instead of a charter, which is the designed
behaviour. Offline: skills 90% vs 42% on 40 labelled prompts; charters 46/49 vs 47/49 on the
server's 49-prompt corpus.

**Table R3. Sonnet 5 follow-up, 2026-10-02, run `2026-10-02T15-43-59Z`, observed model
`claude-sonnet-5`, 24 sessions, four skill tasks x 3 reps, Jev vs keyword.**

| Per session, medians | Jev | Keyword |
|---|---|---|
| Skill right | 12/12 | 9/12 |
| Tasks completed | 12/12 | 11/12 |
| Turns | 10.5 | 13 |
| HubSpot tool calls | 7.5 | 9.5 |
| Cost | $0.169 | $0.197 |
| Duration | 46 s | 55 s |
| Argument rejections | 0 | 4 |
| First-call context tokens | 11,166 | 11,166 |

### 3.3 Context size and accuracy (Part B)

**Table B1. Published evidence, accessed 2026-10-03.** Full citations in section 6.

| Source | What was measured | Result | What it supports |
|---|---|---|---|
| Anthropic, Advanced tool use (Nov 2025) | Internal MCP evaluation with and without the Tool Search tool | Opus 4: 49% to 74%; Opus 4.5: 79.5% to 88.1%; tool definitions ~77K to ~8.7K tokens | Loading fewer tools raised task accuracy on a real multi-server eval, two model generations; does not separate fewer tokens from fewer options |
| Anthropic, Tool search tool docs; Agent SDK tool search docs | Product guidance | "Claude's ability to pick the right tool degrades once you exceed 30–50 available tools"; recommended at 10+ tools or >10k tokens of definitions; Claude Code defers MCP tool definitions when they reach 10% of the window | Anthropic's stated threshold; no public curve |
| Chroma, Context Rot (Jul 2025) | 18 models, needle-in-haystack, LongMemEval, repeated words, input length varied | Performance falls with input length even on simple tasks; focused ~300-token prompts beat full ~113k prompts on all 18 models; one distractor hurts, four hurt more; Qwen3-8B collapses from ~5k words, larger models later | Length alone degrades retrieval and raises abstention; smaller models earlier |
| Liu et al., Lost in the Middle (TACL 2023) | Multi-document QA, 10/20/30 documents, answer position varied | GPT-3.5-Turbo with 20 documents: 75.8% answer first, 53.8% answer in the middle, 56.1% with no documents | Irrelevant context can push accuracy below no context; pre-2024 models |
| Modarressi et al., NoLiMa (ICML 2025) | 13 long-context models, non-lexical needle retrieval | At 32K tokens, 11 of 13 models below 50% of their short-context score; GPT-4o 99.3% to 69.7% | Length degrades retrieval when wording does not match |
| Paramanayakam et al., Less is More (DATE 2025) | 1.5B–8B models, tool count varied, similarity-filtered tools | Llama3.1-8B: 46 tools fails, 19 succeeds; BFCL success 44.2% vs ~20% baseline; tool accuracy 93.8% | Fewer, retrieved tools raise selection accuracy for small models |
| Gan & Sun, RAG-MCP (May 2025) | Qwen-max, tool pool 1 to 11,100 MCPs | 43.1% (retrieved tools) vs 18.2% (keyword match) vs 13.6% (all tools in prompt); prompt tokens 1,084 vs 2,134 | Narrowing the toolset beats loading everything by 3x; retrieval precision falls past ~100 tools |
| Repantis et al., Meta (May 2026) | Claude Sonnet 4.6, adaptive vs fixed number of tools shown | 93.1% adaptive depth vs 87.1% always five tools | The right number of tools is query-dependent, not a fixed small number |
| Wang et al., MCP-Bench (Aug 2025) | 20 models, 28 servers / 250 tools, distractor servers added | llama-3.1-8b 0.438 to 0.415, nova-micro 0.520 to 0.471 with more servers; gpt-5 flat ~0.75 | Weaker models suffer from more tools; frontier models mostly do not |

**Table B2. Error proxies in our own transcripts, by arm.** Same model within each run, same
tasks, so any difference is the surface or the router. Counts are over all sessions in the arm;
sessions with at least one such error in brackets.

| Run | Arm | Sessions | Invented property names (HubSpot "No property named …") | Proxy argument rejections (schema check) | HubSpot 4xx other | Re-lookups | Task checks failed |
|---|---|---|---|---|---|---|---|
| Surface A/B (Opus) | full, 80 tools | 36 | 21 (3 sessions) | 2 (own tool validation) | 0 | n/a | 0 |
| Surface A/B (Opus) | routed, 3 tools | 36 | 24 (3 sessions) | 0 | 1 | 3 | 0 |
| Skill bench (Opus, routed had the missing-schema bug) | full, 88 tools | 12 | 0 | 0 | 3 | n/a | 0 |
| Skill bench | routed, 4 tools | 12 | 0 | 80 (51 on one association tool) | 1 | 2 | 0 |
| Router comparison (Opus) | routed, Jev | 60 | 22 | 0 | 6 | 0 | 0 |
| Router comparison (Opus) | routed, keyword | 60 | 19 | 4 | 4 | 9 | 3 |
| Sonnet 5 follow-up | routed, Jev | 12 | 0 | 0 (plus 2 invalid object type) | 15 | 0 | 0 |
| Sonnet 5 follow-up | routed, keyword | 12 | 0 | 4 | 14 | 0 | 1 |
| Multi-turn reuse (Opus, 2026-10-03) | routed, Jev, 4 tools | 18 | 0 | 19 (all on one association tool, contact-lookup skill) | 0 | 0 | 0 real |
| Multi-turn reuse (Opus, 2026-10-03) | full, 88 tools | 18 | 0 | 0 | 0 | 0 | 0 real |

Notes on B2. Every invented property name came from one task, "which deal properties track why
a deal was lost": in all six sessions the model first listed the properties, then probed four
guessed names (`loss_reason`, `close_reason`, `lost_reason`, `deal_lost_reason` or similar)
before answering. Same behaviour, same count, on both surfaces. The 80 argument rejections in
the skill bench happened in the run where a bug left the chosen skill's tools out of the lookup
result, so the model had no schema for the association tool and guessed its arguments repeatedly;
the proxy caught every one, and the full surface, which had the schema in context, produced none.
After the fix, the Jev arm produced 0 rejections in the 72 router-comparison and Sonnet sessions,
but 19 in the 18 multi-turn-run sessions, all on the same association tool while following the
contact-lookup skill (the skill's text names HubSpot's connector tool, the header maps it onto
ours, and the model left out the two required identifiers on its first try). The pattern is the
same in both cases: when the model acts on a tool it has no schema for in context, or whose
schema it has not read closely, it guesses arguments, and the routed proxy is the only place that
catches the guess. In the router comparison the
keyword arm's 4 rejections and 9 re-lookups all followed a wrong first pick. "HubSpot 4xx other"
is mostly the API rejecting a malformed association payload; it rose on Sonnet 5 on both arms.

**Table B3. What a wrong first pick cost (router comparison, Opus, 114 labelled sessions).**

| | Jev, pick right (50) | Jev, pick wrong (7) | Keyword, pick right (37) | Keyword, pick wrong (20) |
|---|---|---|---|---|
| HubSpot calls, median | 8 | 9 | 8 | 8 |
| Tool errors per session | 0.56 | 0.00 | 0.16 | 1.15 |
| Cost, median | $0.191 | $0.237 | $0.171 | $0.249 |
| Turns, median | 11 | 12 | 12 | 10.5 |

What this shows: a wrong pick did not add vendor calls on Opus, it added errors (7x) and cost
(46% more per session). On Sonnet 5 (Table R3) it also added calls: 9.5 against 7.5.

### 3.4 Tool-call budgets (Part C)

**Table C1. Limits published by MCP servers and hosts, accessed 2026-10-03.** Official sources
unless marked; full links in section 6.

| Server or host | What is limited | Number and window | Scope | Verified |
|---|---|---|---|---|
| Pipedrive MCP (official) | API tokens, shared with the API budget; no separate cap on MCP calls | Daily budget 30,000 x plan multiplier (Lite 1, Growth 2, Premium 5, Ultimate 7) x seats, cap 100M; burst 20/40/100/120 requests per 2 s by plan, 4x on OAuth; search 10 per 2 s. A call costs its endpoint's tokens (single get 2, list 20, update 10, search 40). Top-ups +250,000 per unit, max 10 | Company per day; app for burst | Yes |
| HubSpot MCP (mcp.hubspot.com) | Nothing at the MCP level; API underneath | 100/190/190 requests per 10 s (Free-Starter / Pro / Enterprise private apps), OAuth apps 110 per 10 s; 250k / 625k / 1M per day; add-on 250 per 10 s and +1M per day | App for burst, account per day | Yes (API); MCP limit not found |
| Salesforce MCP / Agentforce | Tools active, timeout, API requests | About 20 tools active at once; 60 s client timeout; calls count against the org's 24-hour API allocation (Developer 15,000; Enterprise 100,000 + 1,000 per licence) | Org | Partial (pages blocked fetch; numbers from official-page snippets) |
| Notion MCP | Tool calls on the MCP server itself | 20 search + 20 data-source-query calls per 10 s; 600 requests per minute (Business/Enterprise), 180 (others); 429 with Retry-After | Connection, plan | Yes (changelog Sep 2026) |
| Atlassian Rovo MCP | Rovo credits per call | Up to 10 credits per call for search-type tools, from a monthly allowance per user by plan; hourly caps of 500/1,000/10,000 appear only in community threads | Org (credits) | Credits yes; hourly no |
| Linear MCP | Nothing at the MCP level; GraphQL API underneath | 2,500 requests per hour (API key), 5,000 (OAuth); complexity 3M/2M points per hour | User | Yes (API) |
| Slack MCP | Per tool, same as Web API methods | Tier 2 20+/min, Tier 3 50+/min, Tier 4 100+/min; non-Marketplace apps 1 request per minute and 15 objects on history/replies | App + workspace, per method | Yes |
| GitHub MCP | Nothing at the MCP level; REST underneath | 5,000 requests per hour per user (15,000 Enterprise Cloud app); 900 points per minute; 80 content-creating per minute, 500 per hour; 100 concurrent | User or app | Yes (API) |
| Stripe MCP | Nothing at the MCP level; API underneath | 100 requests per second live, 25 sandbox; 25 per second per endpoint | Account | Yes (API) |
| Zapier MCP | Tasks | 2 tasks per successful tool call; Free 100 tasks per month (50 calls), Pro from 750, Team from 2,000; failed calls free | Account per month | Yes |
| Composio | Tool calls and requests per minute | Hobby 100,000 calls + 50,000 triggers per month, 2,000 requests per minute, then paused; Pro $29 credit then $0.0003 per call | Account | Yes |
| Claude.ai / Desktop connectors | Result size, call duration, connector count | About 150,000 characters per result; 240 s per call; Free plan one custom connector; no per-conversation call cap found | Per call | Yes |
| Claude Code | MCP output tokens; tool-definition context | Warning at 10,000 output tokens, default cap 25,000 (`MAX_MCP_OUTPUT_TOKENS`); tool search activates when deferred tool definitions reach 10% of context | Session | Yes |
| ChatGPT connectors (Developer Mode) | Plan gating only | No call, tool or output numbers published | Workspace | Numbers not found |
| Cursor | Number of tools | First 40 tools sent to the agent (staff-tagged forum thread; current docs silent) | Workspace | Partial |
| VS Code Copilot | Number of tools | 128 per chat request; virtual-tools threshold to exceed | Request | Yes |

Pattern: four providers cap MCP calls directly (Notion, Atlassian, Zapier, Composio); seven cap
the API underneath and the MCP is transparent (Pipedrive, HubSpot, Salesforce, Slack, GitHub,
Stripe, Linear); two hosts cap the number of tools (Cursor 40, VS Code 128); Claude Code and
Claude.ai cap output size, not calls. Nobody publishes a per-conversation tool-call ceiling. In
practice the binding constraint is the vendor's API budget, which an agent spends eight to
fifteen calls at a time. On Pipedrive specifically there is no cap on MCP tool calls as such; a
call draws its endpoint's token cost from the company's daily API budget, which is what makes a
daily brief that fans out across many list and search calls expensive.

**Where routed selection helps, with our numbers:**

- Routing calls cost the vendor nothing. `find_capabilities` and `load_skill` are answered by
  our server and Jev. In the router comparison that was 57 lookups and 60 skill loads with zero
  HubSpot API calls behind them; Jev's own bill was $0.027 for the run.
- A right first pick avoids the detour. On Opus a wrong keyword pick added 7x the tool errors and
  46% to the session cost (Table B3); on Sonnet 5 it added two HubSpot calls per session
  (Table R3). Each error or extra call is a vendor request that a budget would count.
- One function sees every vendor call. `call_hubspot` dispatches every routed tool call, so a
  per-portal or per-user counter, a soft limit with a warning in the response, or a queue when
  the HubSpot burst limit is near, is one change in one place. Not built; the state store the
  server already uses for previews and audit is where the counter would live.
- Honest limit: routing does not reduce the calls a task needs. The routed surface made the same
  median 4 HubSpot calls per task as the full surface (Table R1), and on two write tasks the
  charter's verify-after-write rule raised it from 4 to 7 and 5 to 7. The saving is in wrong
  calls, not in calls.
- Hosted vendor MCPs (Pipedrive's, HubSpot's) count on their side and cannot be proxied; a
  self-hosted server like ours talks to the REST API, so the API's own limits apply and the
  proxy can meter them.

## 4. Quotable findings

Each line is tied to a table. Use as written or shorten; do not round further.

1. In 24 follow-up turns across 12 three-turn sessions, the model never called the skill lookup
   or the skill loader a second time, on either surface. It reused what turn 1 had put in its
   context. (A1, A3)
2. A follow-up turn cost about a fifth of the first turn, $0.036 to $0.043 against $0.118 to
   $0.182, and wrote a tenth of the cache, because the skill and the tool list came back as
   cache reads. (A1)
3. Three requests in one session cost 30% to 65% less than the same three requests as separate
   sessions, and took a third less time; the saving was larger on the routed surface, which
   pays its routing and skill text once per session instead of once per request. (A2)
4. Five follow-up turns were answered correctly with no HubSpot call at all, from the data the
   skill had fetched on turn 1. (A3)
5. Jev picked the labelled skill 24 times out of 24 in live Opus sessions and 12 of 12 on
   Sonnet 5; the keyword fallback managed 14 and 9. (R2, R3)
6. A routing decision costs 651 ms and $0.0005, about 0.2% of the session it serves. (R2)
7. The routed surface loads 1,213 tokens of tool definitions where the full surface loads 14,640,
   and still costs 1.65 times more per task, because prompt caching had already made the big
   list cheap. (R1)
8. The model with 88 tools in view invented property names exactly as often as the model with
   four: 21 against 24 times, all on one task, after listing the real properties first. (B2)
9. When the model had no schema for a tool, it guessed the arguments 80 times in 12 sessions and
   the proxy rejected every one; the full surface, with the schema in context, produced none.
   The same association tool drew 19 more rejections in the multi-turn run while the model
   followed the contact-lookup skill. (B2)
10. A wrong first pick on Opus did not add vendor calls; it added seven times the tool errors and
    46% to the session's cost. On Sonnet 5 it also added two vendor calls per session. (B3, R3)
11. Anthropic's own MCP evaluation went from 49% to 74% on Opus 4 and from 79.5% to 88.1% on
    Opus 4.5 when tools were loaded on demand instead of all at once. (B1)
12. Chroma tested 18 models and found that a focused 300-token prompt beat the same question in a
    113,000-token context on every one of them. (B1)
13. Every published tool-selection gain changes two things at once, fewer tokens and fewer
    options; no study has separated them. (B1)
14. Pipedrive does not cap MCP tool calls; each call spends its endpoint's token cost from a
    company-wide daily API budget of 30,000 tokens times the plan multiplier times seats. (C1)
15. Of fifteen MCP providers and hosts checked, four cap MCP calls directly, seven cap the API
    underneath, two cap the number of tools, and none publishes a per-conversation call limit.
    (C1)
16. Routing calls cost the vendor nothing: 57 lookups and 60 skill loads in the router comparison
    produced zero HubSpot API requests. (R2, C)

## 5. Caveats and what not to claim

- One model per comparison. The surface A/B and the router comparison ran on Opus 5.5 only; the
  Sonnet 5 run covered four tasks and the router question only. Do not claim the routed surface
  helps smaller models until the surface A/B is repeated on one.
- Small live samples. 24 skill-labelled sessions per arm on Opus, 12 on Sonnet, 12 multi-turn
  sessions per arm. Report counts (24 of 24), not percentages to two digits.
- One server on the host. The "context headroom" argument depends on several MCP servers sharing
  a session; no run tested that.
- Headless only. CONFIRM-tier writes were declined by the headless client and recorded as
  rejects; interactive sessions would show a form and wait. Same code path on both surfaces.
- The invented-property result is a null result on an easy task for a strong model. It does not
  show that context size never matters for hallucination; it shows no effect at 88 vs 4 tools
  for Opus 5.5 on these twelve tasks.
- Do not say Pipedrive "limits tool calls". It limits API tokens per day; MCP calls spend them.
- Do not quote the Cursor 40-tool limit or the Atlassian hourly caps as documented; they come
  from a staff-tagged forum thread and community posts respectively.
- The quota counter in `call_hubspot` is described, not built.
- Costs are API-equivalent at list price; nothing was paid per token.
- Reports and this document use placeholder names; the test contact and deal exist in the
  portal under other names and must not be named in published work.

## 6. Sources

Research sources, accessed 2026-10-03:

- Anthropic, Tool search tool: https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool
- Anthropic, Advanced tool use (24 Nov 2025): https://www.anthropic.com/engineering/advanced-tool-use
- Anthropic, Effective context engineering for AI agents (29 Sep 2025): https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic, Context windows: https://platform.claude.com/docs/en/build-with-claude/context-windows
- Anthropic, Manage tool context: https://platform.claude.com/docs/en/agents-and-tools/tool-use/manage-tool-context
- Claude Code, Tool search (Agent SDK): https://code.claude.com/docs/en/agent-sdk/tool-search
- Claude Code, MCP: https://code.claude.com/docs/en/mcp
- Hong, Troynikov, Huber (Chroma), Context Rot (Jul 2025): https://www.trychroma.com/research/context-rot
- Liu et al., Lost in the Middle (TACL 2023): https://arxiv.org/abs/2307.03172
- Modarressi et al., NoLiMa (ICML 2025): https://arxiv.org/abs/2502.05167
- Hsieh et al., RULER (COLM 2024): https://arxiv.org/abs/2404.06654
- Paramanayakam et al., Less is More (DATE 2025): https://arxiv.org/html/2411.15399v1
- Gan & Sun, RAG-MCP (May 2025): https://arxiv.org/html/2505.03275
- Repantis et al., How Many Tools Should an LLM Agent See? (Meta, May 2026): https://arxiv.org/html/2605.24660v1; companion The 99% Success Paradox: https://arxiv.org/abs/2605.18857
- Wang et al., MCP-Bench (Aug 2025): https://arxiv.org/html/2508.20453
- Fan et al., MCPToolBench++ (Aug 2025): https://arxiv.org/abs/2508.07575
- Shi et al., ToolRet (ACL 2025): https://arxiv.org/abs/2503.01763
- Berkeley Function Calling Leaderboard: https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html
- Secondary (not primary evidence): dbreunig, How to Fix Your Context (26 Jun 2025): https://www.dbreunig.com/2025/06/26/how-to-fix-your-context.html; Speakeasy, Less is More (undated): https://www.speakeasy.com/mcp/tool-design/less-is-more

Vendor limits, accessed 2026-10-03:

- Pipedrive MCP: https://www.pipedrive.com/en/features/mcp-server; https://support.pipedrive.com/en/article/chatgpt-pipedrive-app; API limits: https://pipedrive.readme.io/docs/core-api-concepts-rate-limiting; https://support.pipedrive.com/en/article/usage-limits-in-pipedrive; https://support.pipedrive.com/en/article/top-ups
- HubSpot MCP: https://developers.hubspot.com/docs/build-with-ai/remote-mcp-server; API limits: https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
- Salesforce: help.salesforce.com, Agentforce MCP considerations (fetch blocked); https://developer.salesforce.com/docs/platform/salesforce-app-limits-cheatsheet/guide/salesforce-app-limits-platform-api.html
- Notion MCP changelog: https://developers.notion.com/page/changelog
- Atlassian Rovo MCP: https://developer.atlassian.com/cloud/rovo-mcp/; https://support.atlassian.com/rovo/docs/rovo-usage-limits/
- Linear: https://linear.app/developers/rate-limiting; https://linear.app/docs/mcp
- Slack MCP: https://docs.slack.dev/ai/slack-mcp-server/; https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps/
- GitHub: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api; https://github.com/github/github-mcp-server
- Stripe: https://docs.stripe.com/rate-limits; https://docs.stripe.com/mcp
- Zapier MCP: https://docs.zapier.com/mcp/usage/overview; https://zapier.com/pricing
- Composio: https://composio.dev/pricing
- Claude connectors: https://claude.com/docs/connectors/building; https://support.claude.com/en/articles/11175166
- ChatGPT Developer Mode: https://developers.openai.com/api/docs/guides/developer-mode
- Cursor (forum, staff-tagged): https://forum.cursor.com/t/tools-limited-to-40-total/67976
- VS Code Copilot tools: https://code.visualstudio.com/docs/agents/run/tools
- Anthropic pricing (read 2026-10-02): https://www.anthropic.com/pricing

## 7. Assets

Existing, in the izzy.build repo (`src/content/posts/routed-mcp-ab-hubspot/`), generated by
`docs/diagram-generators/gen_routed_ab.py` (edit the numbers there and rerun; it writes the
desktop and mobile SVGs):

- `context.svg`: context in the first request, three bars (no MCP, full, routed).
- `tokens.svg`: cache reads against cache writes, summed over 36 sessions per arm, with cost.
- `route.svg`: one routed request as a swimlane (you, Claude, server, Jev, write gate).
- `skills.svg`: offline skill routing, Jev vs keyword, overall and boundary cases.
- `routers.svg`: live first-pick accuracy, Jev vs keyword, skills and charters, Opus and Sonnet.

Worth adding, described for the designer:

- Per-turn bars for the multi-turn run: for each surface, three bars (turn 1, 2, 3) split into
  cache write and cache read tokens, with the lookup and load call counts printed above each bar
  (1 and 1 on turn 1, 0 and 0 after). Data: Table A1.
- A two-column "what a wrong pick costs" panel: tool errors per session and cost per session,
  right pick against wrong pick, keyword router. Data: Table B3.
- A grid of the fifteen providers in Table C1 coloured by what they cap: MCP calls, API
  underneath, tool count, output size.

## 8. Glossary

- **MCP (Model Context Protocol):** the standard by which a host such as Claude Code connects to
  a server that exposes tools; the server sends a list of tool definitions on connect.
- **Tool definition:** the name, description and input schema of one tool; the host puts all of
  them into the model's context on every request.
- **Skill:** a written workflow (here HubSpot's nine Sales skills) that tells the model how to do
  a job step by step; served from our server as text behind a tool.
- **Charter:** one of 44 specialist operating guides the server ships for areas such as
  pipelines or engagements; loaded as text on request.
- **Jev:** TypeSafe's evaluation-only model on Vercel's AI Gateway; answers a batch of typed
  questions (a choice, yes/no per option) with a probability each, in one call.
- **Routed surface:** the `/mcp/routed` endpoint with four tools that selects skills, charters
  and tools per request, then runs them through `call_hubspot`.
- **Full surface:** the `/mcp` endpoint with all 88 tools registered.
- **Cache read / cache write:** prompt-caching terms; a read is a token the API already has
  cached (a tenth of list price), a write is a token newly stored (more than list price).
- **AUTO / CONFIRM tier:** the write gate's levels; AUTO writes apply at once with an undo,
  CONFIRM writes stop at a preview until a person approves.
- **Lookup / load:** `find_capabilities` or `hubspot_find_skills`; `load_skill` or
  `hubspot_load_skill`.

## 9. Pointers

- `docs/routed-mode.md` in hubspot-mcp: design, every earlier result, cost basis.
- Reports: `docs/bench-2026-10-01-report.md`, `docs/bench-2026-10-01-skills-report.md`,
  `docs/bench-2026-10-02-routers-report.md`, `docs/bench-2026-10-02-routers-report-sonnet.md`,
  `docs/bench-2026-10-02-sonnet-routers-report.md`, `docs/bench-2026-10-03-reuse-report.md`
  (this run).
- PR #42 (router comparison, reporting flags, boundary tasks): https://github.com/promptmetrics/hubspot-mcp/pull/42
- Article draft: izzy.build `src/content/posts/routed-mcp-ab-hubspot/index.mdx` (commit 756e702,
  `draft: true`).
- Bench code: `bench/run.py`, `bench/multiturn.py`, `bench/report.py`, `bench/multiturn_report.py`,
  `bench/skill_eval.py`, `bench/charter_eval.py`, `bench/README.md`.
