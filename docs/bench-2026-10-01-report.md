# Bench report: 2026-10-01T06-25-37Z

- Sessions: 106 (12 tasks x 3 reps x 3 arms)
- Model: Claude Code default (observed: claude-opus-5-5[1m]); Claude Code 2.1.281 (Claude Code)
- Server: http://127.0.0.1:8000

## Context the MCP adds (tools/list)

| Surface | Tools | Bytes | ~tokens (chars/4) | First-call context tokens, median (exact, from sessions) |
|---|---|---|---|---|
| control | 0 | - | - | 3,304 |
| full | 80 | 30,534 | 7,633 | 17,944 (+14,640 vs control) |
| routed | 3 | 1,637 | 409 | 4,517 (+1,213 vs control) |

Full/routed tools/list ratio: 18.7x by bytes.

## Per arm, medians over all sessions

| Arm | Context tokens, all calls | Cache read | Cache create | Output tokens | Cost | Duration ms | Turns | HubSpot tool calls | Routing calls | Tool errors | Completion rate |
|---|---|---|---|---|---|---|---|---|---|---|---|
| control | 6,608 | 2,654 | 3,950 | 12 | $0.0243 | 8,190 | 1 | 0 | 0 | 0 | 3/3 |
| full | 132,414 | 125,684 | 7,497 | 85 | $0.0672 | 20,158 | 4 | 4 | 0 | 0 | 36/36 |
| routed | 152,870 | 128,745 | 26,608 | 153 | $0.1469 | 28,903 | 8 | 6 | 2 | 0 | 36/36 |

## Per task

