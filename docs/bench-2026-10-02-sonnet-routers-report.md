# Bench report: 2026-10-02T15-43-59Z

- Sessions: 24 (4 tasks x 3 reps x 2 arms)
- Cost basis: Anthropic list API prices for the model that ran (claude-sonnet-5), as Claude Code reports them (`total_cost_usd`, costBasis list). The sessions ran on a Claude subscription, so the dollar figures are API-equivalent, not money spent.
- Model: sonnet (observed: claude-sonnet-5); Claude Code 2.1.281 (Claude Code)
- Server: http://127.0.0.1:8000

## Context the MCP adds (tools/list)

| Surface | Tools | Bytes | ~tokens (chars/4) | First-call context tokens, median (exact, from sessions) |
|---|---|---|---|---|

## Per arm, medians over all sessions

| Arm | Context tokens, all calls | Cache read | Cache create | Output tokens | Cost | Duration ms | Turns | HubSpot tool calls | Routing calls | Tool errors | Completion rate |
|---|---|---|---|---|---|---|---|---|---|---|---|
| routed-jev | 441,685 | 377,738 | 47,818 | 118 | $0.1686 | 45,732 | 10 | 10 | 2 | 2 | 12/12 |
| routed-kw | 530,836 | 487,447 | 48,748 | 148 | $0.1968 | 54,604 | 13 | 12 | 2 | 2 | 8/12 |

## Per task

| Task | Arm | Passed | Ctx first | Ctx all | Output | Cost | Duration ms | Turns | Tool calls | Server-side tools (rep 1) |
|---|---|---|---|---|---|---|---|---|---|---|
| t13 | routed-jev | 3/3 | 11,150 | 618,572 | 108 | $0.3432 | 102,751 | 12 | 11 | find_capabilities, load_skill, hubspot_status, hubspot_list_users, hubspot_search_objects, hubspot_search_objects, hubsp |
| t13 | routed-kw | 3/3 | 11,152 | 695,419 | 152 | $0.3262 | 79,046 | 14 | 13 | find_capabilities, load_skill, hubspot_status, hubspot_list_users, hubspot_search_objects, hubspot_search_objects, hubsp |
| t14 | routed-jev | 3/3 | 11,163 | 267,546 | 91 | $0.1011 | 19,152 | 9 | 8 | find_capabilities, load_skill, hubspot_list_users, hubspot_search_objects, hubspot_get_object, hubspot_list_associated_r |
| t14 | routed-kw | 3/3 | 11,159 | 231,240 | 128 | $0.1021 | 17,863 | 8 | 7 | find_capabilities, load_skill, hubspot_list_users, hubspot_search_objects, hubspot_list_associated_records, hubspot_list |
| t15 | routed-jev | 3/3 | 11,181 | 891,190 | 201 | $0.2589 | 81,160 | 19 | 18 | find_capabilities, load_skill, hubspot_search_objects, hubspot_search_objects, hubspot_list_associated_records, hubspot_ |
| t15 | routed-kw | 0/3 | 11,183 | 763,611 | 180 | $0.2286 | 72,523 | 17 | 16 | find_capabilities, load_skill, hubspot_list_users, hubspot_search_objects, hubspot_search_objects, hubspot_list_associat |
| t16 | routed-jev | 3/3 | 11,170 | 315,038 | 124 | $0.1221 | 32,445 | 9 | 8 | find_capabilities, load_skill, hubspot_status, hubspot_search_objects, hubspot_list_associated_records, hubspot_list_ass |
| t16 | routed-kw | 2/3 | 11,170 | 377,175 | 144 | $0.1423 | 33,136 | 11 | 10 | find_capabilities, load_skill, hubspot_search_objects, hubspot_status, hubspot_search_objects, hubspot_list_users, hubsp |

## Routing decisions (routed arms)

