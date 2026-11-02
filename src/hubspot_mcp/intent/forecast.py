"""``pronova_forecast_period``: the revenue number for a period, computed on the server.

Asked "what will I close this quarter?", a model that reads the deals itself sums
them in its head, picks its own probability, and gets a different total each run.
This tool reads the owner's deals once and :func:`compute_forecast` does the
arithmetic with ``Decimal``, so the same deals always give the same cents.

Figures follow ``hubspot-forecast/shared/rules.md``:

* Closed won: closed-won deals whose close date is in the period.
* Commit / Best case / Pipeline: open in-period deals by forecast category
  (Best case includes Commit; Pipeline is everything except Omit).
* Weighted: amount x probability, where probability is the rules' stage override,
  else ``hs_forecast_probability``, else ``hs_deal_stage_probability``, else the
  stage's own probability. When every probability is 0 or 1 the weighted figure is
  the open deal value and ``probability_mode`` says ``unweighted``.
* Supported commit: Commit minus the whole amount of every Commit deal flagged
  SILENT, OVERDUE, NO_NEXT, STALLED or INFLATED. SANDBAG never lowers it.
* Slipped: open deals whose close date is before the period start.

Currency: every figure is in the home currency using HubSpot's own
``amount_in_home_currency``. This module never converts. A deal in another
currency with no home amount is left out of the totals and named under
``excluded``; ``by_currency`` also gives each currency's own totals in its own
amounts, so EUR and USD deals are never mixed without HubSpot's rate.

The audit codes use the same definitions as ``pronova_triage_pipeline``.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from hubspot_mcp.intent import common
from hubspot_mcp.intent import rules as rules_mod
from hubspot_mcp.intent.common import PipelineIndex, Stage
from hubspot_mcp.intent.rules import SalesRules

# Codes that take a Commit deal out of supported commit. SANDBAG is an accuracy
# finding about the category, not about the deal closing, so it is not here.
SUPPORT_BREAKING = ("OVERDUE", "SILENT", "NO_NEXT", "STALLED", "INFLATED")
CODE_ORDER = ("OVERDUE", "SILENT", "NO_NEXT", "STALLED", "INFLATED", "SANDBAG")

CONCISE_CAP = 10
MOVERS_CONCISE = 5
MOVERS_DETAILED = 10
_CENT = Decimal("0.01")
_ZERO = Decimal(0)
_FIGURES = ("closed_won", "commit", "best_case", "pipeline", "weighted", "supported_commit")


# --- periods -----------------------------------------------------------------


def period_bounds(period: str, fiscal_year_start_month: int = 1) -> tuple[date, date]:
    """First and last day of ``2026-11``, ``2026-Q4`` or ``2026-11-15`` (a day is a one-day period).

    A quarter resolves against the fiscal year start. The fiscal year is named by
    the calendar year it ends in, so with an April start ``2027-Q1`` is April to
    June 2026, the way ``hubspot-forecast`` reads it. A period without a year is
    refused, never guessed.
    """
    text = period.strip() if isinstance(period, str) else ""
    month = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", text)
    if month:
        year, number = int(month.group(1)), int(month.group(2))
        return date(year, number, 1), date(year, number, calendar.monthrange(year, number)[1])
    quarter = re.fullmatch(r"(\d{4})-[Qq]([1-4])", text)
    if quarter:
        year, number = int(quarter.group(1)), int(quarter.group(2))
        first = fiscal_year_start_month if 1 <= fiscal_year_start_month <= 12 else 1
        offset = (first - 1) + (number - 1) * 3
        base = (year - 1 if first > 1 else year) * 12 + offset
        start_year, start_month = divmod(base, 12)
        end_year, end_month = divmod(base + 2, 12)
        return (
            date(start_year, start_month + 1, 1),
            date(end_year, end_month + 1, calendar.monthrange(end_year, end_month + 1)[1]),
        )
    day: date | None = None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            day = date.fromisoformat(text)
        except ValueError:
            day = None
    if day is None:
        raise ValueError(
            f"period must be a month (2026-11), a quarter (2026-Q4) or a day (2026-11-15) with the year; got {period!r}."
        )
    return day, day


# --- audit codes ---------------------------------------------------------------


def _reason(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _stage_probability(props: dict[str, Any], stage: Stage | None, rules: SalesRules) -> float | None:
    """The stage's probability for INFLATED and SANDBAG: the deal's own forecast probability does not count."""
    stage_only = {k: v for k, v in props.items() if k != "hs_forecast_probability"}
    return common.deal_probability(stage_only, stage, rules)


def audit_deal(props: dict[str, Any], stage: Stage | None, rules: SalesRules, as_of: date) -> list[dict[str, str]]:
    """Reason codes (code + detail) for one open in-period deal, in report order.

    SILENT, NO_NEXT, INFLATED and SANDBAG read the forecast category; a missing
    evidence value never fires a rule.
    """
    tz = rules.timezone
    label = stage.label if stage else None
    category = common.normalize_category(props.get("hs_manual_forecast_category"))
    fired: dict[str, dict[str, str]] = {}

    close = common.to_date(props.get("closedate"))
    if close is not None and close < as_of:
        fired["OVERDUE"] = _reason(
            "OVERDUE", f"overdue {common.days_between(as_of, close)} days (close date {close.isoformat()})"
        )

    contacted = common.to_date(props.get("notes_last_contacted"), tz)
    if contacted is not None and rules.silent_applies(category):
        days = common.days_between(as_of, contacted)
        if days > rules.silent_days:
            fired["SILENT"] = _reason(
                "SILENT", f"silent {days} days (last contact {contacted.isoformat()}, limit {rules.silent_days})"
            )

    if rules.no_next_applies(category):
        upcoming = common.to_date(props.get("notes_next_activity_date"), tz)
        scheduled = upcoming is not None and upcoming >= as_of
        has_next_step = bool(str(props.get("hs_next_step") or "").strip())
        if not scheduled and not has_next_step:
            fired["NO_NEXT"] = _reason(
                "NO_NEXT", f"no next activity scheduled on or after {as_of.isoformat()} and no next step"
            )

    entered = common.to_date(common.stage_entered(props), tz)
    if entered is not None:
        limit = rules.stalled_days_for(label)
        days = common.days_between(as_of, entered)
        if days > limit:
            fired["STALLED"] = _reason("STALLED", f"stalled {days} days in {label or 'its stage'} (limit {limit})")

    stage_p = _stage_probability(props, stage, rules)
    if stage_p is not None:
        if category == "COMMIT" and stage_p < rules.inflated_below:
            fired["INFLATED"] = _reason(
                "INFLATED",
                f"Commit at {label or 'its stage'} with stage probability {stage_p:.0%} (below {rules.inflated_below:.0%})",
            )
        if category == "PIPELINE" and stage_p > rules.sandbag_above:
            fired["SANDBAG"] = _reason(
                "SANDBAG",
                f"Pipeline at {label or 'its stage'} with stage probability {stage_p:.0%} (above {rules.sandbag_above:.0%})",
            )

    return [fired[c] for c in CODE_ORDER if c in fired]


# --- computation ---------------------------------------------------------------


def _dec(value: Any) -> Decimal | None:
    number = common.parse_float(value)
    return None if number is None else Decimal(str(number))


def _cents(value: Decimal) -> float:
    return float(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def _id_key(deal_id: str) -> tuple[int, int, str]:
    return (0, int(deal_id), "") if deal_id.isdigit() else (1, 0, deal_id)


def _flag(props: dict[str, Any], name: str) -> bool | None:
    """A HubSpot boolean property as a bool; ``None`` when HubSpot sent nothing."""
    value = props.get(name)
    if value is None or value == "":
        return None
    return value is True or str(value).strip().lower() == "true"


def _probability(props: dict[str, Any], stage: Stage | None, rules: SalesRules) -> tuple[float | None, str | None]:
    value = common.deal_probability(props, stage, rules)
    if value is None:
        return None, None
    if stage is not None and rules.probability_override(stage.label) is not None:
        return value, "rules"
    for name in ("hs_forecast_probability", "hs_deal_stage_probability"):
        if common.parse_float(props.get(name)) is not None:
            return value, name
    return value, "stage"


@dataclass
class Row:
    """One deal with everything the figures and the report need."""

    record: dict[str, Any]
    id: str
    category: str | None
    close: date | None
    amount: Decimal | None  # the deal's own amount, in its own currency
    currency: str | None  # the deal's own currency, the home currency when it carries none
    home: Decimal | None  # HubSpot's home-currency amount; None when it cannot be had without inventing FX
    probability: float | None = None
    probability_source: str | None = None
    codes: list[dict[str, str]] = field(default_factory=list)

    @property
    def code_names(self) -> set[str]:
        return {c["code"] for c in self.codes}

    @property
    def supported(self) -> bool:
        return self.category == "COMMIT" and not self.code_names.intersection(SUPPORT_BREAKING)


@dataclass
class Forecast:
    period: str
    start: date
    end: date
    as_of: date
    currency: str | None
    probability_mode: str
    probability_sources: list[str]
    figures: dict[str, float]
    by_currency: dict[str, dict[str, float]]
    currency_notes: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    findings: dict[str, int]
    counts: dict[str, int]
    won: list[Row]
    open: list[Row]  # open deals in the period, largest first
    slipped: list[Row]  # open deals dated before the period start
    outside: dict[str, Any]
    warnings: list[str]
    unavailable: str | None = None  # why no figures were made; set only when nothing can be summed
    currencies: list[str] = field(default_factory=list)

    def movers(self, limit: int) -> list[Row]:
        """The open in-period deals whose loss or slip would move the number most: largest home amount first.

        Losing or slipping one takes its amount out of Pipeline, and out of
        supported commit too when it is a supported Commit deal.
        """
        pool = [r for r in self.open if r.category != "OMIT" and r.home is not None]
        pool.sort(key=lambda r: (-(r.home or _ZERO), _id_key(r.id)))
        return pool[:limit]


def _home_currency(rules: SalesRules, deals: list[dict[str, Any]]) -> tuple[str | None, list[str]]:
    """The currency figures are reported in, with a warning when it had to be inferred."""
    if rules.home_currency:
        return rules.home_currency.upper(), []
    seen = sorted(
        {str(d["properties"]["deal_currency_code"]).upper() for d in deals if d["properties"].get("deal_currency_code")}
    )
    if len(seen) == 1:
        return seen[0], [f"home_currency is not set in the sales rules; figures are in {seen[0]}, the only currency on these deals."]
    return None, []


def _mixed_currencies(rules: SalesRules, deals: list[dict[str, Any]]) -> list[str]:
    """The currencies on ``deals`` when the rules name no home currency and there are two or more.

    hubspot-forecast's rules.md: with no known company currency and two or more currencies on the
    deals the forecast is unavailable, because nothing can be summed.
    """
    if rules.home_currency:
        return []
    seen = sorted(
        {str(d["properties"]["deal_currency_code"]).upper() for d in deals if d["properties"].get("deal_currency_code")}
    )
    return seen if len(seen) > 1 else []


def _unavailable(period: str, start: date, end: date, as_of: date, currencies: list[str]) -> Forecast:
    reason = (
        f"home_currency is not set in the sales rules and the deals carry {len(currencies)} currencies "
        f"({', '.join(currencies)}), which cannot be summed without it. Set home_currency in the sales rules."
    )
    return Forecast(
        period=period, start=start, end=end, as_of=as_of, currency=None, probability_mode="unweighted",
        probability_sources=[], figures={}, by_currency={}, currency_notes=[], excluded=[], findings={},
        counts={}, won=[], open=[], slipped=[], outside={}, warnings=[reason], unavailable=reason,
        currencies=currencies,
    )


def compute_forecast(
    deals: list[dict[str, Any]],
    rules: SalesRules,
    *,
    period: str,
    as_of: date | None = None,
    pipelines: PipelineIndex | None = None,
) -> Forecast:
    """The forecast figures for ``period`` from raw HubSpot deal records.

    ``deals`` may mix open deals (any close date), closed-won deals and closed-lost
    deals; the first two are used and closed-lost deals are ignored. No I/O, no
    clock: ``as_of`` defaults to the rules' as-of date.
    """
    start, end = period_bounds(period, rules.fiscal_year_start_month)
    as_of = as_of or rules_mod.resolve_as_of(rules)
    pipelines = pipelines or PipelineIndex()

    unique: dict[str, dict[str, Any]] = {}
    for deal in deals:
        deal_id = str(deal.get("id") or (deal.get("properties") or {}).get("hs_object_id") or "")
        unique.setdefault(deal_id, {**deal, "id": deal_id, "properties": deal.get("properties") or {}})

    def in_period(deal: dict[str, Any]) -> bool:
        close = common.to_date(deal["properties"].get("closedate"))
        return close is not None and start <= close <= end

    won_raw: list[tuple[dict[str, Any], Stage | None]] = []
    open_raw: list[tuple[dict[str, Any], Stage | None]] = []
    for deal in unique.values():
        props = deal["properties"]
        stage = pipelines.stage(props.get("dealstage"))
        closed = _flag(props, "hs_is_closed")
        if closed is None:
            closed = stage.is_closed if stage else False
        if not closed:
            open_raw.append((deal, stage))
            continue
        won = _flag(props, "hs_is_closed_won")
        if won is None:
            won = stage.is_won if stage else False
        if won and in_period(deal):
            won_raw.append((deal, stage))

    selected = [d for d, _ in won_raw] + [d for d, _ in open_raw if in_period(d)]
    mixed = _mixed_currencies(rules, selected)
    if mixed:
        return _unavailable(period, start, end, as_of, mixed)
    home, warnings = _home_currency(rules, selected)
    eff_rules = rules.model_copy(update={"home_currency": home}) if home != rules.home_currency else rules

    def build(deal: dict[str, Any]) -> Row:
        props = deal["properties"]
        home_amount = common.amount_home(props, eff_rules)
        if home_amount is None and _dec(props.get("amount")) is None:
            home_amount = 0.0  # a blank amount is worth 0 in every currency; it is not an exchange-rate gap
        return Row(
            record=deal,
            id=deal["id"],
            category=common.normalize_category(props.get("hs_manual_forecast_category")),
            close=common.to_date(props.get("closedate")),
            amount=_dec(props.get("amount")),
            currency=(props.get("deal_currency_code") or "").upper() or home,
            home=None if home_amount is None else Decimal(str(home_amount)),
        )

    won_rows = [build(d) for d, _ in won_raw]
    open_rows: list[Row] = []
    slipped: list[Row] = []
    outside_rows: list[Row] = []
    for deal, stage in open_raw:
        row = build(deal)
        if in_period(deal):
            row.probability, row.probability_source = _probability(deal["properties"], stage, rules)
            row.codes = audit_deal(deal["properties"], stage, rules, as_of)
            open_rows.append(row)
        else:
            outside_rows.append(row)
            if row.close is not None and row.close < start:
                slipped.append(row)
    for rows in (won_rows, open_rows, slipped, outside_rows):
        rows.sort(key=lambda r: (-(r.home or _ZERO), _id_key(r.id)))

    counted = [r for r in open_rows if r.category != "OMIT"]
    used = [r.probability for r in counted if r.probability is not None]
    unweighted = not used or all(p in (0.0, 1.0) for p in used)

    def weight(amount: Decimal | None, row: Row) -> Decimal:
        if amount is None:
            return _ZERO
        return amount if unweighted else amount * Decimal(str(row.probability or 0))

    totals = dict.fromkeys(_FIGURES, _ZERO)
    native: dict[str, dict[str, Decimal]] = {}

    def bucket(row: Row) -> dict[str, Decimal]:
        return native.setdefault(row.currency or "unknown", dict.fromkeys((*_FIGURES, "deals"), _ZERO))

    def add(figure: str, row: Row) -> None:
        totals[figure] += row.home or _ZERO
        bucket(row)[figure] += row.amount or _ZERO

    for row in won_rows:
        add("closed_won", row)
        bucket(row)["deals"] += 1
    for row in open_rows:
        if row.category == "COMMIT":
            add("commit", row)
            if row.supported:
                add("supported_commit", row)
        if row.category in ("COMMIT", "BEST_CASE"):
            add("best_case", row)
    for row in counted:
        add("pipeline", row)
        totals["weighted"] += weight(row.home, row)
        bucket(row)["weighted"] += weight(row.amount, row)
        bucket(row)["deals"] += 1

    figures = {name: _cents(value) for name, value in totals.items()}
    figures["challenged_commit"] = _cents(totals["commit"] - totals["supported_commit"])
    by_currency = {
        cur: {k: (int(v) if k == "deals" else _cents(v)) for k, v in vals.items()} for cur, vals in sorted(native.items())
    }

    excluded: list[dict[str, Any]] = []
    converted: dict[str, dict[str, Decimal]] = {}
    for kind, rows in (("closed_won", won_rows), ("open", counted)):
        for row in rows:
            if row.home is None:
                excluded.append(
                    {
                        "id": row.id,
                        "name": row.record["properties"].get("dealname"),
                        "kind": kind,
                        "currency": row.currency,
                        "amount": _cents(row.amount) if row.amount is not None else None,
                    }
                )
            elif home and row.currency and row.currency != home and row.amount is not None:
                note = converted.setdefault(row.currency, {"count": _ZERO, "original": _ZERO, "home": _ZERO})
                note["count"] += 1
                note["original"] += row.amount or _ZERO
                note["home"] += row.home
    notes: list[dict[str, Any]] = [
        {
            "currency": cur,
            "deals": int(n["count"]),
            "original_amount": _cents(n["original"]),
            "home_amount": _cents(n["home"]),
            "text": f"{int(n['count'])} deal(s) in {cur} counted at HubSpot's home-currency amount; no rate is assumed.",
        }
        for cur, n in sorted(converted.items())
    ]
    for item in excluded:
        warnings.append(
            f"Deal {item['id']} ({item['currency'] or 'no currency'}) has no home-currency amount from HubSpot; "
            "it is left out of the totals and no exchange rate is assumed."
        )

    outside_value = sum((r.home or _ZERO for r in outside_rows), _ZERO)
    count = len(outside_rows)
    outside: dict[str, Any] = {
        "count": count,
        "value": _cents(outside_value),
        "before_period": len(slipped),
        "after_period": sum(1 for r in outside_rows if r.close is not None and r.close > end),
        "no_close_date": sum(1 for r in outside_rows if r.close is None),
        "without_home_amount": sum(1 for r in outside_rows if r.home is None),
    }
    label = f"{home} " if home else ""
    outside["line"] = (
        f"{count} open deal{'s' if count != 1 else ''} ({label}{outside['value']:,.2f}) "
        f"{'have' if count != 1 else 'has'} close dates outside {period} or none, so "
        f"{'they are' if count != 1 else 'it is'} not in this forecast."
    )

    counts = {
        "closed_won": len(won_rows),
        "open_in_period": len(open_rows),
        "commit": sum(1 for r in open_rows if r.category == "COMMIT"),
        "best_case": sum(1 for r in open_rows if r.category == "BEST_CASE"),
        "pipeline_counted": len(counted),
        "unsupported_commit": sum(1 for r in open_rows if r.category == "COMMIT" and not r.supported),
        "slipped": len(slipped),
        "out_of_period": count,
    }
    return Forecast(
        period=period,
        start=start,
        end=end,
        as_of=as_of,
        currency=home,
        probability_mode="unweighted" if unweighted else "weighted",
        probability_sources=sorted({r.probability_source for r in counted if r.probability_source}),
        figures=figures,
        by_currency=by_currency,
        currency_notes=notes,
        excluded=excluded,
        findings={code: sum(1 for r in open_rows if code in r.code_names) for code in CODE_ORDER},
        counts=counts,
        won=won_rows,
        open=open_rows,
        slipped=slipped,
        outside=outside,
        warnings=warnings,
    )


# --- tool --------------------------------------------------------------------


def _lines(rows: list[Row], dctx: common.DealContext, response_format: str, cap: int | None) -> dict[str, Any]:
    shown = rows if cap is None else rows[:cap]
    out: dict[str, Any] = {
        "deals": [common.deal_line(r.record, dctx, reasons=r.codes, response_format=response_format) for r in shown]
    }
    if len(shown) < len(rows):
        out["omitted"] = len(rows) - len(shown)
    return out


def render_forecast(
    fc: Forecast, dctx: common.DealContext, *, owner: str | None, response_format: str, truncated: bool
) -> dict[str, Any]:
    if fc.unavailable:
        return {
            **rules_mod.rules_stamp(dctx.rules),
            "as_of": fc.as_of.isoformat(),
            "status": "data_unavailable",
            "period": fc.period,
            "period_start": fc.start.isoformat(),
            "period_end": fc.end.isoformat(),
            "owner": owner or "all owners",
            "reason": fc.unavailable,
            "currencies": fc.currencies,
            "warnings": list(fc.warnings),
        }
    cap = CONCISE_CAP if response_format == "concise" else None
    movers = fc.movers(MOVERS_CONCISE if response_format == "concise" else MOVERS_DETAILED)
    unsupported = [r for r in fc.open if r.category == "COMMIT" and not r.supported]
    result: dict[str, Any] = {
        **rules_mod.rules_stamp(dctx.rules),
        "as_of": fc.as_of.isoformat(),
        "period": fc.period,
        "period_start": fc.start.isoformat(),
        "period_end": fc.end.isoformat(),
        "owner": owner or "all owners",
        "currency": fc.currency,
        "figures": fc.figures,
        "probability_mode": fc.probability_mode,
        "probability_sources": fc.probability_sources,
        "by_currency": fc.by_currency,
        "counts": fc.counts,
        "findings": {k: v for k, v in fc.findings.items() if v},
        "unsupported_commit": _lines(unsupported, dctx, response_format, cap),
        "inflated": _lines([r for r in fc.open if "INFLATED" in r.code_names], dctx, response_format, cap),
        "sandbag": _lines([r for r in fc.open if "SANDBAG" in r.code_names], dctx, response_format, cap),
        "slipped": {"count": len(fc.slipped), **_lines(fc.slipped, dctx, response_format, cap)},
        "out_of_period": fc.outside,
        "movers": _lines(movers, dctx, response_format, None)["deals"],
        "currency_notes": fc.currency_notes,
        "excluded": fc.excluded,
        "warnings": list(fc.warnings),
    }
    if truncated:
        result["warnings"].append(
            "HubSpot Search returned its 10,000-result cap, so deals are missing and the figures are incomplete."
        )
    if response_format == "detailed":
        result["closed_won_deals"] = _lines(fc.won, dctx, response_format, None)["deals"]
        result["open_in_period"] = _lines(fc.open, dctx, response_format, None)["deals"]
    return result


async def pronova_forecast_period(
    ctx: Context,
    period: Annotated[
        str,
        Field(description="Forecast period with the year: a quarter 2026-Q4 (fiscal per the rules), a month 2026-11, or a day 2026-11-15."),
    ],
    owner: Annotated[
        str | None,
        Field(description="Owner's email, name or id; 'me' or omitted means the default owner in the sales rules, none set means all owners."),
    ] = None,
    response_format: common.ResponseFormatParam = "concise",
) -> dict[str, Any]:
    lf = await common.session(ctx)
    try:
        rules = rules_mod.load_rules(lf["portal_id"])
        start, end = period_bounds(period, rules.fiscal_year_start_month)
    except ValueError as exc:  # RulesError is a ValueError
        raise ToolError(str(exc)) from exc
    owner_id = await common.resolve_owner(lf, rules, owner)

    open_deals = await common.search_deals(lf, owner_id=owner_id, open_only=True)
    won_filters = common.deal_filter_groups(owner_id=owner_id, open_only=False, closedate_from=start, closedate_to=end)
    won_filters[0]["filters"].append(common.prop_filter("hs_is_closed_won", "EQ", "true"))
    won_deals = await common.search_all(lf, "deals", filter_groups=won_filters, properties=common.DEAL_PROPERTIES)

    dctx = await common.deal_context(lf, rules)
    fc = compute_forecast([*open_deals.results, *won_deals.results], rules, period=period, pipelines=dctx.pipelines)
    return render_forecast(
        fc,
        dctx,
        owner=dctx.owners.display(owner_id),
        response_format=response_format,
        truncated=open_deals.truncated or won_deals.truncated,
    )


TOOLS: list[tuple[str, object, str]] = common.guard_tools(
    [
        (
            "pronova_forecast_period",
            pronova_forecast_period,
            "Compute the revenue forecast for a quarter, month or day for one owner, using the sales rules. "
            "Use it for 'what will I close', 'are we on track' or 'what is at risk'. Returns closed won, "
            "commit, best case, pipeline, weighted and supported commit in the home currency, each currency's "
            "own totals, deals flagged INFLATED or SANDBAG, slipped close dates, deals outside the period, "
            "and the movers (deals whose loss or slip changes the number most).",
        ),
    ]
)
