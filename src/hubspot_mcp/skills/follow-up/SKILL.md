---
name: follow-up
description: >
  Drafts a personalised follow-up or outreach email for a named contact,
  grounded in HubSpot deal context, recent notes, and email history. Opens
  HubSpot with the email pre-populated so the rep can review and send with
  full tracking. If deal-related, updates the next step or deal stage as applicable.

  ALWAYS use this skill when the user wants to write or send outbound
  communication to a specific contact — "follow up with X", "draft an email
  to X", "send something to X", "check in with X", "reach out to X", "ping X",
  "touch base with X", "I need to reply to X", "draft a follow-up for [deal]",
  "write to X". Trigger on any request to compose outbound communication tied
  to a contact or deal.
---

# Follow Up

> **Tool names** below are expected names for the HubSpot MCP connector. If a named tool isn't available, check for updates to the skill and/or alternates in the HubSpot toolset as available tools may change

## Phase 1 — Identify the contact

Run simultaneously:
- `get_organization_details` → store `portalId`, `uiDomain`
- `search_crm_objects`: objectType: contacts, query: name from message, properties: firstname, lastname, email, jobtitle, hs_lead_status, lifecyclestage, associatedcompanyid, notes_last_contacted, hs_email_last_send_date

Multiple matches → disambiguation list (name, title, company), wait for confirmation.

Store: `contactId`, `contactEmail`, `firstName`, `companyId`. Search deals filtered by associations.contact = contactId — store `dealId`, `dealStage`, `dealPipeline`, `dealName`, `currentNextStep`.

---

## Phase 2 — Research (parallel)

### A. CRM context
`get_crm_objects` contacts, objectId: contactId, associations: deals, notes, tasks. Retrieve:
- **Open deal**: stage, pipeline, amount, close date, deal name, hs_next_step
- **Recent notes**: last 5 by hs_timestamp descending
- **Open tasks**: status NOT_STARTED or IN_PROGRESS

### B. Email history (if email connector available)
Search threads for contact's email, last 3–5. Note: last reply direction, tone, unanswered questions, subject context.

---

## Phase 3 — Determine email goal

Work through these in order, stopping as soon as the goal is clear:

**1. Infer from CRM history (highest signal)**
Look across recent notes, call logs, and email threads for explicit commitments, open questions, or stated next steps — e.g. "I'll send pricing next week", "They asked for a security review", "Waiting on legal sign-off". If a clear outstanding action or unanswered question exists, that is the goal.

**2. Infer from deal stage (if no clear signal from history)**
Fetch pipeline metadata and filter to the deal's specific pipeline:
- `get_properties`: objectType: deals, propertyNames: ["dealstage", "pipeline"] — returns all stage options across all pipelines, plus pipeline metadata.
- Filter `dealstage` options to only those belonging to `dealPipeline` (matched by pipeline ID). Portals commonly have multiple pipelines with different stage sets — do not use stages from other pipelines.
- Within the filtered stage list, sort by `displayOrder` and identify the current stage's position and the label of the next stage.
- Use the next stage label as the goal signal (e.g. next stage is "Contract Sent" → goal is to get the contract out).
- Also consider `hs_next_step` on the deal if populated.
- Do not use a fixed mapping. Reason from the actual stage labels and position in the pipeline.

**3. Ask the user (if goal is still unclear)**
If neither CRM history nor deal stage gives a confident goal, ask before drafting:
> "What's the goal of this email — what do you want [firstName] to do?"

Wait for the answer before proceeding.

---

## Phase 3b — Draft the email

- **Opening**: Never open with "I wanted to follow up", "Just checking in", or "Hope you're well." Open with a specific reference — recent email subject, note, company signal, or prior commitment.
- **Tone**: Match formality of contact's most recent emails. If no email history, default to professional-casual — clear and direct without being stiff.
- **Structure**: 3–5 short paragraphs, one clear ask at the end, directly tied to the goal determined in Phase 3.

