"""Job 3: log a sales meeting as ONE changeset the rep confirms once.

After a call a rep has five things to record: the meeting, a note, a new close
date and amount on the deal, the tasks they now owe, and any new contact they
met. In the raw API that is a dozen calls across four object types, and a
half-finished sequence leaves the deal inconsistent. ``pronova_log_meeting_outcome``
resolves the deal, reads what it needs, and returns a single preview from
:func:`hubspot_mcp.intent.changes.propose`. Nothing is written until the model
calls ``pronova_apply_changes``; ``pronova_undo_changes`` reverses all of it.

Choices a reviewer should know about:

* A name that matches more than one open deal returns ``status: "ambiguous"`` with
  candidates. It never guesses, and nothing is stored.
* The meeting and note attach to the deal, its primary company, and its contact,
  and each task to the deal and that company, but only where the target is
  unambiguous. A deal with several contacts, or several companies and none
  marked Primary, gets no association to that kind and the result says so in
  ``warnings``.
* A new contact whose email already exists, or whose name matches a contact of the
  company, is reused (linked to the company and the deal), not duplicated.
* Dates arrive as ISO text. Words like "Friday" are the model's job; the result
  states ``as_of`` so the user can check the date it chose. A date without a time
  is stored at 12:00 in the portal timezone, which keeps the calendar day stable
  in UTC for Europe and the Americas.
* A frozen ``as_of`` in the sales rules also sets the default meeting time, so a
  benchmark run does not depend on the wall clock.
"""
from __future__ import annotations

import html
import math
import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from hubspot_mcp.intent import changes, common
from hubspot_mcp.intent.rules import RulesError, SalesRules, load_rules, resolve_as_of, rules_stamp

MAX_TASKS = 10
MAX_NEW_CONTACTS = 10
MAX_CANDIDATES = 10
MAX_COMPANY_MATCHES = 5
MAX_SUMMARY_CHARS = 20_000
MEETING_OUTCOME = "COMPLETED"
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")
_CURRENCY = re.compile(r"[A-Za-z]{3}")
_CONTACT_PROPERTIES = ("firstname", "lastname", "email", "jobtitle")


class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: Annotated[str, Field(min_length=1, max_length=255, description="What the rep owes, for example 'Send the security document'.")]
    due_date: Annotated[date, Field(description="Due date as ISO YYYY-MM-DD. Turn words like 'Friday' into a date first.")]


class NewContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: Annotated[str, Field(min_length=1, max_length=100, description="Given name.")]
    last_name: Annotated[str, Field(min_length=1, max_length=100, description="Family name.")]
    email: Annotated[str | None, Field(description="Email address, if known. An existing contact with this email is reused, not duplicated.")] = None
    job_title: Annotated[str | None, Field(description="Job title, for example 'CFO'.")] = None


# --- input checks ----------------------------------------------------------


def _plain_number(value: float) -> str:
    return format(Decimal(str(value)).normalize(), "f")


def _local_noon(day: date, tz: str) -> datetime:
    return datetime.combine(day, time(12, 0), tzinfo=ZoneInfo(tz))


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_meeting_time(text: str | None, rules: SalesRules, now: datetime | None = None) -> tuple[datetime, bool]:
    """``(moment, defaulted)``. Naive times are read in the portal timezone; a bare date means 12:00 there."""
    if text is not None and text.strip():
        raw = text.strip()
        if _DATE_ONLY.fullmatch(raw):
            try:
                return _local_noon(date.fromisoformat(raw), rules.timezone), False
            except ValueError as exc:
                raise ToolError(f"meeting_time {raw!r} is not a real date. Use ISO, for example 2026-11-02 or 2026-11-02T14:30.") from exc
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ToolError(f"meeting_time {raw!r} is not ISO 8601. Use 2026-11-02 or 2026-11-02T14:30.") from exc
        return (moment if moment.tzinfo else moment.replace(tzinfo=ZoneInfo(rules.timezone))), False
    if rules.as_of is not None:
        return _local_noon(rules.as_of, rules.timezone), True
    return (now or datetime.now(UTC)), True