| Task | Router | Primary charter | Routing ms | Routing cost | Tools returned |
|---|---|---|---|---|---|
| t13 | jev | engagements | 633 | $0.0005 | 24 |
| t14 | jev | - | 1,189 | $0.0005 | 21 |
| t15 | jev | engagements | 636 | $0.0005 | 26 |
| t16 | jev | engagements | 1,017 | $0.0005 | 25 |
| t13 | jev | triage | 581 | $0.0005 | 24 |
| t14 | jev | - | 776 | $0.0005 | 21 |
| t15 | jev | engagements | 734 | $0.0005 | 26 |
| t16 | jev | - | 1,114 | $0.0005 | 23 |
| t13 | jev | engagements | 1,036 | $0.0005 | 24 |
| t14 | jev | - | 601 | $0.0005 | 21 |
| t15 | jev | engagements | 784 | $0.0005 | 26 |
| t16 | jev | - | 1,034 | $0.0005 | 23 |
| t13 | keyword | engagements | 0 | - | 21 |
| t14 | keyword | engagements | 0 | - | 21 |
| t15 | keyword | engagements | 0 | - | 23 |
| t16 | keyword | engagements | 0 | - | 26 |
| t13 | keyword | - | 0 | - | 19 |
| t14 | keyword | engagements | 0 | - | 21 |
| t15 | keyword | engagements | 0 | - | 24 |
| t16 | keyword | engagements | 0 | - | 26 |
| t13 | keyword | engagements | 0 | - | 21 |
| t14 | keyword | engagements | 0 | - | 21 |
| t15 | keyword | engagements | 0 | - | 24 |
| t16 | keyword | engagements | 0 | - | 26 |

Jev answered 12/24 routes; median routing 780 ms.

## Skills

| Task | Arm | Skill picked (reps) | Skill loaded (reps) | Passed | Ctx first | Ctx all | Cost | Duration ms | Turns |
|---|---|---|---|---|---|---|---|---|---|
| t13 | routed-jev | 3/3 | 3/3 | 3/3 | 11,150 | 618,572 | $0.3432 | 102,751 | 12 |
| t13 | routed-kw | 3/3 | 3/3 | 3/3 | 11,152 | 695,419 | $0.3262 | 79,046 | 14 |
| t14 | routed-jev | 3/3 | 3/3 | 3/3 | 11,163 | 267,546 | $0.1011 | 19,152 | 9 |
| t14 | routed-kw | 3/3 | 3/3 | 3/3 | 11,159 | 231,240 | $0.1021 | 17,863 | 8 |
| t15 | routed-jev | 3/3 | 3/3 | 3/3 | 11,181 | 891,190 | $0.2589 | 81,160 | 19 |
| t15 | routed-kw | 0/3 | 3/3 | 0/3 | 11,183 | 763,611 | $0.2286 | 72,523 | 17 |
| t16 | routed-jev | 3/3 | 3/3 | 3/3 | 11,170 | 315,038 | $0.1221 | 32,445 | 9 |
| t16 | routed-kw | 3/3 | 3/3 | 2/3 | 11,170 | 377,175 | $0.1423 | 33,136 | 11 |

Skill picked = the lookup's primary skill matched the expected one; loaded = the model then loaded that skill.

## Routing, per arm

| Arm | Router | Charter right | Skill right | Re-routes per task | Tools returned | Lookup result chars | Routing ms | Routing cost | Arg rejections | Tool errors |
|---|---|---|---|---|---|---|---|---|---|---|
| routed-jev | jev | n/a | 12/12 | 0 | 24 | 20,560 | 780 | $0.0005 | 0 | 17 |
| routed-kw | keyword | n/a | 9/12 | 0 | 22 | 17,656 | 0 | $0.0000 | 4 | 18 |

Charter right / skill right = the first lookup's primary pick matched the task label. Re-routes = extra find_capabilities calls after the first. Arg rejections = proxy schema errors the model had to correct.

## Paired per task: routed-jev vs routed-kw

