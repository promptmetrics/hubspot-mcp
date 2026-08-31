"""Unit tests for the risk-tiered approval policy (Phase 2)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hubspot_mcp.policy import (
    AUTO,
    CONFIRM,
    FULL_GATE,
    ApprovalPolicy,
    classify_write,
    load_approval_policy,
)


# --------------------------------------------------------------------------- #
# Loader: precedence + fallback
# --------------------------------------------------------------------------- #

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    return tmp_path


def test_default_when_no_files(cfg):
    p = load_approval_policy("123")
    assert p.auto_apply_max_records == 100
    assert "amount" in p.sensitive_properties
    assert p.sensitive_property_action == "confirm"


def test_global_override(cfg):
    (cfg / "approval_policy.json").write_text(json.dumps({"auto_apply_max_records": 25}))
    p = load_approval_policy("123")
    assert p.auto_apply_max_records == 25
    # unspecified keys keep the shipped default
    assert "amount" in p.sensitive_properties


def test_per_portal_overrides_global(cfg):
    (cfg / "approval_policy.json").write_text(json.dumps({"auto_apply_max_records": 25}))
    (cfg / "123").mkdir()
    (cfg / "123" / "approval_policy.json").write_text(
        json.dumps({"auto_apply_max_records": 500, "sensitive_properties": ["custom_score"]})
    )
    p = load_approval_policy("123")
    assert p.auto_apply_max_records == 500
    # scalar overrides replace; the safety LIST union-merges — custom_score is
    # added, the shipped sensitive defaults are retained (cannot be dropped).
    assert "custom_score" in p.sensitive_properties
    assert "amount" in p.sensitive_properties
    # a different portal still sees only the global override
    assert load_approval_policy("999").auto_apply_max_records == 25


def test_pattern_confirm_threshold_default(cfg):
    # Shipped default is 100 (= the shipped auto_apply_max_records; spec §7).
    p = load_approval_policy("123")
    assert p.pattern_confirm_threshold == 100


def test_pattern_confirm_threshold_override(cfg):
    (cfg / "123").mkdir()
    (cfg / "123" / "approval_policy.json").write_text(
        json.dumps({"pattern_confirm_threshold": 25})
    )
    assert load_approval_policy("123").pattern_confirm_threshold == 25


def test_pattern_confirm_threshold_malformed_falls_back_to_records_ceiling(cfg):
    (cfg / "approval_policy.json").write_text(
        json.dumps({"auto_apply_max_records": 40, "pattern_confirm_threshold": "oops"})
    )
    p = load_approval_policy("123")
    # A malformed threshold falls back to the effective records ceiling, never
    # fail-open to unlimited.
    assert p.pattern_confirm_threshold == 40


def test_schedule_queue_ttl_days_default(cfg):
    assert load_approval_policy("123").schedule_queue_ttl_days == 7


def test_schedule_queue_ttl_days_override(cfg):
    (cfg / "approval_policy.json").write_text(json.dumps({"schedule_queue_ttl_days": 14}))
    assert load_approval_policy("123").schedule_queue_ttl_days == 14


def test_schedule_queue_ttl_days_malformed_falls_back(cfg):
    (cfg / "approval_policy.json").write_text(
        json.dumps({"schedule_queue_ttl_days": "oops"})
    )
    assert load_approval_policy("123").schedule_queue_ttl_days == 7


def test_schedule_queue_ttl_days_negative_falls_back(cfg):
    (cfg / "approval_policy.json").write_text(
        json.dumps({"schedule_queue_ttl_days": -3})
    )
    assert load_approval_policy("123").schedule_queue_ttl_days == 7


def test_malformed_file_falls_back(cfg):
    (cfg / "approval_policy.json").write_text("{ this is not json")
    p = load_approval_policy("123")
    assert p.auto_apply_max_records == 100  # shipped default, never fail-open


def test_unknown_action_coerced_to_confirm(cfg):
    (cfg / "approval_policy.json").write_text(json.dumps({"sensitive_property_action": "nonsense"}))
    p = load_approval_policy("123")
    assert p.sensitive_property_action == "confirm"


def test_sensitive_tier_property():
    assert ApprovalPolicy(sensitive_property_action="confirm").sensitive_tier == CONFIRM
    assert ApprovalPolicy(sensitive_property_action="full_gate").sensitive_tier == FULL_GATE


# --------------------------------------------------------------------------- #
# classify_write: the 5-rule table
# --------------------------------------------------------------------------- #

def _pd(*, intent_type="update", risk="medium", impact=1, original_values=None, payload=None,
        tool_name="hubspot_update_object"):
    """Build a preview_data dict matching apply_write's persisted shape."""
    return {
        "tool_name": tool_name,
        "intent": {"intent_type": intent_type, "risk_level": risk},
        "preview": {
            "risk_level": risk,
            "impact_count": impact,
            # default = FULL capture (one original per targeted record); pass an
        # explicit dict to model partial/empty capture.
        "original_values": original_values if original_values is not None
        else {str(i): {"x": "old"} for i in range(impact)},
        },
        "proposed_payload": payload or {"properties": {"jobtitle": "Engineer"}},
    }


