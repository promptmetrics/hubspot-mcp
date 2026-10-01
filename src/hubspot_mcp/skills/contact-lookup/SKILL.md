---
name: contact-lookup
description: >
  Pulls a full view of a contact from HubSpot — deal status, lifecycle stage,
  last interaction, notes, tasks, and associated company. Returns a clean
  summary in chat.

  ALWAYS use this skill when the user asks about a specific person by name —
  "pull up X", "show me X's record", "what's happening with X", "check X",
  "who is X", "tell me about X", "what's X's status", "when did we last contact
  X", "look up X". Also trigger when the user pastes a contact email address
  wanting context on that person.
---

# Contact Lookup

> **Tool names** below are expected names for the HubSpot MCP connector. If a named tool isn't available, check for updates to the skill and/or alternates in the HubSpot toolset as available tools may change

## Phase 1 — Find the contact

Run simultaneously:
- `get_organization_details` → store `portalId`, `uiDomain`
- `search_crm_objects`: objectType: contacts, query: name or email from user message, properties: firstname, lastname, email, jobtitle, phone, associatedcompanyid, hs_lead_status, lifecyclestage, notes_last_contacted, hs_email_last_send_date, createdate

If multiple contacts match, show a short disambiguation list (name, title, company) and wait for confirmation.

## Phase 2 — Parallel enrichment

Run simultaneously:

**Deals** — `search_crm_objects` deals, filter: associations.contact = contactId, properties: dealname, dealstage, amount, closedate, hs_deal_stage_probability

**Notes** — `get_crm_objects` notes associated with contactId, properties: hs_note_body, hs_timestamp — last 3, descending

**Tasks** — `get_crm_objects` tasks associated with contactId, properties: hs_task_subject, hs_task_status, hs_timestamp, filter: status = NOT_STARTED or IN_PROGRESS

**Company** (if companyId exists) — `get_crm_objects` companies, objectId: companyId, properties: name, domain, industry, numberofemployees, city, country

## Phase 3 — Format the snapshot

```
[First Last] — [Job Title] at [Company]
[email] · [phone if present]
Lifecycle: [lifecyclestage] · Lead status: [hs_lead_status]
Added: [createdate as "MMM D, YYYY"]

DEALS  (omit if none; list each if multiple)
• [Deal name] · [Stage] · [Amount] · closes [Close date]

LAST CONTACT
[X days ago via email/note] on [date]
[One sentence summary from most recent note]

NOTES (last 3)
• [Date]: [Key line from note]

OPEN TASKS
• [Task subject] — due [date]
```

- Extract the key sentence from a note — don't truncate.
- If last contact > 60 days ago, flag: "⚠️ Last contact [X] days ago"
- Omit empty sections entirely — no "N/A" or placeholder text.

End with:
```
https://[uiDomain]/contacts/[portalId]/contact/[contactId]
```

## Error handling

- **Not found**: Offer to search by email or create a new contact.
- **Multiple matches**: Show disambiguation list, wait for confirmation.
