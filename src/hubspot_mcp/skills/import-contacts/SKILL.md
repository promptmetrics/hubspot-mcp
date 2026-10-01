---
name: import-contacts
description: >
  Imports contacts into HubSpot from any source — a CSV file, a pasted list,
  or data from connected apps like Gmail. Handles field mapping, duplicate
  detection, batch import, and company association.

  ALWAYS use this skill when the user wants to bring contacts into HubSpot —
  "import contacts", "upload a CSV", "add these people to HubSpot", "import
  my list", "bring in contacts from [source]", "I have a spreadsheet of
  contacts", "add contacts from my email", "migrate contacts", "sync contacts
  from [app]", "load my network". Trigger whenever the user wants to bulk-add
  people to the CRM, regardless of source.
---

# Import Contacts

> **Tool names** below are expected names for the HubSpot MCP connector. If a named tool isn't available, check for updates to the skill and/or alternates in the HubSpot toolset as available tools may change

---

## Phase 1 — Identify the data source

Use this priority order:

1. **Infer from context** — if the user attached a file, said "from my CSV", "from Salesforce", or similar, proceed directly to Phase 2 with that source.
2. **Check connected services** — silently test which connectors are available (attempt a lightweight tool call for each). Build a dynamic list of sources to offer.
3. **Ask** if the source isn't clear.

**Ask as plain text — no widget.** Build the question based on what connectors are available:

If connected services are detected, list them under a heading, then offer the manual options:
```
Where are your contacts coming from?

From a connected service:
1. [Email provider] — extract contacts from recent threads
2. [Other CRM, e.g. Salesforce] — pull contacts from existing records

Or add them manually:
3. Upload a file — share a path or attach a CSV/spreadsheet
4. Paste a list — names, emails, and companies
```

If no connectors are detected:
```
Where are your contacts coming from?
1. Upload a file — share a path or attach a CSV/spreadsheet
2. Paste a list — names, emails, and companies
```

**For connected email provider:** Use the connector's search/thread tools to find recent contacts. Extract unique senders and recipients from recent threads. Record the timestamp of the most recent thread per contact as the "last contact" date.

**For connected CRM:** Use the CRM's export or search tools to pull contact records. Map the source CRM's fields to HubSpot properties.

Fetch portal context early using `get_organization_details` and `get_user_details`. Store `portalId`, `userId` (for `hubspot_owner_id`), and the **user's email domain** (for filtering internal colleagues in Phase 2).

---

## Phase 2 — Parse, deduplicate, and confirm

### Field mapping

Map detected column headers (or inferred positions) to HubSpot contact properties:

| Detected column | HubSpot property |
|---|---|
| name, full name | Split into `firstname` + `lastname` |
| first / first name | `firstname` |
| last / last name / surname | `lastname` |
| email, email address | `email` |
| phone, mobile, cell | `phone` |
| company, organization | `company` |
| title, job title, role | `jobtitle` |
| website, url | `website` |
| linkedin | `hs_linkedin_url` |

Use `get_properties` with objectType: contacts to confirm target properties exist. Use `search_properties` for non-standard or custom column names.

### Filter internal contacts

Remove any contact whose email domain matches the user's own domain from Phase 1. Contacts with no email cannot be filtered on domain — leave them in; a warning will surface in the widget. Note how many were removed — surface this in the widget header.

### Duplicate check

For contacts with a valid email, batch into groups of 50 and call `search_crm_objects`:
- objectType: contacts
- filter: email IN [...batch of emails...]
- properties: email, firstname, lastname, hs_object_id

Classify each contact as **New** (not in HubSpot) or **Existing** (already in HubSpot).

### Data quality flags

Before showing the widget, flag:
- Rows with no email address
- Malformed email addresses
- Duplicate emails within the source file itself
- Unmapped columns

### Contact selection widget

Render an interactive widget using `show_widget`. **Do not show a text summary first — the widget is the confirmation step.**

The widget must include:

1. **Header**: source label (e.g. "Found in your CSV"), subtitle with source metadata, and a note if contacts were skipped (internal domain, invalid emails)
2. **Scrollable table** with columns: checkbox, NAME, EMAIL, COMPANY, LAST CONTACT (for email/CRM source; omit for CSV/paste), STATUS badge ("Already in HubSpot" for existing contacts)
3. **Select-all checkbox** in the header row — toggles all, shows indeterminate state if partial
4. **Footer**: "Cancel" text button and a primary "Import N contacts" button (N updates live as checkboxes are toggled; disabled if 0 selected)

On "Import N contacts" click:
```js
sendPrompt('IMPORT_CONFIRMED:' + JSON.stringify(selectedEmails))
```

