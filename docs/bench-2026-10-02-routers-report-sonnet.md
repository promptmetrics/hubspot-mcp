# Bench report: 2026-10-02T05-58-55Z

- Sessions: 120 (20 tasks x 3 reps x 2 arms)
- Cost basis: Anthropic list API prices for the model that ran (claude-opus-5-5[1m]), as Claude Code reports them (`total_cost_usd`, costBasis list). The sessions ran on a Claude subscription, so the dollar figures are API-equivalent, not money spent. **Every cost in this report is repriced at sonnet-5.5 list prices** (input $2.0, 1h cache write $4.0, cache read $0.2, output $10.0 per MTok, pricing page read 2026-10-02). Same tokens, different price list; the model's behaviour is unchanged.
- Model: Claude Code default (observed: claude-opus-5-5[1m]); Claude Code 2.1.281 (Claude Code)
- Server: http://127.0.0.1:8000

## Context the MCP adds (tools/list)

| Surface | Tools | Bytes | ~tokens (chars/4) | First-call context tokens, median (exact, from sessions) |
|---|---|---|---|---|

## Per arm, medians over all sessions

| Arm | Context tokens, all calls | Cache read | Cache create | Output tokens | Cost | Duration ms | Turns | HubSpot tool calls | Routing calls | Tool errors | Completion rate |
|---|---|---|---|---|---|---|---|---|---|---|---|
| routed-jev | 234,636 | 197,998 | 33,562 | 201 | $0.1714 | 29,142 | 11 | 10 | 2 | 0 | 53/60 |
| routed-kw | 190,898 | 159,062 | 34,832 | 261 | $0.1740 | 29,496 | 10 | 10 | 2 | 0 | 40/60 |

## Per task

