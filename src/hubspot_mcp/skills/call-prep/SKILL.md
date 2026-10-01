---
name: call-prep
description: >
  Prepares a focused brief before a sales call or meeting. Pulls CRM history,
  open deals, recent notes, email threads, and company news. Falls back to
  calendar and email if the contact isn't in HubSpot yet.

  ALWAYS use this skill before a call or meeting — "prep me for my call with X",
  "I have a meeting with [company]", "brief me on [name]", "what do I know about
  X before my call", "research X", "who am I meeting with today", "prepping for
  X", or just a name paired with "call" or "meeting". Trigger proactively when
  the user seems about to get on a call and hasn't run prep.
---

# Call Prep

> **Tool names** below are expected names for the HubSpot MCP connector. If a named tool isn't available, check for updates to the skill and/or alternates in the HubSpot toolset as available tools may change

## Phase 0 — Check for a name

If the user gave no name or company at all, ask only:

> "Who's the call with?"

Do not ask for anything else yet. Once you have a name, proceed.

---

## Phase 1 — Find the contact

Run simultaneously:
- `get_organization_details` → store `portalId`, `uiDomain`
- `search_crm_objects`: objectType: contacts, query: name or email, properties: firstname, lastname, email, jobtitle, hs_lead_status, associatedcompanyid, notes_last_contacted, hs_email_last_send_date, createdate

Multiple matches → show disambiguation list, wait for confirmation. Store `contactId`, `companyId`. Then search deals filtered by associations.contact = contactId → store `dealId`.

**If not found in HubSpot**, search available connectors simultaneously:
- **Calendar**: search upcoming events for the name. Extract email, company from attendee/organiser fields.
- **Email**: search threads for the name. Extract email, company from domain or signature.

If found in a connector, store as `externalContact: true`. Proceed with available data — Phase 2A will be skipped.

If no connector finds the contact either, stop: "I couldn't find [name] in HubSpot or your connected apps. Double-check the name or add them to HubSpot first."

---

## Phase 2 — Parallel research

Run simultaneously:

### A. CRM history (skip if `externalContact: true`)
`get_crm_objects` contacts, objectId: contactId, associations: deals, notes, tasks. Retrieve:
- **Open deals**: stage, amount, close date, deal name
- **Recent notes**: last 5 by hs_timestamp descending
- **Open tasks**: hs_task_status = NOT_STARTED or IN_PROGRESS
- **Timeline signals**: notes_last_contacted, hs_email_last_send_date, createdate

### B. Email history (if email connector available)
Search threads for contact's email, last 3–5. Note: last reply direction, tone, unanswered questions, subject context.

### C. Calendar (if calendar connector available)
Find meeting in next 7 days with contact's email as attendee. Extract: time, attendees, agenda.

### D. Company research (web)
Using an available web search tool, search `"[company name] news [current year]"` and `"[company name]"`. Capture 3–4 concrete findings. Skip silently if no web search tool is available.

---

## Phase 3 — Fill context gaps (if needed)

After research, check what's missing from the user's original message:

| Missing | Ask |
|---|---|
| Call type | "What type of call is this — discovery, demo, follow-up, QBR?" |
| Purpose / what they want to cover | "What are you trying to cover on this call?" |
| Desired outcome | "What would a good outcome look like?" |

Ask only for what's genuinely missing and useful for the brief. If CRM context makes the call type obvious (e.g. deal is at Demo stage), infer it — don't ask. If research is rich enough to write a strong brief, skip this phase entirely and proceed.

If you do ask, do it in one message, conversationally — not as a form. Reference what you found:

> "Found [Name] — they're at [stage] with an open deal worth [amount], last contacted [X days ago]. Before I write up the brief:
> - What type of call is this?
> - What are you hoping to walk away with?"

Store responses as: `callType`, `purpose`, `desiredOutcome`.

---

## Phase 4 — Analyse

| Signal | Sentiment |
|---|---|
| Recent reply, deal advancing, active engagement | Positive |
| Some activity, no clear momentum | Neutral |
| No reply 3+ weeks, deal stuck 30+ days, disengaged tone | At-risk |

**Blockers** — concrete deal-stoppers from notes, tasks, email  
**Hurdles** — softer friction (evaluation pending, champion change, timing)  
**Deal context** — stage, value, close date, next committed action

---

## Phase 5 — Format the brief

Tailor talking points and next step to `callType` and `desiredOutcome` if provided.

```
## Call Prep: [First Last] @ [Company]
[Date] · [Call type] · Goal: [desiredOutcome]

### Quick snapshot
- Role: [Job title]
- Deal: [Deal name] · [Stage] · [Value] · closes [Close date]
- Sentiment: [Positive / Neutral / At-risk] — [one sentence reason]
- Last contact: [X days ago via email/call/meeting — topic]

### Open items
- [Blocker or outstanding question]
- [Any commitment not yet closed out]

### Company context
- [Specific news item — source and date]
- [Second relevant finding]

### Talking points
1. [Personal opener — reference a specific email, news item, or note]
2. [Address the main blocker or open item]
3. [Named next step with a timeframe]

### Suggested next step
[One specific actionable close — tied to desiredOutcome]
```

Omit sections cleanly if data is missing — no placeholders.

---

## Phase 6 — Save to HubSpot

Skip if `externalContact: true` — go to Phase 7.

`manage_crm_objects`: objectType: notes, createRequest:
- properties: hs_note_body: [full brief text], hs_timestamp: [current ISO timestamp]
- associations: contact → contactId, deal → dealId (if present)

Confirm: "Call prep saved. View contact: https://[uiDomain]/contacts/[portalId]/contact/[contactId]"

---

## Phase 7 — HubSpot sync CTA (external contacts only)

If `externalContact: true`, after the brief:

> **[Name] isn't in HubSpot yet.** Want me to add them? I'll create a contact with their name, email, and company.
> `Yes, add to HubSpot` · `Not now`

On "Yes": `manage_crm_objects` createRequest, objectType: contacts, with fields from Phase 1. Confirm with a link to the new record.

---

## Error handling

- **No name given**: Phase 0 — ask only for who before doing anything.
- **Contact not found anywhere**: Stop with a clear message.
- **Found externally, connectors partial**: Build from available data, note skipped sources.
- **No deal**: Omit deal fields, note "No open deal on record".
- **No email history**: Base sentiment on CRM activity and notes only.
- **Note save fails**: Show the brief in chat and report the error.
