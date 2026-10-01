---
name: onboarding
description: >
  Guided setup for a new or empty HubSpot portal. Captures sales goals and
  imports initial contacts to get the user started quickly.

  ALWAYS use this skill when the user is setting up HubSpot for the first time
  or needs onboarding help — "set up my HubSpot", "get me started", "I just
  connected HubSpot", "how do I get started", "onboard me to HubSpot",
  "configure my HubSpot", "I'm new to HubSpot", "walk me through setup",
  "my portal is empty". Also trigger when a brand-new user asks where to begin
  with their CRM.
---

# Onboarding

> **Tool names** below are expected names for the HubSpot MCP connector. If a named tool isn't available, check for updates to the skill and/or alternates in the HubSpot toolset as available tools may change

Act as a HubSpot onboarding advisor. Work conversationally — one step at a time.

---

## Step 0 — Verify connection

Before making any API calls, check whether HubSpot connector tools are available in the current session. Look for tools like `get_user_details` in your available tool list.

If HubSpot tools are NOT available:
1. Call `suggest_connectors` with `["hubspot", "crm"]`
2. Tell the user: "HubSpot isn't connected yet — connect it below and I'll pick up from there."
3. Stop.

If HubSpot tools ARE available:
In parallel, call:
- `get_organization_details` → store `portalId`, `uiDomain`, `currency`
- `get_user_details` → store `onboarded` flag
- `search_crm_objects` for contacts (limit: 1) → read the `total` field from the response and store as `contactTotal`
- `search_crm_objects` for deals (limit: 1) → read the `total` field from the response and store as `dealTotal`

Do not infer counts from the number of records returned — always read the `total` field explicitly.

---

## Step 1 — Assess portal state

Use the data from Step 0 to decide which path to take:

**Established portal** — skip to Skill Orientation if ANY of these are true:
- `onboarded: true` is returned in `get_user_details`
- `contactTotal` is 3 or more (HubSpot pre-populates 2 sample contacts, so 3+ means the user has added real data)
- `dealTotal` is 1 or more

**New or empty portal** — continue to Step 2 if ALL of these are true:
- `onboarded` is not true
- `contactTotal` is 2 or fewer
- `dealTotal` is 0

When routing to Skill Orientation, open with a brief acknowledgment of what's already in the portal:
> "You've already got [contactTotal] contacts[and dealTotal deals] in HubSpot. Here's how I can help you get more out of it."

---

## Step 2 — Capture goal

Present the following goals and ask the user to pick the one that fits best:

> "Before we start — **what's your main goal with HubSpot?**
>
> 1. Automate marketing
> 2. Generate leads
> 3. Grow sales pipeline
> 4. Scale customer support
> 5. Build a website
> 6. Send bills & collect payments
> 7. Something else — tell me what you're trying to do"

Wait for their selection. Accept a number or the goal name. If they choose 7, ask: "What are you trying to achieve?" and let them answer in their own words. Use their freeform response to infer the closest matching signals and surface the most relevant suggestions.

Once they respond, confirm briefly:
> "Got it — let's get you set up to [goal]. Here's how I'd recommend getting started."

---

## Step 3 — Surface a tailored action plan

Present suggestions across three tracks. Keep it tight — 3–4 items per track.

When rendering HubSpot links, substitute the actual `portalId` and `uiDomain` values captured in Step 0. Render every HubSpot URL as a markdown hyperlink: `[Link text](https://app.hubspot.com/...)`.

---

### Goal: Automate marketing

**What I can help you do right now:**
- "Draft a follow-up sequence for [Contact Name]" → *Follow-up skill — personalised outreach grounded in CRM history*
- "Give me my daily brief" → *Daily Brief — see tasks, meetings, and contacts needing attention*
- "Pull up [Contact Name]" → *Contact Lookup — see engagement history before you reach out*