| Task | Arm | Passed | Ctx first | Ctx all | Output | Cost | Duration ms | Turns | Tool calls | Server-side tools (rep 1) |
|---|---|---|---|---|---|---|---|---|---|---|
| t01 | routed-jev | 3/3 | 4,771 | 497,776 | 674 | $0.5168 | 53,817 | 16 | 15 | find_capabilities, load_skill, hubspot_list_pipelines, hubspot_search_objects, hubspot_search_objects, hubspot_search_ob |
| t01 | routed-kw | 3/3 | 4,771 | 538,345 | 373 | $0.6519 | 48,553 | 16 | 15 | find_capabilities, load_skill, hubspot_list_pipelines, hubspot_search_objects, hubspot_search_objects, hubspot_search_ob |
| t02 | routed-jev | 3/3 | 4,761 | 569,852 | 206 | $0.4259 | 53,257 | 14 | 13 | find_capabilities, load_skill, hubspot_list_pipelines, hubspot_search_objects, hubspot_list_users, hubspot_search_object |
| t02 | routed-kw | 0/3 | 4,759 | 457,977 | 157 | $0.3588 | 47,961 | 12 | 11 | find_capabilities, load_skill, hubspot_list_pipelines, hubspot_list_users, hubspot_search_objects, hubspot_search_object |
| t03 | routed-jev | 3/3 | 4,785 | 238,305 | 362 | $0.1529 | 26,258 | 13 | 12 | find_capabilities, load_skill, hubspot_search_objects, hubspot_list_associated_records, hubspot_list_associated_records, |
| t03 | routed-kw | 3/3 | 4,785 | 182,282 | 342 | $0.1768 | 22,624 | 12 | 11 | find_capabilities, load_skill, hubspot_search_objects, hubspot_list_associated_records, hubspot_list_associated_records, |
| t04 | routed-jev | 3/3 | 4,780 | 130,216 | 222 | $0.1673 | 16,631 | 6 | 5 | find_capabilities, load_skill, hubspot_search_objects, hubspot_create_note, hubspot_list_associated_records |
| t04 | routed-kw | 3/3 | 4,784 | 146,189 | 298 | $0.1027 | 22,915 | 8 | 7 | find_capabilities, load_charter, hubspot_search_objects, hubspot_create_note, hubspot_get_engagement, hubspot_search_eng |
| t05 | routed-jev | 3/3 | 4,771 | 124,106 | 146 | $0.1088 | 20,734 | 7 | 6 | find_capabilities, load_skill, hubspot_search_objects, hubspot_update_object, hubspot_list_pending_writes, hubspot_get_o |
| t05 | routed-kw | 3/3 | 4,769 | 84,638 | 152 | $0.0655 | 17,469 | 7 | 6 | find_capabilities, load_charter, hubspot_search_objects, hubspot_update_object, hubspot_list_pending_writes, hubspot_get |
| t06 | routed-jev | 3/3 | 4,780 | 193,674 | 149 | $0.1638 | 24,499 | 8 | 7 | find_capabilities, load_skill, hubspot_search_objects, hubspot_create_task, hubspot_list_users, hubspot_list_associated_ |
| t06 | routed-kw | 3/3 | 4,781 | 156,486 | 540 | $0.1674 | 24,159 | 8 | 7 | find_capabilities, load_skill, hubspot_search_objects, hubspot_list_users, hubspot_create_task, hubspot_list_associated_ |
| t07 | routed-jev | 3/3 | 4,755 | 62,659 | 52 | $0.1035 | 12,986 | 3 | 2 | find_capabilities, hubspot_list_workflows |
| t07 | routed-kw | 0/3 | 4,755 | 84,814 | 203 | $0.1765 | 12,606 | 4 | 3 | find_capabilities, load_charter, hubspot_list_workflows |
| t08 | routed-jev | 3/3 | 4,758 | 124,141 | 121 | $0.1535 | 16,121 | 5 | 4 | find_capabilities, load_charter, hubspot_find_duplicates, hubspot_search_objects |
| t08 | routed-kw | 3/3 | 4,758 | 116,701 | 200 | $0.1544 | 20,979 | 5 | 4 | find_capabilities, load_charter, hubspot_find_duplicates, hubspot_search_objects |
| t09 | routed-jev | 3/3 | 4,757 | 238,794 | 231 | $0.1881 | 22,439 | 12 | 11 | find_capabilities, hubspot_list_properties, hubspot_get_property, hubspot_get_property, hubspot_get_property, hubspot_ge |
| t09 | routed-kw | 0/3 | 4,757 | 159,914 | 127 | $0.1229 | 25,131 | 12 | 11 | find_capabilities, hubspot_list_properties, hubspot_get_property, hubspot_get_property, hubspot_get_property, hubspot_ge |
| t10 | routed-jev | 2/3 | 4,813 | 266,793 | 220 | $0.1814 | 30,555 | 10 | 9 | find_capabilities, load_skill, hubspot_search_objects, hubspot_search_objects, hubspot_get_pipeline, hubspot_create_call |
| t10 | routed-kw | 3/3 | 4,815 | 265,704 | 172 | $0.2257 | 32,637 | 10 | 9 | find_capabilities, load_skill, hubspot_search_objects, hubspot_search_objects, hubspot_get_pipeline, hubspot_create_call |
| t11 | routed-jev | 3/3 | 4,756 | 51,716 | 102 | $0.0745 | 13,546 | 3 | 2 | find_capabilities, hubspot_list_users |
| t11 | routed-kw | 3/3 | 4,756 | 53,754 | 96 | $0.0881 | 9,390 | 4 | 3 | find_capabilities, load_charter, hubspot_list_users |
| t12 | routed-jev | 3/3 | 4,750 | 9,500 | 16 | $0.0194 | 4,206 | 1 | 0 |  |
| t12 | routed-kw | 3/3 | 4,752 | 14,622 | 126 | $0.0230 | 6,514 | 2 | 1 | find_capabilities |
| t13 | routed-jev | 3/3 | 4,748 | 409,676 | 127 | $0.6305 | 42,236 | 13 | 12 | find_capabilities, load_skill, hubspot_status, hubspot_list_users, hubspot_list_pipelines, hubspot_search_objects, hubsp |
| t13 | routed-kw | 3/3 | 4,746 | 432,067 | 139 | $0.6403 | 50,157 | 13 | 12 | find_capabilities, load_skill, hubspot_status, hubspot_list_users, hubspot_list_pipelines, hubspot_search_objects, hubsp |
| t14 | routed-jev | 3/3 | 4,767 | 262,162 | 379 | $0.1725 | 31,031 | 14 | 13 | find_capabilities, load_skill, hubspot_search_objects, hubspot_list_associated_records, hubspot_list_associated_records, |
| t14 | routed-kw | 3/3 | 4,769 | 264,417 | 389 | $0.1695 | 34,476 | 14 | 13 | find_capabilities, load_skill, hubspot_search_objects, hubspot_list_users, hubspot_list_associated_records, hubspot_list |
| t15 | routed-jev | 3/3 | 4,790 | 261,447 | 202 | $0.1533 | 43,601 | 10 | 9 | find_capabilities, load_skill, hubspot_search_objects, hubspot_list_associated_records, hubspot_create_note, hubspot_upd |
| t15 | routed-kw | 0/3 | 4,790 | 249,546 | 292 | $0.1769 | 28,715 | 10 | 9 | find_capabilities, load_skill, hubspot_search_objects, hubspot_list_pipelines, hubspot_list_associated_records, hubspot_ |
| t16 | routed-jev | 3/3 | 4,778 | 203,013 | 332 | $0.1702 | 50,301 | 12 | 11 | find_capabilities, load_skill, hubspot_search_objects, hubspot_list_associated_records, hubspot_list_associated_records, |
| t16 | routed-kw | 3/3 | 4,778 | 219,544 | 348 | $0.1716 | 40,961 | 12 | 11 | find_capabilities, load_skill, hubspot_search_objects, hubspot_status, hubspot_list_users, hubspot_list_associated_recor |
| t17 | routed-jev | 0/3 | 4,781 | 298,024 | 207 | $0.1927 | 45,597 | 12 | 11 | find_capabilities, load_skill, hubspot_search_objects, hubspot_list_pipelines, hubspot_search_objects, hubspot_search_ob |
| t17 | routed-kw | 0/3 | 4,781 | 452,455 | 343 | $0.2691 | 57,995 | 16 | 15 | find_capabilities, find_capabilities, load_skill, hubspot_search_objects, hubspot_list_pipelines, hubspot_list_associate |
| t18 | routed-jev | 3/3 | 4,741 | 610,403 | 152 | $0.7710 | 59,751 | 13 | 12 | find_capabilities, load_skill, hubspot_status, hubspot_list_users, hubspot_list_pipelines, hubspot_search_objects, hubsp |
| t18 | routed-kw | 2/3 | 4,745 | 443,807 | 386 | $0.5101 | 51,390 | 14 | 13 | find_capabilities, load_skill, hubspot_status, hubspot_list_users, hubspot_list_pipelines, hubspot_search_objects, hubsp |
| t19 | routed-jev | 0/3 | 4,760 | 128,893 | 294 | $0.0914 | 22,555 | 12 | 11 | find_capabilities, load_skill, hubspot_search_objects, hubspot_list_users, hubspot_list_associated_records, hubspot_list |
| t19 | routed-kw | 2/3 | 4,760 | 212,774 | 309 | $0.1357 | 24,475 | 13 | 12 | find_capabilities, find_capabilities, load_skill, hubspot_search_objects, hubspot_status, hubspot_list_associated_record |
| t20 | routed-jev | 3/3 | 4,753 | 336,921 | 180 | $0.2787 | 42,413 | 8 | 7 | find_capabilities, load_skill, hubspot_search_objects, hubspot_list_pipelines, hubspot_search_objects, hubspot_search_ob |
| t20 | routed-kw | 0/3 | 4,755 | 159,142 | 108 | $0.2998 | 51,031 | 6 | 5 | find_capabilities, load_charter, hubspot_search_objects, hubspot_search_objects, hubspot_search_objects |