| Task | Arm | Passed | Ctx first | Ctx all | Output | Cost | Duration ms | Turns | Tool calls | Server-side tools (rep 1) |
|---|---|---|---|---|---|---|---|---|---|---|
| t01 | control | n/a | 3,312 | 6,624 | 13 | $0.1011 | 37,688 | 1 | 0 |  |
| t01 | full | 3/3 | 17,953 | 409,354 | 138 | $0.4035 | 44,022 | 14 | 13 | hubspot_list_pipelines, hubspot_search_objects, hubspot_search_objects, hubspot_search_objects, hubspot_search_objects,  |
| t01 | routed | 3/3 | 4,522 | 406,585 | 671 | $0.4965 | 57,290 | 16 | 15 | find_capabilities, find_capabilities, load_charter, hubspot_list_pipelines, hubspot_search_objects, hubspot_search_objec |
| t02 | control | n/a | 3,300 | 6,600 | 12 | $0.0250 | 7,881 | 1 | 0 |  |
| t02 | full | 3/3 | 17,939 | 611,571 | 147 | $0.3657 | 55,288 | 11 | 10 | hubspot_list_pipelines, hubspot_search_objects, hubspot_search_objects, hubspot_search_objects, hubspot_search_objects,  |
| t02 | routed | 3/3 | 4,512 | 393,552 | 152 | $0.5658 | 69,776 | 10 | 9 | find_capabilities, load_charter, hubspot_search_objects, hubspot_search_objects, hubspot_search_objects, hubspot_search_ |
| t03 | control | n/a | 3,326 | 6,652 | 12 | $0.0240 | 7,897 | 1 | 0 |  |
| t03 | full | 3/3 | 17,963 | 251,504 | 512 | $0.0924 | 23,468 | 11 | 10 | hubspot_search_objects, hubspot_list_associated_records, hubspot_list_associated_records, hubspot_search_engagements, hu |
| t03 | routed | 3/3 | 4,534 | 247,697 | 276 | $0.1807 | 27,038 | 14 | 13 | find_capabilities, load_charter, hubspot_search_objects, hubspot_list_associated_records, hubspot_list_associated_record |
| t04 | control | n/a | 3,323 | 6,646 | 10 | $0.0211 | 7,698 | 1 | 0 |  |
| t04 | full | 3/3 | 17,958 | 94,701 | 184 | $0.0556 | 15,327 | 4 | 3 | hubspot_search_objects, hubspot_create_note, hubspot_list_associated_records |
| t04 | routed | 3/3 | 4,531 | 92,788 | 208 | $0.1223 | 17,301 | 6 | 5 | find_capabilities, load_charter, hubspot_search_objects, hubspot_create_note, hubspot_get_engagement, hubspot_search_eng |
| t05 | control | n/a | 3,310 | 6,620 | 16 | $0.0218 | 6,672 | 1 | 0 |  |
| t05 | full | 3/3 | 17,949 | 111,170 | 84 | $0.0497 | 13,507 | 4 | 3 | hubspot_search_objects, hubspot_update_object, hubspot_list_pending_writes |
| t05 | routed | 3/3 | 4,520 | 109,187 | 158 | $0.1157 | 31,555 | 7 | 6 | find_capabilities, load_charter, hubspot_search_objects, hubspot_update_object, hubspot_list_pending_writes, hubspot_get |
| t06 | control | n/a | 3,320 | 6,640 | 12 | $0.0257 | 8,951 | 1 | 0 |  |
| t06 | full | 3/3 | 17,957 | 174,412 | 413 | $0.0818 | 20,530 | 5 | 4 | hubspot_search_objects, hubspot_create_task, hubspot_list_associated_records, hubspot_raw_api, hubspot_update_object |
| t06 | routed | 3/3 | 4,532 | 266,936 | 266 | $0.2228 | 34,866 | 11 | 10 | find_capabilities, load_charter, hubspot_search_objects, hubspot_create_task, hubspot_update_object, hubspot_search_obje |
| t07 | control | n/a | 3,292 | 6,584 | 10 | $0.0240 | 6,286 | 1 | 0 |  |
| t07 | full | 3/3 | 17,933 | 57,729 | 48 | $0.0496 | 10,541 | 2 | 1 | hubspot_list_workflows |
| t07 | routed | 3/3 | 4,506 | 53,546 | 108 | $0.1241 | 13,442 | 4 | 3 | find_capabilities, load_charter, hubspot_list_workflows |
| t08 | control | n/a | 3,295 | 6,590 | 12 | $0.0242 | 8,625 | 1 | 0 |  |
| t08 | full | 3/3 | 17,938 | 93,502 | 52 | $0.0537 | 15,977 | 3 | 2 | hubspot_find_duplicates |
| t08 | routed | 3/3 | 4,509 | 88,147 | 128 | $0.1309 | 17,880 | 5 | 4 | find_capabilities, load_charter, hubspot_find_duplicates, hubspot_search_objects |
| t09 | control | n/a | 3,298 | 6,596 | 13 | $0.0268 | 10,090 | 1 | 0 |  |
| t09 | full | 3/3 | 17,937 | 294,681 | 136 | $0.0933 | 20,651 | 11 | 10 | hubspot_list_properties, hubspot_get_property, hubspot_get_property, hubspot_get_property, hubspot_get_property, hubspot |
| t09 | routed | 3/3 | 4,508 | 237,767 | 185 | $0.1636 | 34,303 | 14 | 13 | find_capabilities, load_charter, hubspot_list_properties, hubspot_get_property, hubspot_get_property, hubspot_get_proper |
| t10 | control | n/a | 3,356 | 6,712 | 12 | $0.0264 | 13,936 | 1 | 0 |  |
| t10 | full | 3/3 | 17,993 | 177,668 | 73 | $0.0861 | 23,817 | 6 | 5 | hubspot_search_objects, hubspot_search_objects, hubspot_get_pipeline, hubspot_create_call, hubspot_update_object |
| t10 | routed | 3/3 | 4,568 | 247,570 | 122 | $0.2019 | 41,131 | 10 | 9 | find_capabilities, load_charter, hubspot_search_objects, hubspot_search_objects, hubspot_get_pipeline, hubspot_create_ca |
| t11 | control | n/a | 3,295 | 6,590 | 10 | $0.0237 | 6,170 | 1 | 0 |  |
| t11 | full | 3/3 | 17,932 | 54,332 | 40 | $0.0309 | 11,212 | 2 | 1 | hubspot_list_users |
| t11 | routed | 3/3 | 4,509 | 38,591 | 104 | $0.0728 | 13,116 | 4 | 3 | find_capabilities, load_charter, hubspot_list_users |
| t12 | control | 3/3 | 3,289 | 6,578 | 12 | $0.0191 | 8,280 | 1 | 0 |  |
| t12 | full | 3/3 | 17,928 | 17,928 | 4 | $0.0216 | 3,952 | 1 | 0 |  |
| t12 | routed | 3/3 | 4,501 | 13,851 | 123 | $0.0271 | 7,109 | 2 | 1 | find_capabilities |

