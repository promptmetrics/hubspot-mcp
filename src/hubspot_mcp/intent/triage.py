"""``pronova_triage_pipeline``: which open deals need attention, in what order, and why.

A rep asking "what needs attention?" otherwise makes the model read every open
deal and apply the rules in its head, a different way each run. This tool reads
the owner's open deals once, tests four rules against the portal's sales rules
(``intent/rules``, the same thresholds ``pronova_get_sales_rules`` renders), and
returns them ranked.

The rules mirror ``hubspot-forecast/shared/rules.md``:

* OVERDUE: close date before the as-of date.
* SILENT: last completed contact more than ``silent_days`` before the as-of date.
* NO_NEXT: no scheduled next activity dated on or after the as-of date and an empty next step.
* STALLED: in the current stage more than that stage's ``stalled_days``.

SILENT and NO_NEXT apply only to the forecast categories in the rules' scope. A
missing evidence value never fires a rule. Ranking is deterministic: the sum of
the weights of a deal's codes times its amount in the home currency, ties broken
by deal id, so two runs over the same data return the same order.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from hubspot_mcp.intent import common
from hubspot_mcp.intent import rules as rules_mod
from hubspot_mcp.intent.rules import SalesRules

# Report order inside one deal's reasons, most urgent first.
_CODE_ORDER = ("OVERDUE", "SILENT", "NO_NEXT", "STALLED")
DEFAULT_LIMIT = 30
MAX_LIMIT = 200


def _reason(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def evaluate_deal(
    props: dict[str, Any], stage_label: str | None, rules: SalesRules, as_of: date
) -> list[dict[str, str]]:
    """Reason codes that fire for one open deal. Empty means the deal is clean."""
    tz = rules.timezone
    category = common.normalize_category(props.get("hs_forecast_category"))
    fired: dict[str, dict[str, str]] = {}

    # closedate is midnight UTC of the picked day, so it reads as a UTC date.
    close = common.to_date(props.get("closedate"))
    if close is not None and close < as_of:
        days = common.days_between(as_of, close)
        fired["OVERDUE"] = _reason("OVERDUE", f"overdue {days} days (close date {close.isoformat()})")

    contacted = common.to_date(props.get("notes_last_contacted"), tz)
    if contacted is not None and rules.silent_applies(category):
        days = common.days_between(as_of, contacted)
        if days > rules.silent_days:
            fired["SILENT"] = _reason(
                "SILENT", f"silent {days} days (last contact {contacted.isoformat()}, limit {rules.silent_days})"
            )

    if rules.no_next_applies(category):
        next_activity = common.to_date(props.get("notes_next_activity_date"), tz)
        scheduled = next_activity is not None and next_activity >= as_of
        has_next_step = bool(str(props.get("hs_next_step") or "").strip())
        if not scheduled and not has_next_step:
            fired["NO_NEXT"] = _reason(
                "NO_NEXT", f"no next activity scheduled on or after {as_of.isoformat()} and no next step"
            )

    entered = common.to_date(common.stage_entered(props), tz)
    if entered is not None:
        limit = rules.stalled_days_for(stage_label)
        days = common.days_between(as_of, entered)
        if days > limit:
            where = stage_label or "its stage"
            fired["STALLED"] = _reason("STALLED", f"stalled {days} days in {where} (limit {limit})")

    return [fired[c] for c in _CODE_ORDER if c in fired and c in rules.triage_codes]


def urgency_score(reasons: list[dict[str, str]], amount_home: float | None, rules: SalesRules) -> float:
    weight = sum(rules.triage_weights[r["code"]] for r in reasons)
    return weight * (amount_home or 0.0)


def _id_key(deal_id: str) -> tuple[int, int, str]:
    return (0, int(deal_id), "") if deal_id.isdigit() else (1, 0, deal_id)


def triage_deals(
    deals: list[dict[str, Any]], dctx: common.DealContext, rules: SalesRules, as_of: date
) -> list[dict[str, Any]]:
    """Flagged deals, ranked. Each entry holds the raw deal, its reasons and its score."""
    flagged: list[dict[str, Any]] = []
    for deal in deals:
        props = deal.get("properties") or {}
        stage = dctx.pipelines.stage(props.get("dealstage"))
        if stage is not None and stage.is_closed:
            continue
        reasons = evaluate_deal(props, stage.label if stage else None, rules, as_of)
        if not reasons:
            continue
        score = urgency_score(reasons, common.amount_home(props, rules), rules)
        flagged.append({"deal": deal, "reasons": reasons, "score": score})
    flagged.sort(key=lambda f: (-f["score"], _id_key(str(f["deal"].get("id") or ""))))
    return flagged


def _parse_as_of(value: str | None, rules: SalesRules) -> tuple[date, str]:
    if value is None or not str(value).strip():
        source = "rules" if rules.as_of is not None else "today"
        return rules_mod.resolve_as_of(rules), source
    try:
        return date.fromisoformat(str(value).strip()), "argument"
    except ValueError:
        raise ToolError(f"as_of must be an ISO date like 2026-10-30, got {value!r}.") from None


async def pronova_triage_pipeline(
    ctx: Context,
    owner: Annotated[
        str | None,
        Field(description="Owner's email, name or id; 'me' or omitted means the default owner in the sales rules."),
    ] = None,
    as_of: Annotated[
        str | None,
        Field(description="ISO date (YYYY-MM-DD) to count days from; omitted uses the rules' as-of date, else today."),
    ] = None,
    limit: Annotated[
        int, Field(description=f"Most flagged deals to list, 1 to {MAX_LIMIT}; the counts always cover all of them.")
    ] = DEFAULT_LIMIT,
    response_format: common.ResponseFormatParam = "concise",
) -> dict[str, Any]:
    if not 1 <= limit <= MAX_LIMIT:
        raise ToolError(f"limit must be between 1 and {MAX_LIMIT}.")
    lf = await common.session(ctx)
    rules = rules_mod.load_rules(lf["portal_id"])
    as_of_date, as_of_source = _parse_as_of(as_of, rules)
    owner_id = await common.resolve_owner(lf, rules, owner)
    found = await common.search_deals(lf, owner_id=owner_id, open_only=True)
    dctx = await common.deal_context(lf, rules)

    flagged = triage_deals(found.results, dctx, rules, as_of_date)
    by_code: dict[str, int] = {}
    for item in flagged:
        for reason in item["reasons"]:
            by_code[reason["code"]] = by_code.get(reason["code"], 0) + 1

    lines = []
    for item in flagged[:limit]:
        line = common.deal_line(item["deal"], dctx, reasons=item["reasons"], response_format=response_format)
        line["urgency_score"] = round(item["score"], 2)
        lines.append(line)

    result: dict[str, Any] = {
        "as_of": as_of_date.isoformat(),
        "as_of_source": as_of_source,
        "rules_hash": rules_mod.rules_hash(rules),
        "owner": dctx.owners.display(owner_id) if owner_id else "all owners",
        "codes_checked": list(rules.triage_codes),
        "open_deals_checked": len(found.results),
        "flagged": len(flagged),
        "by_code": {c: by_code[c] for c in _CODE_ORDER if c in by_code},
        "shown": len(lines),
        "deals": lines,
    }
    if len(flagged) > len(lines):
        result["omitted"] = len(flagged) - len(lines)
    unconverted = sum(1 for f in flagged if common.amount_home(f["deal"].get("properties") or {}, rules) is None)
    if unconverted:
        result["flagged_without_home_amount"] = unconverted
    if found.truncated:
        result["search_truncated"] = True
        result["note"] = (
            f"HubSpot Search returns at most {common.SEARCH_RESULT_CAP} deals; only the first "
            f"{len(found.results)} open deals were checked. Narrow by owner."
        )
    return result


TOOLS: list[tuple[str, object, str]] = common.guard_tools(
    [
        (
            "pronova_triage_pipeline",
            pronova_triage_pipeline,
            "Rank an owner's open deals by what needs attention. Flags OVERDUE (close date passed), "
            "SILENT (no recent contact), NO_NEXT (no scheduled next activity or next step) and STALLED "
            "(too long in the stage) using the portal's sales rules. owner defaults to the rules' default "
            "owner; as_of is an ISO date to count days from. Returns flagged deals ranked by urgency "
            "score, each with reason codes and details, plus counts by code and the as-of date.",
        )
    ]
)