On "Cancel":
```js
sendPrompt('IMPORT_CANCELLED')
```

Include all contacts in the widget regardless of duplicate status. Contacts with no email or invalid email: include but unchecked by default with a muted "No email" label. Existing contacts: include, checked by default, with the status badge.

**Handling responses:**
- `IMPORT_CONFIRMED:[...]` — parse the JSON array and proceed to Phase 3
- `IMPORT_CANCELLED` — acknowledge and stop
- Free-text message (e.g. "skip the ones from Acme") — interpret, adjust selection, re-render widget

Do not proceed to Phase 3 until a confirmed selection is received.

---

## Phase 3 — Import

### Prepare records

For each confirmed contact:
1. Build HubSpot properties object from the confirmed field mapping
2. Split full names: everything before the first space = `firstname`, everything after = `lastname`. Single-word names → `firstname` only, `lastname` blank. If `firstname`/`lastname` columns exist separately, use them directly instead of splitting.
3. Assign `hubspot_owner_id` to the current user's ID from Phase 1
4. Note any company name for association

### Batch import

Split into batches of 50. For each batch, call `manage_crm_objects`:
- **New contacts**: `createRequest`, objectType: contacts
- **Updates**: `updateRequest`, objectType: contacts, using `hs_object_id` from Phase 2

Show a single updating progress line:
```
Importing 55 contacts… (batch 3 of 6 complete)
```

Capture any per-record errors and store for the Phase 4 summary.

### Company association

For contacts with a company name:
1. `search_crm_objects` companies, filter: name = [company name]
2. If found, associate via `manage_crm_objects` with association type `contact_to_company`
3. If not found, create via `manage_crm_objects` createRequest, objectType: companies, then associate

Batch company lookups where possible to reduce API calls.

---

## Phase 4 — Summary

```
Import complete.

  ✓ [N] contacts created
  ✓ [N] contacts updated
  — [N] contacts skipped (no email) — [names up to 3, +N more]
  ✗ [N] errors (see below)

Errors:
  Row [N]: "[value]" — [reason]

Companies:
  · [N] new companies created
  · [N] contacts associated with existing companies

View contacts: https://[uiDomain]/contacts/[portalId]/contacts/list/view/all/
```

Name individual skipped/errored contacts up to 5; after that "[first 5 names] + N more." If any rows errored, offer to export a list so the user can fix and re-import.

---

## Phase 5 — Post-import: log history and surface deal signals

Run after Phase 4. Only apply the steps relevant to the import source.

### 5a — Log existing communication history (email/CRM sources only)

If contacts were sourced from a connected email provider or CRM:
1. For each newly imported contact, search the source connector for recent threads or interactions involving their email address (last 90 days, up to 5 per contact).
2. For each thread found, create a note in HubSpot:
   - `manage_crm_objects` notes, createRequest: hs_note_body: "[Date] — [Subject/topic] via [source]", hs_timestamp: thread date, association: contact
3. Skip contacts where no threads are found — no note needed.
4. Cap total notes written to 100 (prioritise most recent threads first if over the limit).

### 5b — Detect deal signals

Review the threads or records from 5a (or the source data itself if CSV/paste) for buying signals: pricing discussions, demo requests, proposal mentions, evaluation timelines, or decision language.

For contacts with a clear signal:
- Suggest creating a deal: "I noticed [Name] mentioned [signal]. Want me to create a deal for them?"
- If the user confirms, create via `manage_crm_objects` deals, createRequest: dealname: "[Contact Name] — [company]", dealstage: appointmentscheduled, associations: contact → contactId.
- If there are multiple signal contacts, show a brief table (Name, Signal, Company) and ask which to convert.

Skip contacts with no signals — don't create speculative deals.

### 5c — Recommend ongoing activity logging

After a successful import, prompt:
> "To keep [N] new contacts up to date automatically, connect your email to HubSpot. That way future emails are logged as activity without any manual steps.
> Say 'connect my email to HubSpot' when you're ready to set it up."

Skip this prompt if an email connector is already logging to HubSpot (infer from whether 5a returned activity data).

---

## Edge cases

- **No email column**: warn strongly — HubSpot deduplicates by email. Offer to continue with a caveat or ask the user to add emails first.
- **Large files (>500 rows)**: warn that import may take a moment. Continue without extra confirmation.
- **All contacts are duplicates**: confirm with the user before exiting — they may have intended to update records.
- **Source returns no contacts**: let the user know and suggest they try a different source or add contacts manually.
- **Company association fails**: log the failure in the summary but do not block the contact import.
