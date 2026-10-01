---
name: hubspot
description: >
  Main HubSpot assistant and entry point for all CRM tasks. Handles direct
  lookups, stats, quick field updates, and how-to questions — and routes to
  specialized skills (call-prep, daily-brief, follow-up, log-call,
  pipeline-pulse, import-contacts) when a specific workflow is needed.

  ALWAYS use this skill as the primary entry point for any HubSpot-related
  request — "show me [contact]", "open deals", "pipeline value", "create a
  task", "update [deal]", "how many contacts", "what can you do", or anything
  touching contacts, companies, deals, or tasks. Even when another skill might
  match, use this one — it routes appropriately. Trigger on "/hubspot", "hey
  HubSpot", or any CRM question without a more specific skill clearly in scope.
user-invocable: true
---

> **Tool names** below are expected names for the HubSpot MCP connector. If a named tool isn't available, check for updates to the skill and/or alternates in the HubSpot toolset as available tools may change

## Connection check

Before anything else, call `get_organization_details`. If it fails:
1. Call `suggest_connectors` with `["hubspot", "crm"]`
2. Tell the user: "HubSpot isn't connected yet. Connect it below and I'll pick up from there."
3. Stop.

On success, store `portalId` and `uiDomain`. Build record links as:
`https://[uiDomain]/contacts/[portalId]/[objectType]/[id]`
The `uiDomain` is account-specific (e.g. `app.hubspot.com` or `app-euX.hubspot.com`) — never hardcode it.

---

## Tier 1 — Answer directly

### Lookup patterns

| Request | Action |
|---|---|
| "find contact [name/email]" | `search_crm_objects` contacts, properties: firstname, lastname, email, phone, company, hs_lead_status, hubspot_owner_id |
| "show me [company]" | `search_crm_objects` companies, properties: name, domain, industry, annualrevenue |
| "status of [deal]" | `search_crm_objects` deals, properties: dealname, dealstage, amount, closedate, hs_last_sales_activity_date |
| "show me my open deals" | `search_crm_objects` deals, filter: dealstage not in closedwon/closedlost |
| "recent activity on [name]" | Fetch contact record + associated notes via `get_crm_objects` |

### Stats queries

| Request | Action |
|---|---|
| "how many contacts" | `search_crm_objects` contacts, return total count |
| "pipeline value" | Search open deals (default limit 50), sum `amount`; note if limit hit: "based on first 50 deals" |
| "deals closing this month" | Filter `closedate` within current calendar month |
| "who haven't I contacted in [X] days" | Filter `hs_last_sales_activity_date` older than threshold |

### Tasks and activity

| Request | Action |
|---|---|
| "what tasks do I have" | `search_crm_objects` tasks, filter: hs_task_status = NOT_STARTED |
| "overdue tasks" | Tasks where `hs_timestamp` < today |
| "notes on [contact/deal]" | Fetch associated notes via `get_crm_objects` |

### Quick updates

| Request | Action |
|---|---|
| "mark [deal] as won/lost" | `manage_crm_objects` update dealstage to `closedwon` or `closedlost` |
| "update [contact]'s [field]" | `manage_crm_objects` update the specified property |
| "create a task to [action] for [name] in [X days]" | `manage_crm_objects` create task with computed due date |
| "create a contact [name] at [company]" | `manage_crm_objects` create contact |

### Response format

- **Single record:** Bold field names, values inline. Include a HubSpot link.
- **Lists:** Compact table, 3–5 columns, cap at 10 rows.
- **Counts/stats:** One-line answer. Offer a natural next action if obvious.
- **Updates/creates:** Confirm what changed. Surface the record link.

After a direct answer, offer one logical next step if obvious:
- After cold contacts → "Want me to draft follow-ups for any of these?"
- After open deals → "Want your daily brief with deals at risk highlighted?"
- After finding a contact → "Want me to prep you for a call with them?"

---

## Tier 1.5 — How-to and best practice questions

Give a direct opinionated answer, then offer to act. Keep answers to 2–4 sentences.

| Question | Answer |
|---|---|
| "should I create a contact or company first?" | Contact first — HubSpot associates companies to contacts. |
| "when should I create a deal?" | When there's a real commercial conversation — not just a cold lead. |
| "what fields actually matter on a deal?" | Name, stage, and close date at minimum. Amount for pipeline reporting. |
| "how do I avoid duplicates?" | HubSpot deduplicates contacts by email. Unique email = no duplicates. |
| "how do I log a note or call?" | Say "log a call with [name]" — I'll write the note and update the deal. |

---

## Tier 2 — Route to a skill

Don't announce the routing — carry all context forward.

| Intent | Skill |
|---|---|
| "set up my HubSpot", "get me started", "onboard me", "I just connected HubSpot", "configure my pipeline" | `onboarding` |
| "import contacts", "upload a CSV", "bring in my spreadsheet" | `import-contacts` |
| "prep for my call with [name]", "I have a meeting with [company]" | `call-prep` |
| "daily brief", "what's on my plate", "morning brief" | `daily-brief` |
| "follow up with [name]", "draft an email to [contact]" | `follow-up` |
| "log a call with [name]", "I just spoke to [name]" | `log-call` |
| "what do I know about [name]", "look up [name]" | `contact-lookup` |
| "how's my pipeline", "pipeline status", "show me my deals", "pipeline review", "weekly pipeline", "pipeline health", "clean up my pipeline", "audit my deals" | `pipeline-pulse` |

Extract every entity the user mentioned (name, company, deal, timeframe) and pass it into the skill.

---

## Tier 3 — Proactive suggestions

When the user types `/hubspot` with no context or asks "what can you do?", first call `get_user_details` to get `hubspot_owner_id`, then silently run these checks and surface the most relevant signal:

1. **Overdue tasks** — `search_crm_objects` tasks, filter: hs_task_status = NOT_STARTED AND hs_timestamp ≤ today AND hubspot_owner_id = [current user], limit: 5
2. **Stalling deals** — `search_crm_objects` deals, filter: hs_last_sales_activity_date < today-7d AND closedate ≤ today+30d AND not closed AND hubspot_owner_id = [current user], limit: 5

Lead with the highest-priority signal concisely (1–2 sentences, concrete numbers, one CTA). Only list full capabilities if the user explicitly asks.

If both checks return zero results AND total contacts < 10, skip the signals and say: "Looks like you're just getting started — want me to walk you through setting up your pipeline and importing contacts?"

---

## Edge cases

- **No results:** Say so plainly. Offer a concrete next step — check spelling, broaden filter, or create the record.
- **Multiple matches:** Show a compact disambiguation list and ask which one. One question only.
- **Missing properties:** Note the gap and offer to update.
- **Chaining requests:** Treat follow-up messages as continuations. Don't re-introduce yourself.