## Routing decisions (routed arms)

| Task | Router | Primary charter | Routing ms | Routing cost | Tools returned |
|---|---|---|---|---|---|
| t01 | jev | analytics | 885 | $0.0005 | 31 |
| t02 | jev | objects | 605 | $0.0005 | 30 |
| t03 | jev | objects | 558 | $0.0005 | 32 |
| t04 | jev | engagements | 739 | $0.0005 | 26 |
| t05 | jev | objects | 607 | $0.0005 | 23 |
| t06 | jev | engagements | 747 | $0.0005 | 24 |
| t07 | jev | workflows | 969 | $0.0005 | 31 |
| t08 | jev | hygiene | 590 | $0.0005 | 35 |
| t09 | jev | properties | 553 | $0.0005 | 30 |
| t10 | jev | engagements | 682 | $0.0005 | 30 |
| t11 | jev | users | 741 | $0.0005 | 24 |
| t13 | jev | - | 610 | $0.0005 | 19 |
| t14 | jev | - | 645 | $0.0005 | 19 |
| t15 | jev | engagements | 1,099 | $0.0005 | 26 |
| t16 | jev | - | 701 | $0.0005 | 23 |
| t17 | jev | - | 833 | $0.0005 | 19 |
| t18 | jev | triage | 916 | $0.0005 | 19 |
| t19 | jev | - | 1,091 | $0.0005 | 6 |
| t20 | jev | objects | 533 | $0.0005 | 39 |
| t01 | jev | analytics | 593 | $0.0005 | 31 |
| t02 | jev | objects | 576 | $0.0005 | 24 |
| t03 | jev | objects | 660 | $0.0005 | 28 |
| t04 | jev | engagements | 609 | $0.0005 | 26 |
| t05 | jev | objects | 683 | $0.0005 | 22 |
| t06 | jev | engagements | 920 | $0.0005 | 24 |
| t07 | jev | workflows | 627 | $0.0005 | 31 |
| t08 | jev | hygiene | 653 | $0.0005 | 35 |
| t09 | jev | properties | 547 | $0.0005 | 30 |
| t10 | jev | triage | 1,342 | $0.0005 | 33 |
| t11 | jev | users | 593 | $0.0005 | 24 |
| t13 | jev | - | 607 | $0.0005 | 19 |
| t14 | jev | - | 588 | $0.0005 | 21 |
| t15 | jev | engagements | 560 | $0.0005 | 26 |
| t16 | jev | - | 1,062 | $0.0005 | 23 |
| t17 | jev | - | 628 | $0.0005 | 19 |
| t18 | jev | triage | 595 | $0.0005 | 19 |
| t19 | jev | - | 687 | $0.0005 | 6 |
| t20 | jev | objects | 562 | $0.0005 | 39 |
| t01 | jev | analytics | 972 | $0.0005 | 31 |
| t02 | jev | objects | 609 | $0.0005 | 24 |