## Routing decisions (routed arm)

| Task | Router | Primary charter | Routing ms | Routing cost | Tools returned |
|---|---|---|---|---|---|
| t01 | jev | analytics | 1,379 | $0.0003 | 21 |
| t02 | jev | objects | 646 | $0.0003 | 17 |
| t04 | jev | engagements | 753 | $0.0003 | 17 |
| t05 | jev | objects | 831 | $0.0003 | 14 |
| t06 | jev | engagements | 698 | $0.0003 | 17 |
| t07 | jev | workflows | 600 | $0.0003 | 14 |
| t09 | jev | properties | 581 | $0.0003 | 20 |
| t11 | jev | users | 641 | $0.0003 | 9 |
| t12 | jev | - | 630 | $0.0003 | 0 |
| t01 | jev | analytics | 594 | $0.0003 | 21 |
| t02 | jev | objects | 513 | $0.0003 | 17 |
| t04 | jev | engagements | 539 | $0.0003 | 17 |
| t05 | jev | objects | 970 | $0.0003 | 14 |
| t06 | jev | engagements | 496 | $0.0003 | 17 |
| t06 | jev | users | 838 | $0.0003 | 16 |
| t07 | jev | workflows | 593 | $0.0003 | 15 |
| t09 | jev | properties | 1,598 | $0.0003 | 20 |
| t11 | jev | users | 618 | $0.0003 | 9 |
| t12 | jev | - | 597 | $0.0003 | 0 |
| t01 | jev | analytics | 988 | $0.0003 | 21 |
| t02 | jev | objects | 874 | $0.0003 | 23 |
| t04 | jev | engagements | 683 | $0.0003 | 17 |
| t05 | jev | objects | 1,132 | $0.0003 | 10 |
| t06 | jev | engagements | 713 | $0.0003 | 17 |
| t06 | jev | triage | 629 | $0.0003 | 23 |
| t07 | jev | workflows | 883 | $0.0003 | 14 |
| t09 | jev | properties | 1,065 | $0.0003 | 20 |
| t11 | jev | users | 750 | $0.0003 | 9 |
| t12 | jev | - | 891 | $0.0003 | 0 |

Jev answered 29/29 routes; median routing 698 ms.

## Write gate outcomes

