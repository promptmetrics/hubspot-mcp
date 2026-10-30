"""Sales rules for the intent tools: one YAML file, re-read on every call.

Triage, forecast and the benchmark's ``sales-rules.md`` all need the same
thresholds (what "silent" or "stalled" means). Keeping them in one per-portal
YAML means an operator edits a number once, the next tool call uses it, and the
text handed to the benchmark arms is rendered from the file the tools run on, so
no arm is graded against a rule it was not told.

The definitions mirror ``hubspot-forecast/shared/rules.md``. This module holds
thresholds and scopes only; the tools apply them.

Nothing is cached: :func:`load_rules` reads the file each time (gap-table row 6,
"rules editable live").
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

RULES_DIR_ENV = "HUBSPOT_MCP_RULES_DIR"
BUNDLED_DIR = Path(__file__).resolve().parent / "rules"

CATEGORIES = ("COMMIT", "BEST_CASE", "PIPELINE", "OMIT")
CODES = ("OVERDUE", "NO_NEXT", "SILENT", "STALLED", "INFLATED", "SANDBAG")

_PORTAL_RE = re.compile(r"[0-9]+")


class RulesError(ValueError):
    """The rules file is missing, unreadable, or fails validation. The message names the file."""


def normalize_category(raw: str | None) -> str | None:
    """``"Best case"`` -> ``"BEST_CASE"``; HubSpot labels and internal values both map."""
    if raw is None:
        return None
    text = re.sub(r"[\s\-]+", "_", str(raw).strip()).upper()
    return text or None


class StalledDays(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: int = Field(default=30, ge=0)
    by_stage: dict[str, int] = Field(default_factory=dict)

    @field_validator("by_stage")
    @classmethod
    def _non_negative(cls, value: dict[str, int]) -> dict[str, int]:
        for label, days in value.items():
            if days < 0:
                raise ValueError(f"stalled_days.by_stage[{label!r}] must be >= 0")
        return value


class SalesRules(BaseModel):
    # extra="forbid": a typo in a live-edited file ("silent_day: 7") must fail
    # loudly, not silently fall back to the default and give a wrong answer.
    model_config = ConfigDict(extra="forbid")

    as_of: date | None = None
    timezone: str = "Europe/Berlin"
    default_owner: str | None = None
    home_currency: str | None = None
    silent_days: int = Field(14, ge=0)
    stalled_days: StalledDays = Field(default_factory=lambda: StalledDays())
    at_risk_window_days: int = Field(14, ge=0)
    inflated_below: float = Field(0.4, ge=0, le=1)
    sandbag_above: float = Field(0.8, ge=0, le=1)
    stage_probabilities: dict[str, float] = Field(default_factory=dict)
    triage_codes: list[str] = Field(default_factory=lambda: ["OVERDUE", "NO_NEXT", "SILENT", "STALLED"])
    triage_weights: dict[str, float] = Field(
        default_factory=lambda: {"OVERDUE": 4.0, "SILENT": 3.0, "NO_NEXT": 2.0, "STALLED": 1.0}
    )
    silent_scope: list[str] = Field(default_factory=lambda: ["COMMIT", "BEST_CASE"])
    no_next_scope: list[str] = Field(default_factory=lambda: ["COMMIT", "BEST_CASE"])
    fiscal_year_start_month: int = Field(1, ge=1, le=12)
    bulk_write_batch_size: int = Field(100, ge=1, le=100)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @field_validator("stage_probabilities")
    @classmethod
    def _probabilities(cls, value: dict[str, float]) -> dict[str, float]:
        for label, p in value.items():
            if not 0 <= p <= 1:
                raise ValueError(f"stage_probabilities[{label!r}] must be between 0 and 1, got {p}")
        return value

    @field_validator("silent_scope", "no_next_scope")
    @classmethod
    def _categories(cls, value: list[str]) -> list[str]:
        out = [normalize_category(v) or "" for v in value]
        bad = [v for v in out if v not in CATEGORIES]
        if bad:
            raise ValueError(f"unknown forecast categories {bad}; use {list(CATEGORIES)}")
        return out

    @field_validator("triage_codes")
    @classmethod
    def _codes(cls, value: list[str]) -> list[str]:
        out = [str(v).strip().upper() for v in value]
        bad = [v for v in out if v not in CODES]
        if bad:
            raise ValueError(f"unknown reason codes {bad}; use {list(CODES)}")
        return out

    @model_validator(mode="after")
    def _weights_cover_codes(self) -> SalesRules:
        self.triage_weights = {str(k).upper(): float(v) for k, v in self.triage_weights.items()}
        missing = [c for c in self.triage_codes if c not in self.triage_weights]
        if missing:
            raise ValueError(f"triage_weights has no weight for {missing}")
        return self

    # --- lookups the tools share, so label matching is defined once ----------

    def stalled_days_for(self, stage_label: str | None) -> int:
        if stage_label:
            wanted = stage_label.strip().casefold()
            for label, days in self.stalled_days.by_stage.items():
                if label.strip().casefold() == wanted:
                    return days
        return self.stalled_days.default

    def probability_override(self, stage_label: str | None) -> float | None:
        if not stage_label:
            return None
        wanted = stage_label.strip().casefold()
        for label, p in self.stage_probabilities.items():
            if label.strip().casefold() == wanted:
                return p
        return None

    def silent_applies(self, category: str | None) -> bool:
        return normalize_category(category) in self.silent_scope

    def no_next_applies(self, category: str | None) -> bool:
        return normalize_category(category) in self.no_next_scope


def _rules_dir() -> Path | None:
    raw = os.environ.get(RULES_DIR_ENV, "").strip()
    return Path(raw) if raw else None


def rules_path(portal_id: str | None) -> Path:
    """The file :func:`load_rules` reads for ``portal_id``.

    Order: ``$HUBSPOT_MCP_RULES_DIR/<portal>.yaml``, bundled ``<portal>.yaml``,
    bundled ``default.yaml``. A portal id that is not all digits is rejected so
    it can never address a path outside the rules directories.
    """
    if portal_id:
        if not _PORTAL_RE.fullmatch(str(portal_id)):
            raise RulesError(f"invalid portal id {portal_id!r}")
        env_dir = _rules_dir()
        if env_dir is not None and (env_dir / f"{portal_id}.yaml").is_file():
            return env_dir / f"{portal_id}.yaml"
        if (BUNDLED_DIR / f"{portal_id}.yaml").is_file():
            return BUNDLED_DIR / f"{portal_id}.yaml"
    return BUNDLED_DIR / "default.yaml"


def load_rules(portal_id: str | None = None) -> SalesRules:
    """Read and validate the rules for ``portal_id``. Reads the file on every call."""
    path = rules_path(portal_id)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RulesError(f"cannot read rules file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RulesError(f"rules file {path} is not valid YAML: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise RulesError(f"rules file {path} must be a YAML mapping")
    try:
        return SalesRules.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'file'}: {err['msg']}" for err in exc.errors()
        )
        raise RulesError(f"rules file {path} is invalid: {problems}") from exc


def resolve_as_of(rules: SalesRules, now: datetime | None = None) -> date:
    """The reference date: ``rules.as_of``, else today in ``rules.timezone``."""
    if rules.as_of is not None:
        return rules.as_of
    tz = ZoneInfo(rules.timezone)
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    return current.date()


def rules_hash(rules: SalesRules) -> str:
    """Stable 12-hex fingerprint of the rule values, for result stamps.

    ``as_of`` is part of it, so a frozen date and a floating one hash differently.
    """
    canonical = json.dumps(rules.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def rules_stamp(rules: SalesRules, now: datetime | None = None) -> dict[str, Any]:
    """The ``as_of`` and ``rules_hash`` every job result carries."""
    return {"as_of": resolve_as_of(rules, now).isoformat(), "rules_hash": rules_hash(rules)}


_CODE_TEXT = {
    "OVERDUE": "OVERDUE: an open deal whose close date is before the as-of date.",
    "NO_NEXT": (
        "NO_NEXT: a deal in {no_next} with no scheduled next activity dated on or after the as-of date "
        "and an empty next step."
    ),
    "SILENT": (
        "SILENT: a deal in {silent} whose last completed contact (notes_last_contacted) is more than "
        "{silent_days} days before the as-of date. A scheduled activity is a plan, not a contact."
    ),
    "STALLED": "STALLED: a deal that has been in its current stage for more than the days listed below.",
    "INFLATED": (
        "INFLATED: a Commit deal whose stage probability is below {inflated_below:g}."
    ),
    "SANDBAG": (
        "SANDBAG: a Pipeline deal whose stage probability is above {sandbag_above:g}. "
        "This is an accuracy finding and never lowers supported commit."
    ),
}


def _names(categories: list[str]) -> str:
    return " or ".join(c.replace("_", " ").title() for c in categories) or "no category"


def render_sales_rules_md(rules: SalesRules, now: datetime | None = None) -> str:
    """Plain-English rules for the benchmark's shared ``sales-rules.md``.

    Every threshold the tools use appears here, with the as-of date, so an arm
    working without the tools can apply the same rules.
    """
    as_of = resolve_as_of(rules, now)
    floating = "" if rules.as_of is not None else f" (today in {rules.timezone})"
    lines = [
        "# Sales rules",
        "",
        f"As-of date: {as_of.isoformat()}{floating}. Every \"days since\" and \"past due\" test counts from it.",
        f"Timezone: {rules.timezone}. Rules fingerprint: {rules_hash(rules)}.",
        "",
        "## Reason codes",
        "",
    ]
    fmt = {
        "no_next": _names(rules.no_next_scope),
        "silent": _names(rules.silent_scope),
        "silent_days": rules.silent_days,
        "inflated_below": rules.inflated_below,
        "sandbag_above": rules.sandbag_above,
    }
    for code in CODES:
        lines.append(f"- {_CODE_TEXT[code].format(**fmt)}")
    lines += [
        "",
        "A missing evidence value never fires a rule: a deal with no recorded contact is not SILENT, "
        "and a deal with no stage-entered date is not STALLED. A deal can carry several codes.",
        "",
        "## Thresholds",
        "",
        f"- SILENT after {rules.silent_days} days without contact.",
        f"- STALLED after {rules.stalled_days.default} days in a stage"
        + (", except:" if rules.stalled_days.by_stage else "."),
    ]
    for label, days in sorted(rules.stalled_days.by_stage.items()):
        lines.append(f"  - {label}: {days} days")
    lines += [
        f"- At-risk window: {rules.at_risk_window_days} days.",
        f"- INFLATED below stage probability {rules.inflated_below:g}; SANDBAG above {rules.sandbag_above:g}.",
        "",
        "## Triage",
        "",
        "Triage reports these codes: " + ", ".join(rules.triage_codes) + ". "
        "Open deals rank by urgency score, the sum of the weights of a deal's codes ("
        + ", ".join(f"{c} {rules.triage_weights[c]:g}" for c in rules.triage_codes)
        + ") multiplied by its amount in the home currency; ties break by deal id, lowest first.",
        "",
        "## Stage probabilities",
        "",
    ]
    if rules.stage_probabilities:
        lines.append("These override HubSpot's stage metadata:")
        for label, p in sorted(rules.stage_probabilities.items()):
            lines.append(f"- {label}: {p:g}")
    else:
        lines.append("No overrides. Use the probability HubSpot stores on each stage.")
    lines += [
        "",
        "## Forecast figures",
        "",
        "A deal is in a period when it is open and its close date falls inside the period; closed-won deals "
        "count when their close date is inside it.",
        "",
        "- Closed won: sum of closed-won deals in the period.",
        "- Commit: open in-period deals whose forecast category is Commit.",
        "- Best case: Commit plus open in-period Best case deals.",
        "- Pipeline: every open in-period deal except Omit.",
        "- Weighted: amount x probability, where probability is the stage override above, else "
        "hs_forecast_probability, else hs_deal_stage_probability, else the stage's own probability.",
        "- Supported commit: Commit minus the whole amount of every Commit deal flagged "
        "SILENT, OVERDUE, NO_NEXT, STALLED or INFLATED. No partial reductions.",
        "- Slipped: an open deal whose close date is before the period start.",
        "",
        f"Fiscal year starts in month {rules.fiscal_year_start_month}.",
        "",
        "## Currency",
        "",
        "Figures use each deal's amount_in_home_currency"
        + (f" ({rules.home_currency})" if rules.home_currency else "")
        + ". A deal without it is counted at its own amount only when its currency is the home currency. "
        "Never invent an exchange rate; a deal that cannot be converted is left out of totals and named. "
        "A deal with no amount counts as 0. When no home currency is set and the deals carry two or more "
        "currencies, the forecast is unavailable and says why, because nothing can be summed.",
    ]
    if rules.default_owner:
        lines += ["", "## Owner", "", f"\"My deals\" means deals owned by {rules.default_owner}."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hubspot_mcp.intent.rules",
        description="Render the sales rules for a portal as plain English (the benchmark's sales-rules.md).",
    )
    parser.add_argument("--portal", help="portal id; omit for the default rules")
    parser.add_argument("--out", help="write to this path instead of stdout")
    args = parser.parse_args(argv)
    try:
        text = render_sales_rules_md(load_rules(args.portal))
    except RulesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