Jev answered 57/125 routes; median routing 651 ms.

## Skills

| Task | Arm | Skill picked (reps) | Skill loaded (reps) | Passed | Ctx first | Ctx all | Cost | Duration ms | Turns |
|---|---|---|---|---|---|---|---|---|---|
| t13 | routed-jev | 3/3 | 3/3 | 3/3 | 4,748 | 409,676 | $0.6305 | 42,236 | 13 |
| t13 | routed-kw | 3/3 | 3/3 | 3/3 | 4,746 | 432,067 | $0.6403 | 50,157 | 13 |
| t14 | routed-jev | 3/3 | 3/3 | 3/3 | 4,767 | 262,162 | $0.1725 | 31,031 | 14 |
| t14 | routed-kw | 3/3 | 3/3 | 3/3 | 4,769 | 264,417 | $0.1695 | 34,476 | 14 |
| t15 | routed-jev | 3/3 | 3/3 | 3/3 | 4,790 | 261,447 | $0.1533 | 43,601 | 10 |
| t15 | routed-kw | 0/3 | 3/3 | 0/3 | 4,790 | 249,546 | $0.1769 | 28,715 | 10 |
| t16 | routed-jev | 3/3 | 3/3 | 3/3 | 4,778 | 203,013 | $0.1702 | 50,301 | 12 |
| t16 | routed-kw | 3/3 | 3/3 | 3/3 | 4,778 | 219,544 | $0.1716 | 40,961 | 12 |
| t17 | routed-jev | 3/3 | 3/3 | 0/3 | 4,781 | 298,024 | $0.1927 | 45,597 | 12 |
| t17 | routed-kw | 0/3 | 3/3 | 0/3 | 4,781 | 452,455 | $0.2691 | 57,995 | 16 |
| t18 | routed-jev | 3/3 | 3/3 | 3/3 | 4,741 | 610,403 | $0.7710 | 59,751 | 13 |
| t18 | routed-kw | 2/3 | 3/3 | 2/3 | 4,745 | 443,807 | $0.5101 | 51,390 | 14 |
| t19 | routed-jev | 3/3 | 3/3 | 0/3 | 4,760 | 128,893 | $0.0914 | 22,555 | 12 |
| t19 | routed-kw | 3/3 | 3/3 | 2/3 | 4,760 | 212,774 | $0.1357 | 24,475 | 13 |
| t20 | routed-jev | 3/3 | 3/3 | 3/3 | 4,753 | 336,921 | $0.2787 | 42,413 | 8 |
| t20 | routed-kw | 0/3 | 0/3 | 0/3 | 4,755 | 159,142 | $0.2998 | 51,031 | 6 |

Skill picked = the lookup's primary skill matched the expected one; loaded = the model then loaded that skill.

## Routing, per arm

