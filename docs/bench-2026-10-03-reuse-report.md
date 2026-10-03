# Multi-turn bench report: 2026-10-03T05-50-52Z-multiturn

- Sessions: 12 multi-turn (3 turns each) and 24 single-turn; arms routed-jev, full; scenarios s01, s02
- Model: CLI default (observed: claude-opus-5-5); 2.1.288 (Claude Code)
- Cost basis: Anthropic list API prices for the model that ran, as Claude Code reports them per session (`total_cost_usd`, cumulative over a resumed session; per-turn cost is the difference). The sessions ran on a Claude subscription, so the dollar figures are API-equivalent, not money spent. Known price tables: opus-5.5, sonnet-5.5, sonnet-5, haiku-4.5.
- tools/list: routed-jev: 4 tools, 2,277 bytes, router jev; full: 82 tools, 31,437 bytes

## A. Per turn, medians over multi-turn sessions

| Arm | Turn | Sessions | Lookup calls | Load calls | HubSpot calls | API calls | Context tokens | Cache read | Cache write | Output tokens | Cost | Seconds | Checks passed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| routed-jev | 1 | 6 | 1 | 1 | 6.5 | 14 | 204,138 | 178,302 | 33,262 | 225 | $0.182 | 26 | 6/6 |
| routed-jev | 2 | 6 | 0 | 0 | 1.5 | 3.5 | 75,080 | 72,516 | 2,556 | 177 | $0.036 | 9 | 5/6 |
| routed-jev | 3 | 6 | 0 | 0 | 2.5 | 5.5 | 127,954 | 123,616 | 4,327 | 34 | $0.043 | 10 | 4/6 |
| full | 1 | 6 | 0.5 | 0.5 | 7 | 14.5 | 308,110 | 283,926 | 18,778 | 214 | $0.118 | 19 | 3/6 |
| full | 2 | 6 | 0 | 0 | 1.5 | 3.5 | 88,063 | 85,644 | 2,412 | 120 | $0.034 | 8 | 5/6 |
| full | 3 | 6 | 0 | 0 | 2.5 | 5 | 132,050 | 128,488 | 3,596 | 41 | $0.041 | 9 | 5/6 |

Lookup = find_capabilities or hubspot_find_skills; load = load_skill or hubspot_load_skill. A lookup or load count of 0 on turns 2 and 3 means the model reused what turn 1 put in its context.

## B. One three-turn session against three single-turn sessions

| Arm | Scenario | Multi: cost | Singles: cost (sum of medians) | Multi: seconds | Singles: seconds | Multi: API calls | Singles: API calls | Multi: context tokens | Singles: context tokens | Multi: cache write | Singles: cache write | Multi: lookups+loads | Singles: lookups+loads |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| routed-jev | s01 | $0.381 | $0.674 | 57 | 85 | 30 | 48.5 | 678,465 | 896,237 | 54,812 | 113,061 | 2 | 6 |
| routed-jev | s02 | $0.140 | $0.403 | 33 | 55 | 21 | 38 | 208,232 | 449,124 | 17,142 | 76,949 | 2 | 5 |
| full | s01 | $0.310 | $0.441 | 55 | 68 | 30 | 41 | 833,136 | 945,653 | 39,719 | 69,960 | 2 | 4 |
| full | s02 | $0.107 | $0.230 | 25 | 46 | 18 | 33 | 359,997 | 649,556 | 16,827 | 42,490 | 0 | 0 |

Singles use the explicit form of turns 2 and 3 (the contact named instead of a pronoun), so each stands alone. Medians per prompt, then summed.

## C. Checks and re-lookups

| Arm | Mode | Sessions | All turns passed | Turn checks passed | Re-lookups (turn 2+ lookup or load calls) | Tool errors |
|---|---|---|---|---|---|---|
| routed-jev | multi | 6 | 4/6 | 15/18 | 0 | 9 |
| routed-jev | single | 12 | 12/12 | 12/12 | 0 | 10 |
| full | multi | 6 | 3/6 | 13/18 | 0 | 0 |
| full | single | 12 | 10/12 | 10/12 | 0 | 0 |

No re-lookups: every multi-turn session called the lookup and the loader on turn 1 only.

## Failed turn checks

- full multi s02 rep 1 turn 1: skill_loaded
- routed-jev multi s02 rep 2 turn 2: tool_any
- routed-jev multi s02 rep 2 turn 3: tool_any
- full multi s02 rep 2 turn 1: skill_loaded
- full multi s02 rep 2 turn 2: tool_any
- full multi s02 rep 2 turn 3: tool_any
- full multi s02 rep 3 turn 1: skill_loaded
- routed-jev multi s02 rep 3 turn 3: tool_any
- full single s02 rep 1 turn 1: skill_loaded
- full single s02 rep 2 turn 1: skill_loaded
