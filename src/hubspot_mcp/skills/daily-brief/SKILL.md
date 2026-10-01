---
name: daily-brief
description: >
  Generates a prioritised daily brief from HubSpot data plus any connected apps
  (calendar, email, Slack, meeting notes). Shows today's meetings, surfaces GTM
  tasks with effort-aware scheduling, deduplicates suggestions against existing
  HubSpot tasks, and outputs as readable markdown.

  ALWAYS use this skill when the user wants their day laid out — "good morning",
  "daily brief", "morning brief", "what do I need to do today", "what's on my
  plate", "what should I focus on", "catch me up", "today's priorities",
  "what's urgent", "what's due today", "show me my day", or any morning planning
  request. Trigger proactively when a user opens Claude first thing with no
  specific ask.
---

# Daily Brief

> **Tool names** below are expected names for the HubSpot MCP connector. If a named tool isn't available, check for updates to the skill and/or alternates in the HubSpot toolset as available tools may change

Surfaces GTM tasks and meetings, flags at-risk contacts and deals, and **suggests** new tasks — never creates tasks automatically.

---

## Phase 1 — Fetch HubSpot data (required)

If `get_organization_details` fails, stop: "I couldn't reach your HubSpot account. Make sure the HubSpot connector is connected, then try again."

**A. Portal + owner** — `get_organization_details` and `get_user_details` in parallel. Store `portalId`, `uiDomain`, `userId`, `hubspot_owner_id`.

**B. Existing open tasks** — `search_crm_objects`:
- objectType: tasks
- filter: hs_task_status = NOT_STARTED AND hs_timestamp ≤ today+5d
- properties: hs_task_subject, hs_task_body, hs_timestamp, hs_task_status, hs_task_type, hubspot_owner_id, hs_object_id
- limit: 25

**C. Cold contacts** — `search_crm_objects`:
- objectType: contacts
- filter: hs_last_sales_activity_date < today-14d AND has associated open deal
- properties: firstname, lastname, email, hs_last_sales_activity_date, hubspot_owner_id, hs_object_id
- limit: 5

**D. Stalling deals** — `search_crm_objects`:
- objectType: deals
- filter: hs_last_sales_activity_date < today-7d AND closedate ≤ today+30d AND not closedwon/closedlost
- properties: dealname, amount, closedate, dealstage, hs_last_sales_activity_date, hubspot_owner_id, hs_object_id
- limit: 5

Run A–D simultaneously. After results return, filter B–D to the current user's `hubspot_owner_id`.

---

## Phase 2 — Fetch from available connectors

Silently attempt each available connector simultaneously. Skip silently if absent or failing.

| Connector | What to fetch |
|---|---|
| Calendar | Today's events. Store title, start time, end time, attendee names. |
| Email (e.g. Gmail) | Unread threads from known contacts with open deals, up to 10. Store sender, subject, thread ID. |
| Slack | Recent @mentions in last 24h related to customers or deals, up to 5. |
| Meeting notes (Granola, Fellow, Gong, etc.) | Recent customer meeting summaries with action items. |
| Project management (Asana, Linear, Jira) | Tasks referencing customer names or deal-related work, overdue or due today. |

Skip any connector data that is clearly non-customer-facing (e.g. internal team syncs, admin tasks).

---

## Phase 3 — Filter and classify GTM tasks

### 3A — GTM filter

From Phase 1B, keep only tasks that are sales or customer-facing. A task qualifies if any of:
- `hs_task_type` is CALL, EMAIL, or MEETING
- Subject contains: follow up, reach out, call, email, demo, proposal, contract, outreach, pitch, meeting, customer, client, prospect
- Task body references a contact or company name

Drop internal tasks (e.g. "Update spreadsheet", "Team sync", "Review internal doc" with no customer context).

### 3B — Effort classification

Assign each GTM task an effort level and visibility window:

| Effort | Examples | Show if due within |
|---|---|---|
| Quick (<1hr) | Reply email, check-in call, send update | 1 day (today + overdue) |
| Half-day | Demo prep, discovery prep, deck review | 2 days |
| Multi-day | Proposal, RFP, contract draft, onboarding plan | 5 days |

