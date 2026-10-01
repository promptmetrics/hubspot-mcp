---
name: pipeline-pulse
description: >
  Quick pipeline snapshot — deals by stage, activity age, next step status,
  and closing timeline. Surfaces deals with no next step, highlights stalled
  deals, and proactively generates follow-up tasks you can add to HubSpot by
  replying.

  ALWAYS use this skill for any pipeline request — "how's my pipeline",
  "show me my pipeline", "show me my deals", "pipeline overview", "pipeline
  status", "which deals are stalling", "deal overview", "pipeline review",
  "weekly pipeline", "pipeline health", "clean up my pipeline", "audit my
  deals". This skill handles both quick snapshots and deeper deal reviews.
---

# Pipeline Pulse

> **Tool names** below are expected names for the HubSpot MCP connector. If a named tool isn't available, check for updates to the skill and/or alternates in the HubSpot toolset as available tools may change

## Phase 1 — Fetch data (parallel)

**A. Deals** — `search_crm_objects`:
- objectType: deals
- filter: dealstage not in [closedwon, closedlost]
- properties: dealname, dealstage, amount, closedate, pipeline, hs_lastmodifieddate, hs_next_step, hubspot_owner_id
- limit: 50

**B. Portal context** — `get_organization_details` → store portalId, uiDomain

**C. Tasks** — `search_crm_objects`:
- objectType: tasks
- filter: hs_task_status = NOT_STARTED, associations.deal IN [dealIds from A]
- properties: hs_task_subject, hs_task_status, hs_timestamp, associations
- Identifies which deals already have an open task

---

## Phase 2 — Enrich with contacts and connector signals

**A.** Batch-call `get_crm_objects` contacts for each deal's associated contactId → firstname, lastname, email. Store `contact` (display name) and `contactEmail` per deal.

**B.** For deals where `hs_lastmodifieddate` is 21+ days ago or `hs_next_step` is empty, silently check available connectors:
- **Calendar**: upcoming meetings with associated contact email
- **Email**: recent unread threads from associated contact email

Store per deal: `upcomingMeeting` (date string), `unrepliedEmail` (subject). Skip if connectors unavailable.

---

## Phase 3 — Classify each deal

For each deal compute:
- `lastModified`: `hs_lastmodifieddate`
- `daysAgo`: today − lastModified
- `daysToClose`: closeDate − today (negative = overdue)
- `stalled`: daysAgo ≥ 21
- `overdue`: closeDate has passed
- `closingSoon`: closes within 14 days
- `hasNextStep`: hs_next_step is non-empty
- `hasOpenTask`: deal appears in Phase 1C results
- `needsAttention`: stalled OR overdue OR no next step

---

## Phase 4 — Generate suggested tasks

For deals where `hasOpenTask` is false, generate a suggested follow-up task. Do not ask — surface them proactively in the output.

**Task generation rules:**

| Deal state | Suggested task subject | Due |
|---|---|---|
| Stalled + upcoming meeting | "Prep call with [Contact] — [Deal Name]" | Day of meeting |
| Stalled + unreplied email | "Reply to [Contact] re: [Deal Name]" | Today |
| Stalled, no signals | "Re-engage [Contact] on [Deal Name]" | Today |
| No next step, closing ≤ 14d | "Set next step — [Deal Name] closing [date]" | Today |
| No next step, any stage | "Follow up with [Contact] — [Deal Name]" | Today |

Each suggested task object: `{ id, subject, context, due, dealId, contactId, dealName, contactName }`

---

## Phase 5 — Format the pulse

Output as plain text in this structure. Group deals by attention status: needs-attention deals first, then healthy. Omit sections with no items.

```
PIPELINE PULSE — [Day, Month D]
[N] open deals · [total value] · [N] need attention

━━ NEEDS ATTENTION
• [Deal name] with [Contact] — [stage] · [amount] · closes [date]
  [stalled X days / overdue / no next step] · [upcoming meeting or unreplied email if any]
  Next step: [hs_next_step or "—"]

━━ ON TRACK
• [Deal name] with [Contact] — [stage] · [amount] · closes [date]
  Next step: [hs_next_step]

━━ SUGGESTED TASKS
• [Task subject] — [signal context]
• [Task subject] — [signal context]

Reply "add all" to create all suggested tasks in HubSpot, or name specific ones.
Say "call prep for [name]" or "follow up with [name]" to act on a deal.
```

Link each deal and contact name to their HubSpot record using `portalId` and `uiDomain`.

---

## Phase 6 — Add suggested tasks to HubSpot

When the user replies "add all" or names specific tasks, for each selected task call `manage_crm_objects` createRequest, objectType: tasks:
- `hs_task_subject`: task subject
- `hs_task_type`: CALL or EMAIL (infer — "Reply" → EMAIL, others → CALL)
- `hs_task_status`: NOT_STARTED
- `hs_task_priority`: HIGH if deal closing ≤ 14d, else MEDIUM
- `hs_timestamp`: task due date ISO
- `hs_task_body`: signal context
- associations: deal → dealId, contact → contactId

Run creates in parallel. Confirm: "Created [N] tasks in HubSpot."

**Add next step**: when the user says "add next step to [Deal Name]", update via `manage_crm_objects` deals, updateRequest: `hs_next_step`.

---

## Error handling

- **No open deals**: "No open deals right now. Want me to add one?"
- **Deal fetch fails**: Report the error and suggest checking the HubSpot connection.
- **Connectors unavailable**: Run with HubSpot data only — omit connector-based signals silently.
