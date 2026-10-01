---
name: log-call
description: >
  Logs a call, meeting, or conversation against a HubSpot contact. Writes a
  note, updates deal stage, next step, close date, and priority, updates
  contact lead status and lifecycle stage, and creates a follow-up task.

  ALWAYS use this skill when the user reports on a completed interaction —
  "just got off the phone with X", "had a call with X", "just spoke to X",
  "log a call with X", "post-call notes for X", "I just finished a meeting
  with X", "debrief from my call with X", "I spoke to X", "update after my
  call with X". Trigger on any past-tense account of a conversation or
  meeting with a specific contact, or an explicit request to log an outcome.
---

# Log Call

> **Tool names** below are expected names for the HubSpot MCP connector. If a named tool isn't available, check for updates to the skill and/or alternates in the HubSpot toolset as available tools may change

## Phase 1 — Parse the message

Extract from user input:
- **Contact name or email**
- **Summary** — what was discussed
- **Outcome** — any decision or result
- **Next step** — any specific follow-up action mentioned
- **Deal signals** — stage movement, timeline change, priority shift, deal size revision
- **Contact status signals** — did you reach them? Are they engaged? Bad timing?

If contact name is missing, ask: "Who was the call with?"

---

## Phase 2 — Find the contact and deal

Run simultaneously:
- `get_organization_details` → store `portalId`, `uiDomain`
- `search_crm_objects` contacts, query: name or email, properties: firstname, lastname, email, jobtitle, associatedcompanyid, hs_lead_status, lifecyclestage

If multiple contacts match, show disambiguation list (name, title, company) and wait for confirmation.

Then search deals filtered by associations.contact = contactId, properties: dealname, dealstage, pipeline, amount, closedate, hs_next_step, hs_priority. Store `dealId`, `currentStage`, `dealPipeline`, `dealName`, `currentNextStep`.

---

## Phase 3 — Build the update plan

Based on what was said, propose updates across note, deal, and contact.

**Stage resolution** — if stage signals were detected, resolve before building the plan. This is fail-closed: if resolution fails at any step, omit the stage change entirely and ask the user.
- If the deal has no `pipeline` value, omit the stage change and ask: "I couldn't determine the pipeline for this deal — which stage should I move it to?"
- Call `get_properties`: objectType: deals, propertyNames: ["dealstage", "pipeline"]. If this call fails or returns no data, omit the stage change — do not proceed.
- Filter `dealstage` options to only those belonging to `dealPipeline`. If no stages match, omit the stage change and ask the user.
- Sort matching stages by `displayOrder`. Match the user's words against the actual stage labels.
- If no stage label clearly fits, or more than one is plausible, omit the stage change and ask — never guess.
- Only when a single unambiguous match is found: store `proposedStageLabel` (for the confirm block) and `proposedStageValue` (API value for Phase 5).

Show a single confirm block — do not write until confirmed.

```
Ready to log this call with [First Last]:

📝 Note: [2–3 sentence summary with outcome and next step]

Deal — "[Deal Name]":
  Stage:     [current] → [proposedStageLabel]       (if mentioned)
  Next step: "[what was agreed]"           (always if deal exists)
  Close date: [current] → [revised date]  (if timeline discussed)
  Priority:  [LOW / MEDIUM / HIGH]         (if urgency was clear)
  Amount:    [current] → [revised]         (if deal size discussed)

Contact — [First Last]:
  Lead status: [current] → [new status]    (e.g. CONNECTED, BAD_TIMING)
  Lifecycle:   [current] → [new stage]     (if they're now an opportunity)

Task: "[Follow up with First — next step text]" due [date] · type: CALL · priority: [MEDIUM/HIGH]

Log it?
```

Omit lines that don't apply. If no deal exists, omit the Deal section. Always include the task line.

---

## Phase 4 — Write the note

`manage_crm_objects` notes, createRequest:
- `hs_note_body`: `📞 Call logged — [today's date]\n\n[Summary]\n\n[Outcome]\n[Next step]`
- `hs_timestamp`: current ISO 8601 timestamp
- associations: contact → contactId, deal → dealId (if present)

---

## Phase 5 — Update deal and contact fields

Run Phase 5 (deal) and Phase 6 (contact) simultaneously — they are independent writes.

If a deal exists, update via `manage_crm_objects` deals, updateRequest, objectId: dealId. Run all field changes in a single call.

**Fields to update (only those that changed):**

| Signal | Field | Value |
|---|---|---|
| Stage movement | `dealstage` | `proposedStageValue` resolved in Phase 3 — omit entirely if Phase 3 did not produce a resolved value |
| Next step agreed | `hs_next_step` | Free text — always write if a next step was mentioned |
| Timeline revised | `closedate` | ISO 8601 date |
| Priority signal | `hs_priority` | low · medium · high |
| Deal size discussed | `amount` | Number |

If stage mapping is ambiguous, ask before updating — never guess.

---

## Phase 6 — Update contact fields

If contact status signals were present, update via `manage_crm_objects` contacts, updateRequest, objectId: contactId.

| Signal | Field | Value |
|---|---|---|
| Reached them, had a real conversation | `hs_lead_status` | CONNECTED |
| Actively progressing | `hs_lead_status` | IN_PROGRESS |
| Voicemail / no answer | `hs_lead_status` | ATTEMPTED_TO_CONTACT |
| They said call back later | `hs_lead_status` | BAD_TIMING |
| Not a fit | `hs_lead_status` | UNQUALIFIED |
| Now has an open deal | `hs_lead_status` | OPEN_DEAL |
| Qualifies as a sales opportunity | `lifecyclestage` | opportunity |
| Decision to buy likely | `lifecyclestage` | salesqualifiedlead |

Only update if the signal is clear. Skip if ambiguous.

---

## Phase 7 — Create follow-up task

Always create a task. Pre-fill from the next step if mentioned; default to "Follow up with [firstName]" in 7 days.

`manage_crm_objects` tasks, createRequest:
- `hs_task_subject`: "Follow up with [firstName] — [next step if mentioned]"
- `hs_task_type`: CALL
- `hs_task_status`: NOT_STARTED
- `hs_task_priority`: MEDIUM (or HIGH if deal is closing soon / urgent)
- `hs_timestamp`: [due date ISO]
- `hs_task_body`: [brief context from call]
- associations: contact → contactId, deal → dealId (if present)

---

## Phase 8 — Confirm

```
Logged.
  ✓ Note saved on [First Last]
  ✓ Deal "[Name]" — [list of updated fields]
  ✓ Contact status → [new status if updated]
  ✓ Task created — due [date]

View contact: https://[uiDomain]/contacts/[portalId]/contact/[contactId]
```

---

## Error handling

- **Contact not found**: Ask to clarify. Do not write anything until confirmed.
- **Multiple matches**: Show disambiguation list, wait.
- **No deal found**: Log the note and create the task. Skip deal update phases.
- **Stage mapping unclear**: Ask before updating — never guess.
- **Note save fails**: Show the composed note in chat so nothing is lost.
- **Partial failure**: Report which updates succeeded and which failed. Offer to retry failed ones.