def _check_inputs(
    summary: str,
    amount: float | None,
    currency: str | None,
    tasks: list[TaskIn],
    new_contacts: list[NewContactIn],
) -> str | None:
    """Validate everything that needs no HubSpot call; returns the upper-cased currency."""
    if not summary or not summary.strip():
        raise ToolError("summary must not be empty: it becomes the meeting and note text.")
    if len(summary) > MAX_SUMMARY_CHARS:
        raise ToolError(f"summary is {len(summary)} characters; keep it under {MAX_SUMMARY_CHARS}.")
    if amount is not None and (not math.isfinite(amount) or amount < 0):
        raise ToolError("amount must be a number of 0 or more.")
    if currency is not None and not _CURRENCY.fullmatch(currency.strip()):
        raise ToolError("currency must be a three-letter code such as EUR.")
    if currency is not None and amount is None:
        raise ToolError("currency needs an amount: changing only the currency would silently re-price the deal.")
    if len(tasks) > MAX_TASKS:
        raise ToolError(f"At most {MAX_TASKS} tasks per call; log the rest in a second call.")
    if len(new_contacts) > MAX_NEW_CONTACTS:
        raise ToolError(f"At most {MAX_NEW_CONTACTS} new contacts per call.")
    seen: set[str] = set()
    for c in new_contacts:
        if c.email is not None and c.email.strip() and not _EMAIL.fullmatch(c.email.strip()):
            raise ToolError(f"{c.email!r} is not an email address. Leave email out if you do not have one.")
        key = (c.email or "").strip().casefold() or f"{c.first_name} {c.last_name}".casefold()
        if key in seen:
            raise ToolError(f"{c.first_name} {c.last_name} appears twice in new_contacts.")
        seen.add(key)
    return currency.strip().upper() if currency else None


# --- resolving the deal ----------------------------------------------------


def _name(record: dict[str, Any], prop: str) -> str:
    return str((record.get("properties") or {}).get(prop) or "")


def _is_open(deal: dict[str, Any], pipelines: common.PipelineIndex) -> bool:
    props = deal.get("properties") or {}
    stage = pipelines.stage(props.get("dealstage"))
    if stage is not None:
        return not stage.is_closed
    return str(props.get("hs_is_closed")).strip().lower() != "true"


async def _company_names(lf: dict[str, Any], deal_ids: list[str]) -> dict[str, list[str]]:
    links = await common.batch_read_associations(lf, "deals", "companies", deal_ids)
    ids = sorted({c for targets in links.values() for c in targets})
    names = {str(r.get("id")): _name(r, "name") for r in await common.batch_read(lf, "companies", ids, ["name"])}
    return {d: [names.get(c) or f"company {c}" for c in targets] for d, targets in links.items()}