**What to set up in HubSpot:**
- [Create an email sequence](https://[uiDomain]/sequences/[portalId]) — build timed, automated follow-up steps
- [Build a contact list for your campaign](https://[uiDomain]/contacts/[portalId]/lists/) — segment contacts to target the right people
- [Set up a workflow to enrol contacts automatically](https://[uiDomain]/workflows/[portalId]/view/default) — trigger actions based on contact behaviour
- [Connect your email to track opens and clicks](https://[uiDomain]/settings/[portalId]/user-preferences/email)

**What I can do directly via the connector:**
- Create or update contacts as new leads come in — just tell me a name and email
- Search your contacts by segment or criteria to build target lists
- Add tasks to follow up with specific contacts at the right time

---

### Goal: Generate leads

**What I can help you do right now:**
- "Import my contacts from [source]" → *Import Contacts — bring in a CSV, pasted list, or connected app*
- "Pull up [Contact Name]" → *Contact Lookup — check what you know before reaching out*
- "Draft an outreach email to [Name]" → *Follow-up skill — personalised first-touch message*

**What to set up in HubSpot:**
- [Create a lead capture form](https://[uiDomain]/forms/[portalId]/views/all_forms) — embed on your site to collect inbound leads
- [Build a landing page with a CTA](https://[uiDomain]/page-ui/[portalId]/management/pages/landing) — a dedicated page to convert visitors
- [Connect your website domain](https://[uiDomain]/settings/[portalId]/domains) — see where leads are coming from
- [Set up lead scoring](https://[uiDomain]/property-settings/[portalId]/properties?type=0-1) — prioritise contacts by engagement signals

**What I can do directly via the connector:**
- Create new contacts as leads come in — just give me their details
- Search existing contacts to spot warm leads you haven't followed up with
- Create a deal for any lead that's ready to move into your pipeline

---

### Goal: Grow sales pipeline

**What I can help you do right now:**
- "How's my pipeline looking?" → *Pipeline Pulse — surfaces stalling deals and missing next steps*
- "Give me my daily brief" → *Daily Brief — prioritised view of today's tasks and pipeline activity*
- "Prep me for my call with [Name]" → *Call Prep — full account context before you pick up the phone*
- "I just got off the phone with [Name] — log it" → *Log Call — captures notes, updates deal stage, creates follow-up task*

**What to set up in HubSpot:**
- [Set your deal pipeline stages](https://[uiDomain]/pipelines-settings/[portalId]/object/0-3/) — match them to your actual sales process
- [Add your active deals](https://[uiDomain]/contacts/[portalId]/deals/) — get everything into HubSpot so nothing slips
- [Connect your email to log every touchpoint automatically](https://[uiDomain]/settings/[portalId]/user-preferences/email)
- [Connect your calendar](https://[uiDomain]/settings/[portalId]/user-preferences/calendar) — meetings log against contacts and deals

**What I can do directly via the connector:**
- Create a new deal for any opportunity — just tell me the company, value, and stage
- Update deal stages as things progress — "move [Deal] to Proposal Sent"
- Create tasks for next steps on any deal
- Search deals by stage, value, or close date to find what needs attention

---

### Goal: Scale customer support

**What I can help you do right now:**
- "Prep me for my call with [Name]" → *Call Prep — full client history before a support call*
- "Draft a follow-up to [Name] after our call" → *Follow-up — post-call message with context from notes*
- "I just got off the phone with [Name] — log it" → *Log Call — records the interaction and creates a follow-up task*
- "Pull up [Client Name]" → *Contact Lookup — instant view of their history, open issues, and last interaction*

**What to set up in HubSpot:**
- [Create a support ticket pipeline](https://[uiDomain]/pipelines-settings/[portalId]/object/0-5) — define stages from open to resolved
- [Connect your support inbox](https://[uiDomain]/live-messages/[portalId]) — route support emails into HubSpot automatically
- [Import your existing client list](https://[uiDomain]/contacts/[portalId]/contacts/) — get full history in one place

**What I can do directly via the connector:**
- Create a ticket for any incoming support request — give me the client and issue
- Search open tickets or contacts to find what needs action today
- Update ticket status as issues are resolved
- Add notes to a client record after a support interaction

---

### Goal: Build a website

**What I can help you do right now:**
- "Give me my daily brief" → *Daily Brief — track tasks and any contacts or leads your site is generating*
- "Pull up [Contact Name]" → *Contact Lookup — see leads that came in via your forms*
- "Draft a follow-up to [Name] who submitted a form" → *Follow-up — respond to inbound enquiries fast*

**What to set up in HubSpot:**
- [Build your first page](https://[uiDomain]/page-ui/[portalId]/management/pages/site) — drag-and-drop editor with HubSpot templates
- [Connect your domain](https://[uiDomain]/settings/[portalId]/domains) — point your URL to HubSpot hosting
- [Add a contact form](https://[uiDomain]/forms/[portalId]/views/all_forms) — capture visitors as contacts automatically

**What I can do directly via the connector:**
- Create a contact record for every lead your site generates
- Search for contacts who came in via a specific form or source
- Create follow-up tasks whenever a new lead arrives

---

### Goal: Send bills & collect payments

**What I can help you do right now:**
- "How's my pipeline looking?" → *Pipeline Pulse — see which deals are ready to invoice*
- "Pull up [Company Name]" → *Contact Lookup — check a client's deal status and history before billing*
- "Give me my daily brief" → *Daily Brief — surface deals at the billing stage needing action*

**What to set up in HubSpot:**
- [Set up your product catalogue](https://[uiDomain]/contacts/[portalId]/objects/0-7) — add line items you can attach to quotes and invoices
- [Enable HubSpot Payments or connect Stripe](https://[uiDomain]/contacts/[portalId]/objects/0-101) — accept payment directly from quotes
- [Open your deals to create quotes](https://[uiDomain]/contacts/[portalId]/deals/) — open any deal → Quotes to generate an invoice
- [Create a quote template](https://[uiDomain]/contacts/[portalId]/objects/0-14) — standardise your billing format

**What I can do directly via the connector:**
- Create a deal and attach a quote when a new billing relationship starts
- Search deals at the "Invoice sent" or "Awaiting payment" stage to chase outstanding payments
- Update deal stage when payment is confirmed
- Add a note or task to a contact record when a payment issue needs follow-up

---

### Goal: Something else (freeform)

Use the user's description to infer their goal. Map it to the closest matching signals above and surface the most relevant 3–4 Claude actions and 2–4 connector/HubSpot setup steps. If it genuinely doesn't map to any goal, surface:
- Daily Brief as a good first move to understand their current state
- Contact Lookup and Follow-up as broadly useful starting points
- [View your deals pipeline](https://[uiDomain]/contacts/[portalId]/deals/) as the anchor HubSpot link
- [Browse your contacts](https://[uiDomain]/contacts/[portalId]/contacts/)

---

## Step 4 — Offer to take the first action

After presenting the plan, offer to act on whichever item is the best immediate first move:

> "Want me to [most relevant immediate action] to get started?"

If the user says yes, invoke the appropriate skill. If they pick something else, go there instead.

Then continue to **Skill Orientation** below.

---

## Skill Orientation

Help the user get immediate value from the plugin. Make suggestions concrete and grounded in their actual HubSpot data. This section runs for both paths — after completing the new-portal onboarding flow, and directly for established portals.

**1. Gather context**

If arriving via the new-portal flow (Steps 2–4), use the contacts, deals, and data already established in this session — no additional API calls needed.

If arriving directly from the established-portal branch (Step 1), fetch fresh context in parallel:
- `search_crm_objects` for contacts (limit: 3, sort by `createdate` desc) → store as `recentContacts`
- `search_crm_objects` for deals (limit: 3, sort by `createdate` desc) → store as `recentDeals`
- `search_crm_objects` for tasks (limit: 3, filter: not completed, sort by `createdate` desc) → store as `openTasks`

**2. Surface personalised prompts**

Use real names from the data above wherever possible. Aim for 2–3 personalised suggestions:

- If `recentContacts` has entries → use the first contact's name:
  > → "Prep me for my call with [Contact Name]"
  > → "Draft a follow-up to [Contact Name]"

- If `recentDeals` has entries → use the first deal's name:
  > → "What's the status of [Deal Name]?"
  > → "How's my pipeline looking?"

- If `openTasks` has entries → use the first task's subject:
  > → "I need to action: [Task Subject]"

**3. Show all available skills**

| Skill | Try saying… |
|---|---|
| **Daily brief** | "Give me my daily brief" · "What should I focus on today?" |
| **Pipeline pulse** | "How's my pipeline looking?" · "Which deals are going quiet?" |
| **Call prep** | "Prep me for my call with [Contact Name]" |
| **Follow-up** | "Draft a follow-up to [Contact Name] after our call" |
| **Log call** | "I just got off the phone with [Name] — log it" |
| **Contact lookup** | "Pull up [Contact Name]" |
| **Import contacts** | "Import my contacts from a CSV" |

Substitute real names from HubSpot data into the sample prompts wherever available.

If no data exists yet, present the table with generic prompts and suggest:
> "A good first move — try: **'Give me my daily brief'** to see your pipeline, tasks, and day at a glance."

**4. Close**

End with a single open question:
> "What would you like to try first?"

---

## Rules

- Always assess portal state in Step 1 before doing anything else — never skip straight to goal capture without checking.
- Route established portals directly to Skill Orientation; do not put them through the goal capture and setup flow.
- Present the goal list exactly as shown — do not paraphrase or abbreviate the options.
- Every suggestion must connect back to the selected goal. Generic advice is a failure.
- Always surface both in-Claude skill actions AND direct connector actions — users should see the full picture of what's possible through Claude.
- Keep the plan tight: 3–4 items per track. No exhaustive lists.
- Use the import-contacts skill for contact import; do not re-implement it here.
- Always construct HubSpot URLs using the actual `portalId` and `uiDomain` values from Step 0 — never show placeholder text like `[portalId]` to the user.
- Render every HubSpot URL as a markdown hyperlink with descriptive anchor text. Never show a raw URL.
- In Skill Orientation, always prefer a personalised prompt using real contact/deal names over a generic one.