| Arm | Router | Charter right | Skill right | Re-routes per task | Tools returned | Lookup result chars | Routing ms | Routing cost | Arg rejections | Tool errors |
|---|---|---|---|---|---|---|---|---|---|---|
| routed-jev | jev | 35/42 | 24/24 | 0 | 24 | 22,368 | 651 | $0.0005 | 0 | 28 |
| routed-kw | keyword | 32/42 | 14/24 | 0 | 22 | 18,470 | 0 | $0.0000 | 4 | 29 |

Charter right / skill right = the first lookup's primary pick matched the task label. Re-routes = extra find_capabilities calls after the first. Arg rejections = proxy schema errors the model had to correct.

## Paired per task: routed-jev vs routed-kw

| Task | Passed routed-jev / routed-kw | Ctx all routed-jev / routed-kw | Cost routed-jev / routed-kw | Duration s routed-jev / routed-kw | Turns routed-jev / routed-kw | Tool calls routed-jev / routed-kw | Picks routed-jev / routed-kw |
|---|---|---|---|---|---|---|---|
| t01 | 3/3 / 3/3 | 497,776 / 538,345 | $0.5168 / $0.6519 | 54 / 49 | 16 / 16 | 15 / 15 | charter 3/3 / charter 3/3 |
| t02 | 3/3 / 0/3 | 569,852 / 457,977 | $0.4259 / $0.3588 | 53 / 48 | 14 / 12 | 13 / 11 | charter 3/3 / charter 0/3 |
| t03 | 3/3 / 3/3 | 238,305 / 182,282 | $0.1529 / $0.1768 | 26 / 23 | 13 / 12 | 12 / 11 | charter 3/3 / charter 3/3 |
| t04 | 3/3 / 3/3 | 130,216 / 146,189 | $0.1673 / $0.1027 | 17 / 23 | 6 / 8 | 5 / 7 | charter 3/3 / charter 3/3 |
| t05 | 3/3 / 3/3 | 124,106 / 84,638 | $0.1088 / $0.0655 | 21 / 17 | 7 / 7 | 6 / 6 | charter 3/3 / charter 3/3 |
| t06 | 3/3 / 3/3 | 193,674 / 156,486 | $0.1638 / $0.1674 | 24 / 24 | 8 / 8 | 7 / 7 | charter 3/3 / charter 3/3 |
| t07 | 3/3 / 0/3 | 62,659 / 84,814 | $0.1035 / $0.1765 | 13 / 13 | 3 / 4 | 2 / 3 | charter 3/3 / charter 0/3 |
| t08 | 3/3 / 3/3 | 124,141 / 116,701 | $0.1535 / $0.1544 | 16 / 21 | 5 / 5 | 4 / 4 | charter 3/3 / charter 3/3 |
| t09 | 3/3 / 0/3 | 238,794 / 159,914 | $0.1881 / $0.1229 | 22 / 25 | 12 / 12 | 11 / 11 | charter 3/3 / charter 0/3 |
| t10 | 2/3 / 3/3 | 266,793 / 265,704 | $0.1814 / $0.2257 | 31 / 33 | 10 / 10 | 9 / 9 | charter 2/3 / charter 3/3 |
| t11 | 3/3 / 3/3 | 51,716 / 53,754 | $0.0745 / $0.0881 | 14 / 9 | 3 / 4 | 2 / 3 | charter 3/3 / charter 3/3 |
| t12 | 3/3 / 3/3 | 9,500 / 14,622 | $0.0194 / $0.0230 | 4 / 7 | 1 / 2 | 0 / 1 | - / - |
| t13 | 3/3 / 3/3 | 409,676 / 432,067 | $0.6305 / $0.6403 | 42 / 50 | 13 / 13 | 12 / 12 | skill 3/3 / skill 3/3 |
| t14 | 3/3 / 3/3 | 262,162 / 264,417 | $0.1725 / $0.1695 | 31 / 34 | 14 / 14 | 13 / 13 | skill 3/3 / skill 3/3 |
| t15 | 3/3 / 0/3 | 261,447 / 249,546 | $0.1533 / $0.1769 | 44 / 29 | 10 / 10 | 9 / 9 | skill 3/3 / skill 0/3 |
| t16 | 3/3 / 3/3 | 203,013 / 219,544 | $0.1702 / $0.1716 | 50 / 41 | 12 / 12 | 11 / 11 | skill 3/3 / skill 3/3 |
| t17 | 0/3 / 0/3 | 298,024 / 452,455 | $0.1927 / $0.2691 | 46 / 58 | 12 / 16 | 11 / 15 | charter 0/3, skill 3/3 / charter 3/3, skill 0/3 |
| t18 | 3/3 / 2/3 | 610,403 / 443,807 | $0.7710 / $0.5101 | 60 / 51 | 13 / 14 | 12 / 13 | skill 3/3 / skill 2/3 |
| t19 | 0/3 / 2/3 | 128,893 / 212,774 | $0.0914 / $0.1357 | 23 / 24 | 12 / 13 | 11 / 12 | charter 0/3, skill 3/3 / charter 2/3, skill 3/3 |
| t20 | 3/3 / 0/3 | 336,921 / 159,142 | $0.2787 / $0.2998 | 42 / 51 | 8 / 6 | 7 / 5 | charter 3/3, skill 3/3 / charter 3/3, skill 0/3 |