| Task | Passed routed-jev / routed-kw | Ctx all routed-jev / routed-kw | Cost routed-jev / routed-kw | Duration s routed-jev / routed-kw | Turns routed-jev / routed-kw | Tool calls routed-jev / routed-kw | Picks routed-jev / routed-kw |
|---|---|---|---|---|---|---|---|
| t13 | 3/3 / 3/3 | 618,572 / 695,419 | $0.3432 / $0.3262 | 103 / 79 | 12 / 14 | 11 / 13 | skill 3/3 / skill 3/3 |
| t14 | 3/3 / 3/3 | 267,546 / 231,240 | $0.1011 / $0.1021 | 19 / 18 | 9 / 8 | 8 / 7 | skill 3/3 / skill 3/3 |
| t15 | 3/3 / 0/3 | 891,190 / 763,611 | $0.2589 / $0.2286 | 81 / 73 | 19 / 17 | 18 / 16 | skill 3/3 / skill 0/3 |
| t16 | 3/3 / 2/3 | 315,038 / 377,175 | $0.1221 / $0.1423 | 32 / 33 | 9 / 11 | 8 / 10 | skill 3/3 / skill 3/3 |

Tasks where routed-jev was cheaper on context: 2 vs 2; on cost: 2 vs 2; on time: 1 vs 3.

## Write gate outcomes

