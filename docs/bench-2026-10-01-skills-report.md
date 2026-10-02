# Bench report: 2026-10-01T10-13-22Z

- Sessions: 24 (4 tasks x 3 reps x 2 arms)
- Cost basis: Anthropic list API prices for the model that ran (claude-opus-5-5[1m]), as Claude Code reports them (`total_cost_usd`, costBasis list). The sessions ran on a Claude subscription, so the dollar figures are API-equivalent, not money spent.
- Model: Claude Code default (observed: claude-opus-5-5[1m]); Claude Code 2.1.281 (Claude Code)
- Server: http://127.0.0.1:8000

## Context the MCP adds (tools/list)

| Surface | Tools | Bytes | ~tokens (chars/4) | First-call context tokens, median (exact, from sessions) |
|---|---|---|---|---|
| full | 82 | 31,437 | 7,859 | 18,480 |
| routed | 4 | 2,277 | 569 | 4,770 |

Full/routed tools/list ratio: 13.8x by bytes.

## Per arm, medians over all sessions

| Arm | Context tokens, all calls | Cache read | Cache create | Output tokens | Cost | Duration ms | Turns | HubSpot tool calls | Routing calls | Tool errors | Completion rate |
|---|---|---|---|---|---|---|---|---|---|---|---|
| full | 399,956 | 358,756 | 34,447 | 284 | $0.1760 | 34,716 | 12 | 12 | 2 | 0 | 12/12 |
| routed | 360,974 | 322,534 | 31,455 | 524 | $0.2455 | 53,086 | 20 | 19 | 2 | 7 | 12/12 |

## Per task

| Task | Arm | Passed | Ctx first | Ctx all | Output | Cost | Duration ms | Turns | Tool calls | Server-side tools (rep 1) |
|---|---|---|---|---|---|---|---|---|---|---|
| t13 | full | 3/3 | 18,450 | 694,451 | 284 | $0.4333 | 45,638 | 14 | 13 | hubspot_find_skills, hubspot_load_skill, hubspot_status, hubspot_list_users, hubspot_list_pipelines, hubspot_search_obje |
| t13 | routed | 3/3 | 4,746 | 582,029 | 348 | $0.4750 | 66,189 | 17 | 16 | find_capabilities, load_skill, hubspot_status, hubspot_list_users, hubspot_list_pipelines, hubspot_search_objects, hubsp |
| t14 | full | 3/3 | 18,477 | 433,205 | 341 | $0.1743 | 34,998 | 14 | 13 | hubspot_find_skills, hubspot_load_skill, hubspot_search_objects, hubspot_list_users, hubspot_list_associated_records, hu |
| t14 | routed | 3/3 | 4,763 | 350,725 | 960 | $0.2144 | 58,621 | 24 | 23 | find_capabilities, load_skill, hubspot_search_objects, hubspot_list_users, hubspot_search_objects, hubspot_search_object |
| t15 | full | 3/3 | 18,498 | 394,416 | 194 | $0.1773 | 26,101 | 11 | 10 | hubspot_find_skills, hubspot_search_objects, hubspot_load_skill, hubspot_list_associated_records, hubspot_create_note, h |
| t15 | routed | 3/3 | 4,786 | 364,906 | 351 | $0.2524 | 39,494 | 15 | 14 | find_capabilities, load_skill, hubspot_search_objects, hubspot_list_pipelines, hubspot_search_objects, hubspot_create_no |
| t16 | full | 3/3 | 18,484 | 339,585 | 279 | $0.1725 | 34,434 | 12 | 11 | hubspot_find_skills, hubspot_load_skill, hubspot_search_objects, hubspot_status, hubspot_list_users, hubspot_list_associ |
| t16 | routed | 3/3 | 4,774 | 278,108 | 690 | $0.1882 | 43,925 | 20 | 19 | find_capabilities, load_skill, hubspot_search_objects, hubspot_status, hubspot_search_objects, hubspot_list_users, hubsp |

## Routing decisions (routed arms)

| Task | Router | Primary charter | Routing ms | Routing cost | Tools returned |
|---|---|---|---|---|---|
| t13 | jev | - | 651 | $0.0005 | 0 |
| t13 | jev | objects | 645 | $0.0005 | 17 |
| t14 | jev | - | 654 | $0.0005 | 0 |
| t15 | jev | engagements | 601 | $0.0005 | 17 |
| t16 | jev | - | 552 | $0.0005 | 0 |
| t13 | jev | - | 557 | $0.0005 | 0 |
| t14 | jev | - | 612 | $0.0005 | 0 |
| t15 | jev | engagements | 838 | $0.0005 | 17 |
| t16 | jev | - | 595 | $0.0005 | 0 |
| t13 | jev | - | 593 | $0.0005 | 0 |
| t13 | jev | objects | 607 | $0.0005 | 17 |
| t14 | jev | - | 651 | $0.0005 | 0 |
| t15 | jev | engagements | 599 | $0.0005 | 17 |
| t16 | jev | - | 975 | $0.0005 | 0 |