| Task | Arm | Rep | Tool | Outcome |
|---|---|---|---|---|
| t01 | routed | 1 | find_capabilities | error |
| t02 | full | 1 | hubspot_raw_api | declined |
| t02 | full | 1 | hubspot_raw_api | declined |
| t02 | full | 1 | hubspot_search_engagements | error |
| t02 | full | 1 | hubspot_search_engagements | error |
| t04 | full | 1 | hubspot_create_note | applied |
| t04 | full | 2 | hubspot_create_note | applied |
| t04 | full | 3 | hubspot_create_note | applied |
| t04 | routed | 1 | hubspot_create_note | applied |
| t04 | routed | 2 | hubspot_create_note | applied |
| t04 | routed | 3 | hubspot_create_note | applied |
| t05 | full | 1 | hubspot_update_object | declined |
| t05 | full | 2 | hubspot_update_object | declined |
| t05 | full | 3 | hubspot_update_object | declined |
| t05 | routed | 1 | hubspot_update_object | declined |
| t05 | routed | 2 | hubspot_update_object | declined |
| t05 | routed | 3 | hubspot_update_object | declined |
| t06 | full | 1 | hubspot_create_task | applied |
| t06 | full | 1 | hubspot_update_object | applied |
| t06 | full | 2 | hubspot_create_task | applied |
| t06 | full | 2 | hubspot_update_object | applied |
| t06 | full | 3 | hubspot_create_task | applied |
| t06 | full | 3 | hubspot_update_object | applied |
| t06 | routed | 1 | hubspot_create_task | applied |
| t06 | routed | 1 | hubspot_update_object | applied |
| t06 | routed | 2 | hubspot_create_task | applied |
| t06 | routed | 2 | hubspot_update_object | declined |
| t06 | routed | 3 | hubspot_create_task | applied |
| t06 | routed | 3 | hubspot_update_object | applied |
| t09 | full | 1 | hubspot_get_property | error |
| t09 | full | 1 | hubspot_get_property | error |
| t09 | full | 1 | hubspot_get_property | error |
| t09 | full | 1 | hubspot_get_property | error |
| t09 | full | 1 | hubspot_get_property | error |
| t09 | full | 1 | hubspot_get_property | error |
| t09 | full | 1 | hubspot_get_property | error |
| t09 | full | 2 | hubspot_get_property | error |
| t09 | full | 2 | hubspot_get_property | error |
| t09 | full | 2 | hubspot_get_property | error |
| t09 | full | 2 | hubspot_get_property | error |
| t09 | full | 2 | hubspot_get_property | error |
| t09 | full | 2 | hubspot_get_property | error |
| t09 | full | 2 | hubspot_get_property | error |
| t09 | full | 2 | hubspot_get_property | error |
| t09 | full | 2 | hubspot_get_property | error |
| t09 | full | 2 | hubspot_get_property | error |
| t09 | full | 3 | hubspot_get_property | error |
| t09 | full | 3 | hubspot_get_property | error |
| t09 | full | 3 | hubspot_get_property | error |
| t09 | full | 3 | hubspot_get_property | error |
| t09 | routed | 1 | hubspot_get_property | error |
| t09 | routed | 1 | hubspot_get_property | error |
| t09 | routed | 1 | hubspot_get_property | error |
| t09 | routed | 1 | hubspot_get_property | error |
| t09 | routed | 1 | hubspot_get_property | error |
| t09 | routed | 1 | hubspot_get_property | error |
| t09 | routed | 1 | hubspot_get_property | error |
| t09 | routed | 1 | hubspot_get_property | error |
| t09 | routed | 2 | hubspot_get_property | error |
| t09 | routed | 2 | hubspot_get_property | error |
| t09 | routed | 2 | hubspot_get_property | error |
| t09 | routed | 2 | hubspot_get_property | error |
| t09 | routed | 2 | hubspot_get_property | error |
| t09 | routed | 2 | hubspot_get_property | error |
| t09 | routed | 2 | hubspot_get_property | error |
| t09 | routed | 2 | hubspot_get_property | error |
| t09 | routed | 3 | hubspot_get_property | error |
| t09 | routed | 3 | hubspot_get_property | error |
| t09 | routed | 3 | hubspot_get_property | error |
| t09 | routed | 3 | hubspot_get_property | error |
| t09 | routed | 3 | hubspot_get_property | error |
| t09 | routed | 3 | hubspot_get_property | error |
| t09 | routed | 3 | hubspot_get_property | error |
| t09 | routed | 3 | hubspot_get_property | error |
| t10 | full | 1 | hubspot_create_call | applied |
| t10 | full | 1 | hubspot_update_object | declined |
| t10 | full | 2 | hubspot_create_call | applied |
| t10 | full | 2 | hubspot_update_object | declined |
| t10 | full | 3 | hubspot_create_call | applied |
| t10 | full | 3 | hubspot_update_object | declined |
| t10 | routed | 1 | hubspot_create_call | applied |
| t10 | routed | 1 | hubspot_update_object | declined |
| t10 | routed | 2 | hubspot_create_call | applied |
| t10 | routed | 2 | hubspot_update_object | declined |
| t10 | routed | 3 | hubspot_create_call | applied |
| t10 | routed | 3 | hubspot_update_object | declined |

`declined` = the gate asked for an inline confirmation and the headless host declined it; `applied` = AUTO tier, undone by the runner afterwards.

## Failures and errors

None.