async def resolve_deal(
    lf: dict[str, Any], dctx: common.DealContext, query: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    """``(deal, candidates, how)``: exactly one of ``deal`` and ``candidates`` is set.

    Order: a numeric id that is a deal; an open deal whose name equals the text;
    open deals of the company (or companies) the text names, plus open deals whose
    name contains it. One candidate is the answer, several are ambiguity, none is an error.
    """
    text = query.strip()
    if not text:
        raise ToolError("deal_or_company must not be empty.")
    if text.isdigit():
        found = await common.batch_read(lf, "deals", [text], common.DEAL_PROPERTIES)
        if found:
            return found[0], [], "id"
    by_name = (
        await common.search_page(
            lf,
            "deals",
            query=text,
            filter_groups=common.deal_filter_groups(open_only=True),
            properties=common.DEAL_PROPERTIES,
            limit=20,
        )
    ).get("results") or []
    named = [d for d in by_name if _is_open(d, dctx.pipelines)]
    exact = [d for d in named if _name(d, "dealname").casefold() == text.casefold()]
    if len(exact) == 1:
        return exact[0], [], "exact deal name"

    companies = (
        await common.search_page(lf, "companies", query=text, properties=["name", "domain"], limit=10)
    ).get("results") or []
    exact_companies = [c for c in companies if text.casefold() in (_name(c, "name").casefold(), _name(c, "domain").casefold())]
    chosen = exact_companies or companies[:MAX_COMPANY_MATCHES]
    candidates: dict[str, dict[str, Any]] = {str(d["id"]): d for d in (exact or named)}
    if chosen:
        links = await common.batch_read_associations(lf, "companies", "deals", [c["id"] for c in chosen])
        deal_ids = sorted({d for targets in links.values() for d in targets} - set(candidates))
        for deal in await common.batch_read(lf, "deals", deal_ids, common.DEAL_PROPERTIES):
            if _is_open(deal, dctx.pipelines):
                candidates[str(deal["id"])] = deal
    if len(candidates) == 1:
        return next(iter(candidates.values())), [], "the only open deal matching"
    if not candidates:
        if chosen:
            listed = ", ".join(_name(c, "name") or str(c["id"]) for c in chosen)
            raise ToolError(f"{listed} has no open deal to log this meeting on. Ask the user which deal, or create one first.")
        raise ToolError(f"No open deal or company matches {text!r}. Ask the user for the deal name, or search with pronova_resolve_record.")
    return None, sorted(candidates.values(), key=lambda d: (_name(d, "dealname").casefold(), str(d["id"]))), "ambiguous"


async def _ambiguous(
    lf: dict[str, Any], dctx: common.DealContext, query: str, candidates: list[dict[str, Any]], stamp: dict[str, Any]
) -> dict[str, Any]:
    shown = candidates[:MAX_CANDIDATES]
    companies = await _company_names(lf, [str(d["id"]) for d in shown])
    lines = []
    for deal in shown:
        line = common.deal_line(deal, dctx)
        line["company"] = ", ".join(companies.get(str(deal["id"]), [])) or None
        lines.append(line)
    out: dict[str, Any] = {
        "status": "ambiguous",
        "query": query,
        **stamp,
        "candidates": lines,
        "next": (
            f"{len(candidates)} open deals match {query!r}. Nothing was prepared and nothing was written. "
            "Ask the user which one, then call pronova_log_meeting_outcome again with that deal's id as deal_or_company."
        ),
    }
    if len(candidates) > len(shown):
        out["more_candidates"] = len(candidates) - len(shown)
    return out


# --- associated records ----------------------------------------------------


async def _deal_company(lf: dict[str, Any], deal_id: str) -> tuple[tuple[str, str] | None, str | None]:
    """The deal's company as ``(id, name)``; the Primary one when there are several."""
    body = await common.read_post(
        lf, "/crm/v4/associations/deals/companies/batch/read", {"inputs": [{"id": deal_id}]}
    )
    targets: list[tuple[str, bool]] = []
    for row in body.get("results") or []:
        for target in row.get("to") or []:
            labels = {str(t.get("label") or "").casefold() for t in target.get("associationTypes") or []}
            targets.append((str(target["toObjectId"]), "primary" in labels))
    if not targets:
        return None, None
    chosen = targets[0] if len(targets) == 1 else None
    if chosen is None:
        primaries = [t for t in targets if t[1]]
        chosen = primaries[0] if len(primaries) == 1 else None
    if chosen is None:
        return None, f"The deal has {len(targets)} companies and none is marked Primary, so the meeting and note are not associated with a company."
    records = await common.batch_read(lf, "companies", [chosen[0]], ["name"])
    return (chosen[0], (_name(records[0], "name") if records else "") or f"company {chosen[0]}"), None


async def _contact_name(lf: dict[str, Any], contact_id: str) -> str:
    records = await common.batch_read(lf, "contacts", [contact_id], ["firstname", "lastname"])
    if not records:
        return f"contact {contact_id}"
    props = records[0].get("properties") or {}
    return " ".join(p for p in (props.get("firstname"), props.get("lastname")) if p) or f"contact {contact_id}"


def _person(props: dict[str, Any]) -> str:
    return " ".join(str(p) for p in (props.get("firstname"), props.get("lastname")) if p).casefold()


async def _existing_contact(
    lf: dict[str, Any], contact: NewContactIn, company_contacts: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """A contact already in HubSpot for this person: by email anywhere, else by name among the company's contacts."""
    email = (contact.email or "").strip()
    if email:
        hits = (
            await common.search_page(
                lf,
                "contacts",
                filter_groups=[{"filters": [common.prop_filter("email", "EQ", email.lower())]}],
                properties=list(_CONTACT_PROPERTIES),
                limit=2,
            )
        ).get("results") or []
        if hits:
            return hits[0]
    wanted = f"{contact.first_name} {contact.last_name}".strip().casefold()
    matches = [c for c in company_contacts if _person(c.get("properties") or {}) == wanted]
    return matches[0] if len(matches) == 1 else None


# --- building the changeset ------------------------------------------------


def _html(text: str) -> str:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    return "".join(f"<p>{html.escape(p, quote=False).replace(chr(10), '<br>')}</p>" for p in paragraphs)


def _short(text: str, limit: int = 80) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _targets(deal_id: str, company_id: str | None, contact_id: str | None) -> list[dict[str, Any]]:
    out = [{"to": {"object_type": "deals", "id": deal_id}}]
    if company_id:
        out.append({"to": {"object_type": "companies", "id": company_id}})
    if contact_id:
        out.append({"to": {"object_type": "contacts", "id": contact_id}})
    return out


def _deal_update(
    props: dict[str, Any],
    rules: SalesRules,
    close_date: date | None,
    amount: float | None,
    currency: str | None,
    assumptions: list[str],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """``(new_values, original_values, readable_changes)`` for the properties that really change."""
    new: dict[str, Any] = {}
    parts: list[str] = []
    if close_date is not None:
        current = common.to_date(props.get("closedate"))
        if current == close_date:
            assumptions.append(f"The close date is already {close_date.isoformat()}; it is not changed.")
        else:
            new["closedate"] = close_date.isoformat()
            parts.append(f"close date {current.isoformat() if current else 'empty'} to {close_date.isoformat()}")
    if amount is not None:
        current_amount = common.parse_float(props.get("amount"))
        current_currency = (props.get("deal_currency_code") or "").upper()
        wanted = _plain_number(amount)
        if current_amount is not None and current_amount == amount:
            assumptions.append(f"The amount is already {wanted}; it is not changed.")
        else:
            new["amount"] = wanted
            before = _plain_number(current_amount) if current_amount is not None else "empty"
            parts.append(f"amount {before} to {wanted}" + (f" {currency}" if currency else ""))
        home = (rules.home_currency or "").upper()
        if currency and currency != current_currency and not (not current_currency and currency == home):
            new["deal_currency_code"] = currency
            parts.append(f"currency {current_currency or 'empty'} to {currency}")
    original = {name: props.get(name) for name in new}
    return new, original, parts


async def build_changeset(
    lf: dict[str, Any],
    rules: SalesRules,
    *,
    deal_or_company: str,
    summary: str,
    outcome: str | None = None,
    close_date: date | None = None,
    amount: float | None = None,
    currency: str | None = None,
    tasks: list[TaskIn] | None = None,
    new_contacts: list[NewContactIn] | None = None,
    meeting_time: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Resolve, read, and propose. Returns the preview, or an ``ambiguous`` result. Writes nothing."""
    tasks, new_contacts = tasks or [], new_contacts or []
    currency = _check_inputs(summary, amount, currency, tasks, new_contacts)
    moment, defaulted = parse_meeting_time(meeting_time, rules, now)
    stamp = rules_stamp(rules, now)
    as_of = resolve_as_of(rules, now)
    assumptions: list[str] = []
    warnings: list[str] = []
    if defaulted:
        assumptions.append(f"No meeting_time given: the meeting is logged at {_iso_utc(moment)} (as_of {as_of.isoformat()}).")
    elif moment.astimezone(ZoneInfo(rules.timezone)).date() > as_of:
        warnings.append(f"meeting_time is after as_of {as_of.isoformat()}; check the date.")

    dctx = await common.deal_context(lf, rules)
    deal, candidates, how = await resolve_deal(lf, dctx, deal_or_company)
    if deal is None:
        return await _ambiguous(lf, dctx, deal_or_company, candidates, stamp)

    deal_id = str(deal["id"])
    props = deal.get("properties") or {}
    deal_name = _name(deal, "dealname") or f"deal {deal_id}"
    line = common.deal_line(deal, dctx)
    if how not in ("id", "exact deal name"):
        assumptions.append(f"{deal_or_company!r} resolved to {how}: {deal_name} (id {deal_id}).")
    if not _is_open(deal, dctx.pipelines):
        warnings.append(f"{deal_name} is closed ({line['stage']}); the meeting is logged on it anyway.")

    company, company_warning = await _deal_company(lf, deal_id)
    if company_warning:
        warnings.append(company_warning)
    company_id, company_name = company if company else (None, None)

    deal_contacts = (await common.batch_read_associations(lf, "deals", "contacts", [deal_id])).get(deal_id, [])
    contact_id: str | None = None
    contact_label: str | None = None
    if len(deal_contacts) == 1:
        contact_id = deal_contacts[0]
        contact_label = await _contact_name(lf, contact_id)
    elif len(deal_contacts) > 1:
        warnings.append(
            f"The deal has {len(deal_contacts)} contacts, so the meeting and note are not associated with one of them. "
            "Associate a contact afterwards, or tell me which one."
        )
    else:
        assumptions.append("The deal has no associated contact; the meeting and note go on the deal and company only.")

    owner_id = await _engagement_owner(lf, rules, props, warnings)
    ops: list[dict[str, Any]] = []
    original_values: dict[str, dict[str, Any]] = {}

    new_values, original, parts = _deal_update(props, rules, close_date, amount, currency, assumptions)
    if new_values:
        original_values[f"deals:{deal_id}"] = original
        ops.append(
            {
                "op": "update",
                "object_type": "deals",
                "id": deal_id,
                "properties": new_values,
                "summary": f"Update deal {deal_name}: {'; '.join(parts)}",
            }
        )

    when = _iso_utc(moment)
    day = moment.astimezone(ZoneInfo(rules.timezone)).date().isoformat()
    attached = ", ".join(
        x
        for x in (
            f"deal {deal_name}",
            f"company {company_name}" if company_name else None,
            f"contact {contact_label}" if contact_label else None,
        )
        if x
    )
    meeting_props: dict[str, Any] = {
        "hs_timestamp": when,
        "hs_meeting_title": f"Meeting: {deal_name}",
        "hs_meeting_body": _html(summary),
        "hs_meeting_start_time": when,
        "hs_meeting_outcome": MEETING_OUTCOME,
    }
    note_parts = ([f"Outcome: {outcome.strip()}"] if outcome and outcome.strip() else []) + [summary.strip()]
    if tasks:
        note_parts.append("Follow-ups:\n" + "\n".join(f"- {t.subject} (due {t.due_date.isoformat()})" for t in tasks))
    note_props: dict[str, Any] = {"hs_timestamp": when, "hs_note_body": _html("\n\n".join(note_parts))}
    if owner_id:
        meeting_props["hubspot_owner_id"] = owner_id
        note_props["hubspot_owner_id"] = owner_id
    ops.append(
        {
            "op": "create",
            "object_type": "meetings",
            "properties": meeting_props,
            "associations": _targets(deal_id, company_id, contact_id),
            "summary": f"Log meeting on {day} ({MEETING_OUTCOME.lower()}) attached to {attached}: {_short(summary)}",
        }
    )
    ops.append(
        {
            "op": "create",
            "object_type": "notes",
            "properties": note_props,
            "associations": _targets(deal_id, company_id, contact_id),
            "summary": f"Add note attached to {attached}"
            + (f", outcome: {_short(outcome, 60)}" if outcome and outcome.strip() else ""),
        }
    )
    for task in tasks:
        task_props: dict[str, Any] = {
            "hs_task_subject": task.subject.strip(),
            "hs_task_status": "NOT_STARTED",
            "hs_task_type": "TODO",
            "hs_timestamp": _iso_utc(_local_noon(task.due_date, rules.timezone)),
        }
        if owner_id:
            task_props["hubspot_owner_id"] = owner_id
        ops.append(
            {
                "op": "create",
                "object_type": "tasks",
                "properties": task_props,
                "associations": _targets(deal_id, company_id, None),
                "summary": f"Create task {task.subject.strip()!r} due {task.due_date.isoformat()} on deal {deal_name}"
                + (f" and company {company_name}" if company_name else ""),
            }
        )

    if new_contacts:
        ops.extend(
            await _contact_ops(lf, new_contacts, deal_id, deal_name, deal_contacts, company_id, company_name, assumptions)
        )

    preview = await changes.propose(
        lf,
        title=f"Log meeting on {deal_name}",
        ops=ops,
        original_values=original_values,
        rules_hash=stamp["rules_hash"],
    )
    result: dict[str, Any] = {
        **preview,
        **stamp,
        "deal": line,
        "company": {"id": company_id, "name": company_name} if company_id else None,
        "contact": {"id": contact_id, "name": contact_label} if contact_id else None,
        "meeting_time": when,
    }
    if assumptions:
        result["assumptions"] = assumptions
    if warnings:
        result["warnings"] = warnings
    return result


async def _engagement_owner(
    lf: dict[str, Any], rules: SalesRules, props: dict[str, Any], warnings: list[str]
) -> str | None:
    """The rep's owner id: the rules' default owner, else the deal's owner, else none."""
    try:
        owner = await common.resolve_owner(lf, rules, None)
    except ToolError as exc:
        warnings.append(f"The default owner in the sales rules could not be used ({exc}); falling back to the deal owner.")
        owner = None
    return owner or (str(props["hubspot_owner_id"]) if props.get("hubspot_owner_id") else None)


async def _contact_ops(
    lf: dict[str, Any],
    new_contacts: list[NewContactIn],
    deal_id: str,
    deal_name: str,
    deal_contacts: list[str],
    company_id: str | None,
    company_name: str | None,
    assumptions: list[str],
) -> list[dict[str, Any]]:
    company_contacts: list[dict[str, Any]] = []
    company_contact_ids: set[str] = set()
    if company_id:
        ids = (await common.batch_read_associations(lf, "companies", "contacts", [company_id])).get(company_id, [])
        company_contact_ids = set(ids)
        company_contacts = await common.batch_read(lf, "contacts", ids, _CONTACT_PROPERTIES)
    ops: list[dict[str, Any]] = []
    for contact in new_contacts:
        full = f"{contact.first_name.strip()} {contact.last_name.strip()}"
        title = f" ({contact.job_title.strip()})" if contact.job_title and contact.job_title.strip() else ""
        existing = await _existing_contact(lf, contact, company_contacts)
        if existing is None:
            props: dict[str, Any] = {"firstname": contact.first_name.strip(), "lastname": contact.last_name.strip()}
            if contact.email and contact.email.strip():
                props["email"] = contact.email.strip().lower()
            if contact.job_title and contact.job_title.strip():
                props["jobtitle"] = contact.job_title.strip()
            where = f" and company {company_name}" if company_name else ""
            ops.append(
                {
                    "op": "create",
                    "object_type": "contacts",
                    "properties": props,
                    "associations": _targets(deal_id, company_id, None),
                    "summary": f"Create contact {full}{title}, associated with deal {deal_name}{where}",
                }
            )
            continue
        existing_id = str(existing["id"])
        links: list[tuple[str, str, str]] = []
        if existing_id not in deal_contacts:
            links.append(("deals", deal_id, f"deal {deal_name}"))
        if company_id and existing_id not in company_contact_ids:
            links.append(("companies", company_id, f"company {company_name}"))
        assumptions.append(
            f"{full} already exists (contact {existing_id}); reusing it instead of creating a duplicate."
            + ("" if links else " It is already linked to the deal and company.")
        )
        for object_type, record_id, label in links:
            ops.append(
                {
                    "op": "associate",
                    "from": {"object_type": "contacts", "id": existing_id},
                    "to": {"object_type": object_type, "id": record_id},
                    "summary": f"Link existing contact {full} to {label}",
                }
            )
    return ops


# --- tool ------------------------------------------------------------------


async def pronova_log_meeting_outcome(
    ctx: Context,
    deal_or_company: Annotated[str, Field(description="Deal name, deal id, or company name or domain. A company must have exactly one open deal; otherwise candidates come back.")],
    summary: Annotated[str, Field(description="What happened in the meeting, in the rep's words. Saved on the meeting and the note.")],
    outcome: Annotated[str | None, Field(description="One line on the result, for example 'Agreed to a pilot'. Put at the top of the note.")] = None,
    close_date: Annotated[date | None, Field(description="New expected close date as ISO YYYY-MM-DD. Omit to leave it unchanged.")] = None,
    amount: Annotated[float | None, Field(description="New deal amount as a plain number, no symbol or 'k' (24000, not 24k). Omit to leave it unchanged.")] = None,
    currency: Annotated[str | None, Field(description="Three-letter currency code for the new amount, for example EUR. Needs amount.")] = None,
    tasks: Annotated[list[TaskIn] | None, Field(description="Follow-ups the rep owes. Each becomes a task on the deal and its company.")] = None,
    new_contacts: Annotated[list[NewContactIn] | None, Field(description="People met who are not in HubSpot yet. Each is linked to the company and the deal; known people are reused.")] = None,
    meeting_time: Annotated[str | None, Field(description="When it took place: ISO date or date-time, read in the portal timezone. Omit for the as_of date or now.")] = None,
) -> dict[str, Any]:
    lf = await common.session(ctx)
    try:
        rules = load_rules(lf["portal_id"])
    except RulesError as exc:
        raise ToolError(str(exc)) from exc
    return await build_changeset(
        lf,
        rules,
        deal_or_company=deal_or_company,
        summary=summary,
        outcome=outcome,
        close_date=close_date,
        amount=amount,
        currency=currency,
        tasks=tasks,
        new_contacts=new_contacts,
        meeting_time=meeting_time,
    )


TOOLS: list[tuple[str, object, str]] = common.guard_tools(
    [
        (
            "pronova_log_meeting_outcome",
            pronova_log_meeting_outcome,
            "Record a finished sales meeting in one step: meeting and note on the deal, a new close date "
            "or amount, follow-up tasks, and new contacts. Dates are ISO; resolve 'Friday' to a date first. "
            "Returns a preview (confirmation_id, change_count, one line per change) or, when the name fits "
            "several deals, candidates to choose from. Nothing is written until pronova_apply_changes.",
        ),
    ]
)