| Task | Arm | Rep | Tool | Outcome |
|---|---|---|---|---|
| t13 | routed-jev | 1 | hubspot_search_objects | error |
| t13 | routed-jev | 1 | hubspot_search_objects | error |
| t13 | routed-jev | 2 | hubspot_get_pipeline | error |
| t13 | routed-jev | 2 | hubspot_search_objects | error |
| t13 | routed-jev | 2 | hubspot_search_objects | error |
| t13 | routed-jev | 2 | hubspot_search_objects | error |
| t13 | routed-jev | 3 | hubspot_search_objects | error |
| t13 | routed-jev | 3 | hubspot_search_objects | error |
| t13 | routed-kw | 1 | hubspot_search_objects | error |
| t13 | routed-kw | 1 | hubspot_search_objects | error |
| t13 | routed-kw | 2 | hubspot_search_objects | error |
| t13 | routed-kw | 2 | hubspot_search_objects | error |
| t13 | routed-kw | 3 | hubspot_search_objects | error |
| t13 | routed-kw | 3 | hubspot_search_objects | error |
| t13 | routed-kw | 3 | hubspot_search_objects | error |
| t15 | routed-jev | 1 | hubspot_create_note | applied |
| t15 | routed-jev | 1 | hubspot_create_note | declined |
| t15 | routed-jev | 1 | hubspot_create_note | error |
| t15 | routed-jev | 1 | hubspot_create_task | applied |
| t15 | routed-jev | 2 | hubspot_create_note | applied |
| t15 | routed-jev | 2 | hubspot_create_note | declined |
| t15 | routed-jev | 2 | hubspot_create_note | error |
| t15 | routed-jev | 2 | hubspot_create_task | applied |
| t15 | routed-jev | 2 | hubspot_create_task | declined |
| t15 | routed-jev | 2 | hubspot_create_task | error |
| t15 | routed-jev | 2 | hubspot_update_object | applied |
| t15 | routed-jev | 2 | hubspot_update_object | declined |
| t15 | routed-jev | 2 | hubspot_update_object | declined |
| t15 | routed-jev | 2 | hubspot_update_object | declined |
| t15 | routed-jev | 3 | hubspot_create_note | applied |
| t15 | routed-jev | 3 | hubspot_create_note | declined |
| t15 | routed-jev | 3 | hubspot_create_note | error |
| t15 | routed-jev | 3 | hubspot_create_task | applied |
| t15 | routed-jev | 3 | hubspot_create_task | declined |
| t15 | routed-jev | 3 | hubspot_create_task | error |
| t15 | routed-jev | 3 | hubspot_update_object | applied |
| t15 | routed-jev | 3 | hubspot_update_object | declined |
| t15 | routed-jev | 3 | hubspot_update_object | declined |
| t15 | routed-jev | 3 | hubspot_update_object | declined |
| t15 | routed-kw | 1 | hubspot_associate_records | declined |
| t15 | routed-kw | 1 | hubspot_associate_records | declined |
| t15 | routed-kw | 1 | hubspot_associate_records | declined |
| t15 | routed-kw | 1 | hubspot_associate_records | declined |
| t15 | routed-kw | 1 | hubspot_associate_records | declined |
| t15 | routed-kw | 1 | hubspot_associate_records | error |
| t15 | routed-kw | 1 | hubspot_create_note | applied |
| t15 | routed-kw | 1 | hubspot_create_note | declined |
| t15 | routed-kw | 1 | hubspot_create_note | error |
| t15 | routed-kw | 1 | hubspot_create_task | applied |
| t15 | routed-kw | 1 | hubspot_create_task | declined |
| t15 | routed-kw | 1 | hubspot_create_task | error |
| t15 | routed-kw | 1 | hubspot_delete_object | declined |
| t15 | routed-kw | 1 | hubspot_delete_object | declined |
| t15 | routed-kw | 1 | hubspot_update_object | applied |
| t15 | routed-kw | 1 | hubspot_update_object | declined |
| t15 | routed-kw | 1 | hubspot_update_object | declined |
| t15 | routed-kw | 2 | hubspot_create_note | applied |
| t15 | routed-kw | 2 | hubspot_create_note | declined |
| t15 | routed-kw | 2 | hubspot_create_note | declined |
| t15 | routed-kw | 2 | hubspot_create_note | error |
| t15 | routed-kw | 2 | hubspot_create_note | error |
| t15 | routed-kw | 2 | hubspot_create_task | applied |
| t15 | routed-kw | 2 | hubspot_update_object | applied |
| t15 | routed-kw | 3 | hubspot_create_note | applied |
| t15 | routed-kw | 3 | hubspot_create_note | declined |
| t15 | routed-kw | 3 | hubspot_create_note | error |
| t15 | routed-kw | 3 | hubspot_create_task | applied |
| t15 | routed-kw | 3 | hubspot_create_task | declined |
| t15 | routed-kw | 3 | hubspot_create_task | error |
| t15 | routed-kw | 3 | hubspot_update_object | applied |
| t15 | routed-kw | 3 | hubspot_update_object | declined |
| t15 | routed-kw | 3 | hubspot_update_object | declined |
| t15 | routed-kw | 3 | hubspot_update_object | declined |
| t16 | routed-jev | 1 | hubspot_create_email | declined |
| t16 | routed-jev | 1 | hubspot_create_email | error |
| t16 | routed-jev | 1 | hubspot_list_properties | error |
| t16 | routed-jev | 1 | hubspot_list_properties | error |
| t16 | routed-jev | 3 | hubspot_create_email | error |
| t16 | routed-kw | 1 | hubspot_search_objects | error |
| t16 | routed-kw | 2 | hubspot_create_email | declined |
| t16 | routed-kw | 2 | hubspot_create_email | error |
| t16 | routed-kw | 2 | hubspot_create_email | error |
| t16 | routed-kw | 3 | hubspot_create_email | error |

`declined` = the gate asked for an inline confirmation and the headless host declined it; `applied` = AUTO tier, undone by the runner afterwards.

## Failures and errors

- rep 1 t15 routed-kw: failed ['skill_picked']; rc=0; result=success
- rep 2 t15 routed-kw: failed ['skill_picked']; rc=0; result=success
- rep 3 t15 routed-kw: failed ['skill_picked']; rc=0; result=success
- rep 3 t16 routed-kw: failed ['text_any']; rc=0; result=success