Jev answered 14/14 routes; median routing 610 ms.

## Skills

| Task | Arm | Skill picked (reps) | Skill loaded (reps) | Passed | Ctx first | Ctx all | Cost | Duration ms | Turns |
|---|---|---|---|---|---|---|---|---|---|
| t13 | full | 3/3 | 3/3 | 3/3 | 18,450 | 694,451 | $0.4333 | 45,638 | 14 |
| t13 | routed | 3/3 | 3/3 | 3/3 | 4,746 | 582,029 | $0.4750 | 66,189 | 17 |
| t14 | full | 3/3 | 3/3 | 3/3 | 18,477 | 433,205 | $0.1743 | 34,998 | 14 |
| t14 | routed | 3/3 | 3/3 | 3/3 | 4,763 | 350,725 | $0.2144 | 58,621 | 24 |
| t15 | full | 3/3 | 3/3 | 3/3 | 18,498 | 394,416 | $0.1773 | 26,101 | 11 |
| t15 | routed | 3/3 | 3/3 | 3/3 | 4,786 | 364,906 | $0.2524 | 39,494 | 15 |
| t16 | full | 3/3 | 3/3 | 3/3 | 18,484 | 339,585 | $0.1725 | 34,434 | 12 |
| t16 | routed | 3/3 | 3/3 | 3/3 | 4,774 | 278,108 | $0.1882 | 43,925 | 20 |

Skill picked = the lookup's primary skill matched the expected one; loaded = the model then loaded that skill.

## Write gate outcomes