Tasks where routed-jev was cheaper on context: 10 vs 10; on cost: 14 vs 6; on time: 10 vs 10.

## Write gate outcomes

| Task | Arm | Rep | Tool | Outcome |
|---|---|---|---|---|
| t02 | routed-kw | 2 | hubspot_search_objects | error |
| t02 | routed-kw | 2 | hubspot_search_objects | error |
| t04 | routed-jev | 1 | hubspot_create_note | applied |
| t04 | routed-jev | 2 | hubspot_create_note | applied |
| t04 | routed-jev | 3 | hubspot_create_note | applied |
| t04 | routed-kw | 1 | hubspot_create_note | applied |
| t04 | routed-kw | 2 | hubspot_create_note | applied |
| t04 | routed-kw | 3 | hubspot_create_note | applied |
| t05 | routed-jev | 1 | hubspot_update_object | declined |
| t05 | routed-jev | 2 | hubspot_update_object | declined |
| t05 | routed-jev | 3 | hubspot_update_object | declined |
| t05 | routed-kw | 1 | hubspot_update_object | declined |
| t05 | routed-kw | 2 | hubspot_update_object | declined |
| t05 | routed-kw | 3 | hubspot_update_object | declined |
| t06 | routed-jev | 1 | hubspot_create_task | applied |
| t06 | routed-jev | 1 | hubspot_update_object | applied |
| t06 | routed-jev | 2 | hubspot_create_task | applied |
| t06 | routed-jev | 2 | hubspot_update_object | applied |
| t06 | routed-jev | 3 | hubspot_create_task | applied |
| t06 | routed-jev | 3 | hubspot_update_object | applied |
| t06 | routed-kw | 1 | hubspot_create_task | applied |
| t06 | routed-kw | 1 | hubspot_update_object | applied |
| t06 | routed-kw | 2 | hubspot_create_task | applied |
| t06 | routed-kw | 2 | hubspot_update_object | applied |
| t06 | routed-kw | 3 | hubspot_create_task | applied |
| t06 | routed-kw | 3 | hubspot_update_object | applied |
| t09 | routed-jev | 1 | hubspot_get_property | error |
| t09 | routed-jev | 1 | hubspot_get_property | error |
| t09 | routed-jev | 1 | hubspot_get_property | error |
| t09 | routed-jev | 1 | hubspot_get_property | error |
| t09 | routed-jev | 1 | hubspot_get_property | error |
| t09 | routed-jev | 1 | hubspot_get_property | error |
| t09 | routed-jev | 1 | hubspot_get_property | error |
| t09 | routed-jev | 2 | hubspot_get_property | error |
| t09 | routed-jev | 2 | hubspot_get_property | error |
| t09 | routed-jev | 2 | hubspot_get_property | error |
| t09 | routed-jev | 2 | hubspot_get_property | error |
| t09 | routed-jev | 2 | hubspot_get_property | error |
| t09 | routed-jev | 2 | hubspot_get_property | error |
| t09 | routed-jev | 2 | hubspot_get_property | error |
| t09 | routed-jev | 3 | hubspot_get_property | error |
| t09 | routed-jev | 3 | hubspot_get_property | error |
| t09 | routed-jev | 3 | hubspot_get_property | error |
| t09 | routed-jev | 3 | hubspot_get_property | error |
| t09 | routed-jev | 3 | hubspot_get_property | error |
| t09 | routed-jev | 3 | hubspot_get_property | error |
| t09 | routed-jev | 3 | hubspot_get_property | error |
| t09 | routed-jev | 3 | hubspot_get_property | error |
| t09 | routed-kw | 1 | hubspot_get_property | error |
| t09 | routed-kw | 1 | hubspot_get_property | error |
| t09 | routed-kw | 1 | hubspot_get_property | error |
| t09 | routed-kw | 1 | hubspot_get_property | error |
| t09 | routed-kw | 1 | hubspot_get_property | error |
| t09 | routed-kw | 2 | hubspot_get_property | error |
| t09 | routed-kw | 2 | hubspot_get_property | error |
| t09 | routed-kw | 2 | hubspot_get_property | error |
| t09 | routed-kw | 2 | hubspot_get_property | error |
| t09 | routed-kw | 2 | hubspot_get_property | error |
| t09 | routed-kw | 2 | hubspot_get_property | error |
| t09 | routed-kw | 2 | hubspot_get_property | error |
| t09 | routed-kw | 3 | hubspot_get_property | error |
| t09 | routed-kw | 3 | hubspot_get_property | error |
| t09 | routed-kw | 3 | hubspot_get_property | error |
| t09 | routed-kw | 3 | hubspot_get_property | error |
| t09 | routed-kw | 3 | hubspot_get_property | error |
| t09 | routed-kw | 3 | hubspot_get_property | error |
| t09 | routed-kw | 3 | hubspot_get_property | error |
| t10 | routed-jev | 1 | hubspot_create_call | applied |
| t10 | routed-jev | 1 | hubspot_update_object | declined |
| t10 | routed-jev | 2 | hubspot_create_call | applied |
| t10 | routed-jev | 2 | hubspot_update_object | declined |
| t10 | routed-jev | 3 | hubspot_create_call | applied |
| t10 | routed-jev | 3 | hubspot_update_object | declined |
| t10 | routed-kw | 1 | hubspot_create_call | applied |
| t10 | routed-kw | 1 | hubspot_update_object | declined |
| t10 | routed-kw | 2 | hubspot_create_call | applied |
| t10 | routed-kw | 2 | hubspot_update_object | declined |
| t10 | routed-kw | 3 | hubspot_create_call | applied |
| t10 | routed-kw | 3 | hubspot_update_object | declined |
| t13 | routed-jev | 1 | hubspot_search_objects | error |
| t13 | routed-jev | 2 | hubspot_search_objects | error |
| t13 | routed-jev | 3 | hubspot_search_objects | error |
| t13 | routed-kw | 1 | hubspot_search_objects | error |
| t13 | routed-kw | 2 | hubspot_search_objects | error |
| t13 | routed-kw | 3 | hubspot_search_objects | error |
| t14 | routed-jev | 1 | hubspot_create_note | applied |
| t14 | routed-jev | 2 | hubspot_create_note | applied |
| t14 | routed-jev | 3 | hubspot_create_note | applied |
| t14 | routed-kw | 1 | hubspot_create_note | applied |
| t14 | routed-kw | 2 | hubspot_create_note | applied |
| t14 | routed-kw | 3 | hubspot_create_note | applied |
| t15 | routed-jev | 1 | hubspot_create_note | applied |
| t15 | routed-jev | 1 | hubspot_create_task | applied |
| t15 | routed-jev | 1 | hubspot_update_object | declined |
| t15 | routed-jev | 2 | hubspot_create_note | applied |
| t15 | routed-jev | 2 | hubspot_create_task | applied |
| t15 | routed-jev | 2 | hubspot_update_object | applied |
| t15 | routed-jev | 2 | hubspot_update_object | applied |
| t15 | routed-jev | 3 | hubspot_create_note | applied |
| t15 | routed-jev | 3 | hubspot_create_task | applied |
| t15 | routed-jev | 3 | hubspot_update_object | declined |
| t15 | routed-kw | 1 | hubspot_create_note | applied |
| t15 | routed-kw | 1 | hubspot_create_task | applied |
| t15 | routed-kw | 1 | hubspot_update_object | applied |
| t15 | routed-kw | 2 | hubspot_create_note | applied |
| t15 | routed-kw | 2 | hubspot_create_task | applied |
| t15 | routed-kw | 2 | hubspot_update_object | applied |
| t15 | routed-kw | 3 | hubspot_create_note | applied |
| t15 | routed-kw | 3 | hubspot_create_task | applied |
| t17 | routed-jev | 1 | hubspot_associate_records | declined |
| t17 | routed-jev | 1 | hubspot_create_note | applied |
| t17 | routed-jev | 1 | hubspot_create_task | applied |
| t17 | routed-jev | 1 | hubspot_update_object | applied |
| t17 | routed-jev | 1 | hubspot_update_object | declined |
| t17 | routed-jev | 2 | hubspot_create_note | applied |
| t17 | routed-jev | 2 | hubspot_create_task | applied |
| t17 | routed-jev | 2 | hubspot_update_object | declined |
| t17 | routed-jev | 3 | hubspot_associate_records | declined |
| t17 | routed-jev | 3 | hubspot_create_note | applied |
| t17 | routed-jev | 3 | hubspot_create_task | applied |
| t17 | routed-jev | 3 | hubspot_update_object | applied |
| t17 | routed-kw | 1 | hubspot_associate_records | declined |
| t17 | routed-kw | 1 | hubspot_create_note | applied |
| t17 | routed-kw | 1 | hubspot_create_task | applied |
| t17 | routed-kw | 1 | hubspot_update_object | declined |
| t17 | routed-kw | 1 | hubspot_update_object | declined |
| t17 | routed-kw | 2 | hubspot_create_note | applied |
| t17 | routed-kw | 2 | hubspot_create_task | applied |
| t17 | routed-kw | 2 | hubspot_update_object | declined |
| t17 | routed-kw | 3 | hubspot_create_note | applied |
| t17 | routed-kw | 3 | hubspot_create_task | applied |
| t17 | routed-kw | 3 | hubspot_update_object | declined |
| t18 | routed-jev | 1 | hubspot_search_objects | error |
| t18 | routed-jev | 2 | hubspot_search_objects | error |
| t18 | routed-jev | 3 | hubspot_search_objects | error |
| t18 | routed-kw | 1 | hubspot_search_objects | error |
| t18 | routed-kw | 1 | hubspot_search_objects | error |
| t18 | routed-kw | 2 | hubspot_search_objects | error |
| t18 | routed-kw | 3 | hubspot_search_objects | error |
| t18 | routed-kw | 3 | hubspot_search_objects | error |