Fields: `to` (name + email), `subject` (specific and contextual — e.g. "Re: Enterprise pilot scope" not "Following up"), `body` (signed with rep's name via `get_user_details`).

---

## Phase 4 — Show draft and offer delivery

Show the draft inline via `show_widget` — white card with a HubSpot-style email compose UI. The subject and body must be rendered as editable fields:

- **Subject**: single-line text input, pre-filled with the drafted subject. User can click and edit directly.
- **Body**: multi-line text area, pre-filled with the drafted body. User can click and edit directly.

When either field is edited, update the deep link URL in real time so the **Open in HubSpot** button always reflects the current subject and body.

Below the editable fields render:

1. **Open in HubSpot** (primary CTA button) — deep link with the current subject and body pre-populated (see Phase 5 for URL construction)

Below the CTA, show this microcopy in small muted text:
> "Sending via HubSpot lets you track email opens and link clicks."

### Widget CSS guidelines

**Font sizes**: The compose widget is a UI chrome element, not body text — keep it compact so it feels consistent with the surrounding chat interface:
- Field labels (`To`, `Subject`): 12px, `color: var(--text-secondary)`
- Subject input and body textarea: 12px
- Recipient line: 12px
- Microcopy below the button: 11px, `color: var(--text-muted)`

**CTA button**: Per HubSpot's MCP Apps design system, the primary bridge button is solid black. Use hardcoded hex values — not CSS variables — so the button is reliably black in both light and dark mode. CSS variable-based accent colors (e.g. `var(--fill-accent)`) resolve to the host theme's blue and must not be used here.

```css
background: #000;
color: #fff;
border: none;
border-radius: 6px;
padding: 8px 16px;
font-size: 13px;
font-weight: 500;
cursor: pointer;
text-decoration: none;
display: inline-flex;
align-items: center;
gap: 6px;
```

---

## Phase 5 — Build the HubSpot deep link

Construct the URL so that HubSpot opens the contact record with the email compose panel pre-filled.

**URL format:**
```
https://[uiDomain]/contacts/[portalId]/contact/[contactId]?interaction=email&initialEmailSubject=[encoded_subject]&initialEmailBody=[encoded_body]
```

**Encoding rules:**
- Convert the email body to HTML: wrap each paragraph in `<p>...</p>`, line breaks become `<br>`.
- Encode both subject and body using standard percent-encoding (equivalent to `encodeURIComponent`): spaces become `%20`, all other special characters percent-encoded (e.g. `<` → `%3C`, `>` → `%3E`, `,` → `%2C`, `@` → `%40`). Do not use form-style encoding — `+` must not be used to represent spaces.
- Example subject: "Thanks for your time, Brian" → `Thanks%20for%20your%20time%2C%20Brian`
- Example body opening: `<p>Hi Brian,</p>` → `%3Cp%3EHi%20Brian%2C%3C%2Fp%3E`

Present as a clearly labelled button: **"Open in HubSpot →"**

Do not save a note. Do not store the draft body in HubSpot.

---

## Phase 6 — Deal updates (if deal exists)

After the user opens the email in HubSpot, check whether deal fields should be updated. Do this proactively based on context — don't ask unless it's ambiguous.

**Next step / task**: If the email implies a clear next action (e.g. "I'll send the proposal Thursday", "Let me know by EOW"), update or create accordingly:
- Update `hs_next_step` on the deal via `manage_crm_objects` deals, updateRequest
- Create a follow-up task: `manage_crm_objects` tasks, createRequest — hs_task_subject: "Follow up with [firstName] if no reply", hs_task_type: EMAIL, hs_task_status: NOT_STARTED, hs_timestamp: [due date], associations: contact + deal

**Deal stage**: If the email signals a stage change (e.g. sending a proposal → qualifiedtobuy, contract sent → contractsent), ask before updating:
> "This looks like it moves the deal to [stage] — want me to update that too?"

Confirm before executing any deal stage change.

**No deal**: Skip this phase entirely.

---

## Error handling

- **Contact not found**: Ask to clarify. Do not proceed without a confirmed contactId.
- **Multiple matches**: Disambiguation list. Wait for confirmation.
- **No deal**: Skip Phase 6. Default email goal: move the relationship forward.
- **No email history**: Base draft on CRM notes only.