Infer effort from task subject/type. Default to Quick if unclear.

### 3C — Urgency classification

- **Overdue** — hs_timestamp before today
- **Due today** — hs_timestamp is today
- **Upcoming** — within effort window, after today

Surface overdue + due today first, then upcoming tasks within their effort window. Hide anything beyond its window.

---

## Phase 4 — Suggest new GTM tasks (deduplicated)

Build a `coveredSet` from Phase 3 tasks: extract all contact and deal names referenced in existing task subjects and bodies. Any suggestion that would cover the same contact or deal is a duplicate — skip it.

From Phases 1–2 (minus covered), generate GTM suggestions only:

| Signal | Suggestion | Effort | Due |
|---|---|---|---|
| Cold contact (14+ days no activity, open deal) | "Follow up with [Name]" | Quick | Today |
| Stalling deal (7+ days no activity, close ≤ 30d) | "Re-engage [Deal Name]" | Quick | Today |
| Unread email from contact with open deal | "Reply to [Name] re: [subject]" | Quick | Today |
| Meeting today with prospect/customer | "Prep for meeting with [Name]" | Half-day | 1h before meeting |
| Meeting notes with open action items | "Follow up on action items — [Meeting]" | Quick | Today |
| Proposal/contract discussed in recent meeting | "Draft proposal for [Company]" | Multi-day | Today (start now) |

Do not suggest anything internal, team-facing, or non-customer.

Each suggestion must include: hs_task_subject (verb-led, max 80 chars), hs_task_body (signal sentence), hs_timestamp (due date), hs_task_status: NOT_STARTED, hubspot_owner_id, associated record.

---

## Phase 5 — Output the brief as markdown

Render the brief as structured markdown in this order. Omit any section that has no items — no placeholders.

```
## Daily Brief — [Weekday, Month D]

### Meetings
- **[HH:MM AM/PM]** — [Title] · [Attendee, Attendee]

### Tasks
**Overdue**
- [Task subject] *([type] · [N days overdue])*

**Due today**
- [Task subject] *([type])*

**Upcoming**
- [Task subject] *([type] · due [date])*

### Signals
**Cold contacts** (14+ days no activity, open deal)
- [First Last] — last contact [N days] ago · [Deal name]

**Stalling deals** (7+ days no activity, closing ≤30d)
- [Deal name] — [stage] · closes [date] · [N days] since last touch

### Suggested tasks
- **[Verb-led subject]** — [signal context] · *[Quick / Half-day / Multi-day]*

Reply "add all" to create all suggested tasks in HubSpot, or name specific ones.
```

Link each contact and deal name to their HubSpot record: `[Name](https://[uiDomain]/contacts/[portalId]/contact/[id])`.

---

## Phase 6 — Offer to schedule the brief

After the brief, offer to set up a recurring morning brief in one line:

> "Say 'schedule it' to get this every morning — what time works best?"

If the user agrees, ask for their preferred time if they haven't provided one. Create a scheduled task at that time with prompt: "Run the daily brief skill. Output as markdown. Fetch from all available connectors. Surface existing GTM tasks and suggest new ones — do not auto-create any tasks." Confirm: "Done — you'll get your brief every morning at [time]."

---

## Phase 7 — Add suggested tasks to HubSpot

When the user replies "add all" or names specific tasks:

For each selected task, call `manage_crm_objects` createRequest, objectType: tasks — hs_task_subject, hs_task_body, hs_timestamp, hs_task_status: NOT_STARTED, hubspot_owner_id. Run all creates in parallel.

**Completing tasks**: if the user asks to mark a task complete, call `manage_crm_objects` update, hs_task_status: COMPLETED. Confirm: "Marked complete."

Confirm adds: "Added [N] tasks to HubSpot."

---

## Edge cases

- **No existing tasks**: omit Tasks section; still show Signals and Suggested Tasks if signals exist.
- **HubSpot only**: skip Phase 2 connectors; render with HubSpot-only data.
- **Owner ID unavailable**: skip owner filtering; note "Showing all records — owner filter unavailable."
- **All sections empty**: "You're all caught up — no overdue tasks, cold contacts, or stalling deals right now."