`declined` = the gate asked for an inline confirmation and the headless host declined it; `applied` = AUTO tier, undone by the runner afterwards.

## Failures and errors

- rep 1 t02 routed-kw: failed ['charter_picked']; rc=0; result=success
- rep 1 t07 routed-kw: failed ['charter_picked']; rc=0; result=success
- rep 1 t09 routed-kw: failed ['charter_picked']; rc=0; result=success
- rep 1 t15 routed-kw: failed ['skill_picked']; rc=0; result=success
- rep 1 t17 routed-kw: failed ['skill_picked']; rc=0; result=success
- rep 1 t17 routed-jev: failed ['charter_picked']; rc=0; result=success
- rep 1 t18 routed-kw: failed ['skill_picked']; rc=0; result=success
- rep 1 t19 routed-jev: failed ['charter_picked']; rc=0; result=success
- rep 1 t20 routed-kw: failed ['skill_picked', 'skill_loaded']; rc=0; result=success
- rep 2 t02 routed-kw: failed ['charter_picked']; rc=0; result=success
- rep 2 t07 routed-kw: failed ['charter_picked']; rc=0; result=success
- rep 2 t09 routed-kw: failed ['charter_picked']; rc=0; result=success
- rep 2 t10 routed-jev: failed ['charter_picked']; rc=0; result=success
- rep 2 t15 routed-kw: failed ['skill_picked']; rc=0; result=success
- rep 2 t17 routed-jev: failed ['charter_picked']; rc=0; result=success
- rep 2 t17 routed-kw: failed ['skill_picked']; rc=0; result=success
- rep 2 t19 routed-jev: failed ['charter_picked']; rc=0; result=success
- rep 2 t19 routed-kw: failed ['charter_picked']; rc=0; result=success
- rep 2 t20 routed-kw: failed ['skill_picked', 'skill_loaded']; rc=0; result=success
- rep 3 t02 routed-kw: failed ['charter_picked']; rc=0; result=success
- rep 3 t07 routed-kw: failed ['charter_picked']; rc=0; result=success
- rep 3 t09 routed-kw: failed ['charter_picked']; rc=0; result=success
- rep 3 t15 routed-kw: failed ['skill_picked']; rc=0; result=success
- rep 3 t17 routed-kw: failed ['skill_picked']; rc=0; result=success
- rep 3 t17 routed-jev: failed ['charter_picked']; rc=0; result=success
- rep 3 t19 routed-jev: failed ['charter_picked']; rc=0; result=success
- rep 3 t20 routed-kw: failed ['skill_picked', 'skill_loaded']; rc=0; result=success
