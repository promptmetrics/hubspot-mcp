"""The sales rules the intent tools run on: one YAML file per portal, re-read on every call.

Pins what the operator relies on: an edit to the file changes the next answer
with no restart, a typo fails loudly instead of silently reverting to a default,
and the plain-English ``sales-rules.md`` handed to the benchmark arms is rendered
from the same values the tools use.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from hubspot_mcp.intent import rules as rules_mod
from hubspot_mcp.intent.rules import (
    RulesError,
    SalesRules,
    load_rules,
    normalize_category,
    render_sales_rules_md,
    resolve_as_of,
    rules_hash,
    rules_path,
    rules_stamp,
)

PORTAL = "99999999"


@pytest.fixture(autouse=True)
def _rules_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HUBSPOT_MCP_RULES_DIR", str(tmp_path / "rules"))
    (tmp_path / "rules").mkdir()
    return tmp_path / "rules"


def test_bundled_default_yaml_matches_the_model_defaults():
    """default.yaml documents the defaults; if the two drift, the file lies to the operator."""
    assert load_rules(None) == SalesRules()


def test_rules_are_reread_on_every_call_so_a_live_edit_takes_effect(_rules_env):
    path = _rules_env / f"{PORTAL}.yaml"
    path.write_text("silent_days: 7\n")
    assert load_rules(PORTAL).silent_days == 7
    path.write_text("silent_days: 21\n")
    assert load_rules(PORTAL).silent_days == 21, "no caching: the file is the source of truth"


def test_portal_file_in_the_rules_dir_beats_the_bundled_one_and_replaces_rather_than_merges(
    _rules_env, tmp_path, monkeypatch
):
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "default.yaml").write_text("silent_days: 5\nstalled_days: {default: 99}\n")
    (bundled / f"{PORTAL}.yaml").write_text("silent_days: 6\n")
    monkeypatch.setattr(rules_mod, "BUNDLED_DIR", bundled)

    assert load_rules(PORTAL).silent_days == 6
    assert load_rules("12345").silent_days == 5, "another portal falls back to default.yaml"
    assert load_rules(PORTAL).stalled_days.default == 30, "a portal file replaces default.yaml; it does not merge"

    (_rules_env / f"{PORTAL}.yaml").write_text("silent_days: 8\n")
    assert load_rules(PORTAL).silent_days == 8
    assert rules_path(PORTAL) == _rules_env / f"{PORTAL}.yaml"


def test_a_portal_id_cannot_escape_the_rules_directories():
    with pytest.raises(RulesError, match="invalid portal id"):
        load_rules("../../etc/passwd")


@pytest.mark.parametrize(
    ("yaml_text", "needle"),
    [
        ("silent_day: 7\n", "silent_day"),
        ("inflated_below: 1.5\n", "inflated_below"),
        ("timezone: Mars/Olympus\n", "unknown timezone"),
        ("silent_scope: [COMMIT, WISHFUL]\n", "WISHFUL"),
        ("triage_codes: [OVERDUE, LATE]\n", "LATE"),
        ("stage_probabilities: {Proposal: 40}\n", "between 0 and 1"),
        ("bulk_write_batch_size: 500\n", "bulk_write_batch_size"),
        ("fiscal_year_start_month: 13\n", "fiscal_year_start_month"),
        ("triage_codes: [OVERDUE, SANDBAG]\n", "triage_weights"),
        ("- just\n- a list\n", "mapping"),
        ("silent_days: [unclosed\n", "not valid YAML"),
    ],
)
def test_a_bad_rules_file_fails_loudly_and_names_the_file(_rules_env, yaml_text, needle):
    (_rules_env / f"{PORTAL}.yaml").write_text(yaml_text)
    with pytest.raises(RulesError) as err:
        load_rules(PORTAL)
    assert needle in str(err.value)
    assert f"{PORTAL}.yaml" in str(err.value)


def test_an_empty_portal_file_means_all_defaults(_rules_env):
    (_rules_env / f"{PORTAL}.yaml").write_text("")
    assert load_rules(PORTAL) == SalesRules()


def test_normalize_category_maps_hubspot_labels_and_internal_values():
    assert normalize_category("Best case") == "BEST_CASE"
    assert normalize_category("best-case") == "BEST_CASE"
    assert normalize_category("COMMIT") == "COMMIT"
    assert normalize_category(None) is None
    assert normalize_category("  ") is None


def test_scopes_are_normalized_and_apply_by_category(_rules_env):
    (_rules_env / f"{PORTAL}.yaml").write_text("silent_scope: [commit, Best case]\nno_next_scope: [Pipeline]\n")
    rules = load_rules(PORTAL)
    assert rules.silent_scope == ["COMMIT", "BEST_CASE"]
    assert rules.silent_applies("Best case") and not rules.silent_applies("PIPELINE")
    assert rules.no_next_applies("PIPELINE") and not rules.no_next_applies(None)


def test_stalled_days_per_stage_overrides_the_default_case_insensitively(_rules_env):
    (_rules_env / f"{PORTAL}.yaml").write_text("stalled_days:\n  default: 30\n  by_stage:\n    Proposal: 10\n")
    rules = load_rules(PORTAL)
    assert rules.stalled_days_for("proposal") == 10
    assert rules.stalled_days_for("Negotiation") == 30
    assert rules.stalled_days_for(None) == 30


def test_stage_probability_override_is_by_label(_rules_env):
    (_rules_env / f"{PORTAL}.yaml").write_text("stage_probabilities: {Proposal: 0.5}\n")
    rules = load_rules(PORTAL)
    assert rules.probability_override("PROPOSAL") == 0.5
    assert rules.probability_override("Demo") is None


def test_as_of_is_the_fixed_date_when_set_else_today_in_the_portal_timezone():
    fixed = SalesRules(as_of=date(2026, 10, 30))
    assert resolve_as_of(fixed, now=datetime(2030, 1, 1, tzinfo=UTC)) == date(2026, 10, 30)

    floating = SalesRules(timezone="Europe/Berlin")
    late_utc = datetime(2026, 10, 30, 23, 30, tzinfo=UTC)  # already the 31st in Berlin
    assert resolve_as_of(floating, now=late_utc) == date(2026, 10, 31)
    assert resolve_as_of(SalesRules(timezone="America/Los_Angeles"), now=late_utc) == date(2026, 10, 30)


def test_rules_hash_is_stable_and_changes_with_any_value():
    a = SalesRules()
    assert rules_hash(a) == rules_hash(SalesRules())
    assert len(rules_hash(a)) == 12
    assert rules_hash(a) != rules_hash(SalesRules(silent_days=15))
    assert rules_hash(a) != rules_hash(SalesRules(stage_probabilities={"Demo": 0.3}))


def test_rules_stamp_carries_as_of_and_hash():
    rules = SalesRules(as_of=date(2026, 10, 30))
    assert rules_stamp(rules) == {"as_of": "2026-10-30", "rules_hash": rules_hash(rules)}


def test_rendered_markdown_states_every_threshold_the_tools_use():
    rules = SalesRules(
        as_of=date(2026, 10, 30),
        silent_days=9,
        stalled_days={"default": 27, "by_stage": {"Proposal": 11}},
        inflated_below=0.35,
        sandbag_above=0.85,
        stage_probabilities={"Proposal": 0.45},
        fiscal_year_start_month=4,
        home_currency="EUR",
        default_owner="rep@example.com",
    )
    md = render_sales_rules_md(rules)
    for expected in (
        "2026-10-30",
        "more than 9 days",
        "27 days in a stage",
        "Proposal: 11 days",
        "below 0.35",
        "above 0.85",
        "Proposal: 0.45",
        "month 4",
        "(EUR)",
        "rep@example.com",
        rules_hash(rules),
    ):
        assert expected in md, expected
    for code in ("OVERDUE", "NO_NEXT", "SILENT", "STALLED", "INFLATED", "SANDBAG"):
        assert f"- {code}:" in md, f"{code} must be defined in the rendered rules"
    assert "Never invent an exchange rate" in md


def test_rendered_markdown_says_when_the_date_floats():
    md = render_sales_rules_md(SalesRules(timezone="Europe/Berlin"), now=datetime(2026, 3, 2, 12, tzinfo=UTC))
    assert "2026-03-02 (today in Europe/Berlin)" in md


def test_cli_prints_or_writes_the_rendered_rules(_rules_env, tmp_path, capsys):
    (_rules_env / f"{PORTAL}.yaml").write_text("as_of: 2026-10-30\nsilent_days: 3\n")
    assert rules_mod.main(["--portal", PORTAL]) == 0
    assert "more than 3 days" in capsys.readouterr().out

    out = tmp_path / "sales-rules.md"
    assert rules_mod.main(["--portal", PORTAL, "--out", str(out)]) == 0
    assert "2026-10-30" in out.read_text()


def test_cli_reports_a_bad_file_on_stderr_with_exit_code_1(_rules_env, capsys):
    (_rules_env / f"{PORTAL}.yaml").write_text("silent_day: 3\n")
    assert rules_mod.main(["--portal", PORTAL]) == 1
    assert "silent_day" in capsys.readouterr().err