| Task | Arm | Rep | Tool | Outcome |
|---|---|---|---|---|
| t13 | full | 1 | hubspot_search_objects | error |
| t13 | full | 2 | hubspot_search_objects | error |
| t13 | full | 3 | hubspot_search_objects | error |
| t13 | routed | 1 | hubspot_search_objects | error |
| t13 | routed | 1 | hubspot_search_objects | error |
| t13 | routed | 1 | hubspot_search_objects | error |
| t13 | routed | 2 | hubspot_search_objects | error |
| t13 | routed | 2 | hubspot_search_objects | error |
| t13 | routed | 2 | hubspot_search_objects | error |
| t13 | routed | 2 | hubspot_search_objects | error |
| t13 | routed | 2 | hubspot_search_objects | error |
| t13 | routed | 2 | hubspot_search_objects | error |
| t13 | routed | 3 | hubspot_search_objects | error |
| t13 | routed | 3 | hubspot_search_objects | error |
| t13 | routed | 3 | hubspot_search_objects | error |
| t13 | routed | 3 | hubspot_search_objects | error |
| t14 | full | 1 | hubspot_create_note | applied |
| t14 | full | 2 | hubspot_create_note | applied |
| t14 | full | 3 | hubspot_create_note | applied |
| t14 | routed | 1 | hubspot_create_note | error |
| t14 | routed | 1 | hubspot_create_note | error |
| t14 | routed | 1 | hubspot_list_associated_records | error |
| t14 | routed | 1 | hubspot_list_associated_records | error |
| t14 | routed | 1 | hubspot_list_associated_records | error |
| t14 | routed | 1 | hubspot_list_associated_records | error |
| t14 | routed | 1 | hubspot_list_associated_records | error |
| t14 | routed | 1 | hubspot_list_associated_records | error |
| t14 | routed | 1 | hubspot_list_associated_records | error |
| t14 | routed | 1 | hubspot_list_associated_records | error |
| t14 | routed | 1 | hubspot_search_objects | error |
| t14 | routed | 1 | hubspot_search_objects | error |
| t14 | routed | 2 | hubspot_associate_records | declined |
| t14 | routed | 2 | hubspot_associate_records | error |
| t14 | routed | 2 | hubspot_associate_records | error |
| t14 | routed | 2 | hubspot_create_note | applied |
| t14 | routed | 2 | hubspot_create_note | error |
| t14 | routed | 2 | hubspot_list_associated_records | error |
| t14 | routed | 2 | hubspot_list_associated_records | error |
| t14 | routed | 2 | hubspot_list_associated_records | error |
| t14 | routed | 2 | hubspot_list_associated_records | error |
| t14 | routed | 2 | hubspot_list_associated_records | error |
| t14 | routed | 2 | hubspot_list_associated_records | error |
| t14 | routed | 2 | hubspot_search_objects | error |
| t14 | routed | 2 | hubspot_search_objects | error |
| t14 | routed | 3 | hubspot_create_note | error |
| t14 | routed | 3 | hubspot_create_note | error |
| t14 | routed | 3 | hubspot_list_associated_records | error |
| t14 | routed | 3 | hubspot_list_associated_records | error |
| t14 | routed | 3 | hubspot_list_associated_records | error |
| t14 | routed | 3 | hubspot_list_associated_records | error |
| t14 | routed | 3 | hubspot_list_associated_records | error |
| t14 | routed | 3 | hubspot_list_associated_records | error |
| t14 | routed | 3 | hubspot_list_associated_records | error |
| t14 | routed | 3 | hubspot_list_associated_records | error |
| t14 | routed | 3 | hubspot_search_objects | error |
| t14 | routed | 3 | hubspot_search_objects | error |
| t15 | full | 1 | hubspot_create_note | applied |
| t15 | full | 1 | hubspot_create_note | applied |
| t15 | full | 1 | hubspot_create_task | applied |
| t15 | full | 1 | hubspot_create_task | applied |
| t15 | full | 1 | hubspot_update_object | applied |
| t15 | full | 1 | hubspot_update_object | applied |
| t15 | full | 2 | hubspot_create_note | applied |
| t15 | full | 2 | hubspot_create_task | applied |
| t15 | full | 2 | hubspot_update_object | applied |
| t15 | routed | 1 | hubspot_create_note | applied |
| t15 | routed | 1 | hubspot_create_task | applied |
| t15 | routed | 1 | hubspot_list_associated_records | error |
| t15 | routed | 1 | hubspot_list_associated_records | error |
| t15 | routed | 1 | hubspot_list_associated_records | error |
| t15 | routed | 1 | hubspot_list_associated_records | error |
| t15 | routed | 1 | hubspot_update_object | applied |
| t15 | routed | 2 | hubspot_create_note | applied |
| t15 | routed | 2 | hubspot_create_task | applied |
| t15 | routed | 2 | hubspot_list_associated_records | error |
| t15 | routed | 2 | hubspot_list_associated_records | error |
| t15 | routed | 2 | hubspot_list_associated_records | error |
| t15 | routed | 2 | hubspot_list_associated_records | error |
| t15 | routed | 2 | hubspot_update_object | applied |
| t15 | routed | 3 | hubspot_create_note | applied |
| t15 | routed | 3 | hubspot_create_task | applied |
| t15 | routed | 3 | hubspot_list_associated_records | error |
| t15 | routed | 3 | hubspot_list_associated_records | error |
| t15 | routed | 3 | hubspot_list_associated_records | error |
| t16 | routed | 1 | hubspot_list_associated_records | error |
| t16 | routed | 1 | hubspot_list_associated_records | error |
| t16 | routed | 1 | hubspot_list_associated_records | error |
| t16 | routed | 1 | hubspot_list_associated_records | error |
| t16 | routed | 1 | hubspot_list_associated_records | error |
| t16 | routed | 1 | hubspot_list_associated_records | error |
| t16 | routed | 1 | hubspot_list_associated_records | error |
| t16 | routed | 1 | hubspot_list_associated_records | error |
| t16 | routed | 1 | hubspot_list_associated_records | error |
| t16 | routed | 1 | hubspot_list_associated_records | error |
| t16 | routed | 1 | hubspot_list_associated_records | error |
| t16 | routed | 1 | hubspot_list_associated_records | error |
| t16 | routed | 1 | hubspot_search_objects | error |
| t16 | routed | 1 | hubspot_search_objects | error |
| t16 | routed | 1 | hubspot_search_objects | error |
| t16 | routed | 1 | hubspot_search_objects | error |
| t16 | routed | 2 | hubspot_list_associated_records | error |
| t16 | routed | 2 | hubspot_list_associated_records | error |
| t16 | routed | 2 | hubspot_list_associated_records | error |
| t16 | routed | 2 | hubspot_list_associated_records | error |
| t16 | routed | 2 | hubspot_list_associated_records | error |
| t16 | routed | 2 | hubspot_list_associated_records | error |
| t16 | routed | 2 | hubspot_search_objects | error |
| t16 | routed | 2 | hubspot_search_objects | error |

`declined` = the gate asked for an inline confirmation and the headless host declined it; `applied` = AUTO tier, undone by the runner afterwards.

## Failures and errors

None.