DEFAULT = ApprovalPolicy(
    auto_apply_max_records=100,
    sensitive_properties=("amount", "dealstage"),
    sensitive_property_action="confirm",
    never_auto_tools=("hubspot_create_workflow",),
)


def test_destructive_is_full_gate():
    assert classify_write(_pd(intent_type="delete", risk="destructive"), DEFAULT) == FULL_GATE
    assert classify_write(_pd(intent_type="merge", risk="destructive"), DEFAULT) == FULL_GATE


def test_unconfirmed_reversibility_is_full_gate():
    # update that captured no original_values -> not undoable -> full gate
    assert classify_write(_pd(intent_type="update", original_values={}), DEFAULT) == FULL_GATE


def test_single_reversible_update_is_auto():
    assert classify_write(_pd(intent_type="update", impact=1), DEFAULT) == AUTO


def test_create_is_auto():
    # create is always undoable (delete the created record); no original_values needed
    assert classify_write(_pd(intent_type="create", original_values={}), DEFAULT) == AUTO


def test_multi_record_under_threshold_is_auto():
    assert classify_write(_pd(intent_type="update", impact=80), DEFAULT) == AUTO


def test_over_threshold_is_confirm():
    assert classify_write(_pd(intent_type="update", impact=101), DEFAULT) == CONFIRM


def test_sensitive_field_confirm_action():
    pd = _pd(intent_type="update", impact=1, payload={"properties": {"amount": "5000"}})
    assert classify_write(pd, DEFAULT) == CONFIRM


def test_sensitive_field_full_gate_action():
    strict = ApprovalPolicy(
        auto_apply_max_records=100, sensitive_properties=("amount",), sensitive_property_action="full_gate"
    )
    pd = _pd(intent_type="update", impact=1, payload={"properties": {"amount": "5000"}})
    assert classify_write(pd, strict) == FULL_GATE


def test_sensitive_in_bulk_records_detected():
    pd = _pd(
        intent_type="update",
        impact=3,
        payload={"records": [{"id": "1", "properties": {"dealstage": "closedwon"}}]},
    )
    assert classify_write(pd, DEFAULT) == CONFIRM


def test_destructive_beats_sensitive_and_size():
    # rule ordering: destructive wins even if it also touches sensitive fields
    pd = _pd(intent_type="delete", risk="destructive", impact=1, payload={"properties": {"amount": "1"}})
    assert classify_write(pd, DEFAULT) == FULL_GATE


def test_never_auto_tool_keeps_gate():
    # a workflow create is reversible + single-record but side-effectful, so it
    # must keep the human gate (CONFIRM), never auto-apply
    pd = _pd(intent_type="create", impact=1, original_values={}, tool_name="hubspot_create_workflow")
    assert classify_write(pd, DEFAULT) == CONFIRM


def test_never_auto_defaults_ship_workflow_tools(cfg):
    p = load_approval_policy("123")
    assert "hubspot_create_workflow" in p.never_auto_tools
    assert "hubspot_enroll_workflow" in p.never_auto_tools


def test_never_auto_override_extends_not_replaces(cfg):
    # a per-portal edit adding a tool must NOT silently drop the shipped
    # workflow guards (review MAJOR #1 — union-merge, not replace)
    (cfg / "123").mkdir()
    (cfg / "123" / "approval_policy.json").write_text(
        json.dumps({"never_auto_tools": ["my_custom_tool"]})
    )
    p = load_approval_policy("123")
    assert "my_custom_tool" in p.never_auto_tools
    assert "hubspot_create_workflow" in p.never_auto_tools  # shipped guard retained
    assert "hubspot_enroll_workflow" in p.never_auto_tools


def test_partial_capture_update_downgrades_to_confirm():
    # a bulk update targeting 3 records but capturing only 2 originals is only
    # partially undoable → CONFIRM, not AUTO (review MINOR)
    pd = _pd(intent_type="update", impact=3,
             original_values={"c-1": {"x": "old"}, "c-2": {"x": "old"}})
    assert classify_write(pd, DEFAULT) == CONFIRM


def test_full_capture_bulk_update_is_auto():
    pd = _pd(intent_type="update", impact=3,
             original_values={"c-1": {"x": "o"}, "c-2": {"x": "o"}, "c-3": {"x": "o"}})
    assert classify_write(pd, DEFAULT) == AUTO


def test_sensitive_detected_via_original_values(cfg):
    # an update whose payload shape doesn't expose `properties`, but whose
    # captured original_values include a sensitive field, still routes to CONFIRM
    pol = load_approval_policy("123")  # default sensitive list includes 'amount'
    pd = {
        "tool_name": "hubspot_update_object",
        "intent": {"intent_type": "update", "risk_level": "medium"},
        "preview": {
            "risk_level": "medium",
            "impact_count": 1,
            "original_values": {"c-1": {"amount": "1000"}},
        },
        "proposed_payload": {"unrecognized_shape": True},
    }
    assert classify_write(pd, pol) == CONFIRM
