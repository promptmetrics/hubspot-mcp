"""Phase 4: inverse extractor (``v4_payload_to_blueprint``) + learning log.

Two suites:
  - **Round-trip backbone** over every shipped blueprint that forward-converts:
    render -> ``blueprint_to_v4_payload`` -> extract -> render -> to_v4 again,
    deep-equal the two V4 payloads. Covers defaults, custom params, and both
    sides of every ``include_if``. The 7 blueprints the forward converter
    refuses (placeholder content_id / team_id, property-relative due dates) are
    skipped — the extractor only has to round-trip what the converter can build.
  - **Unit cases** for the honest-partial-extraction guarantees: unknown
    actionTypeId, non-rejoining branch, each portal-specific flagger, the
    dropped-settings audit, and the learning-log append.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hubspot_mcp.blueprints.workflows import list_blueprints
from hubspot_mcp.blueprints.workflows.converter import blueprint_to_v4_payload
from hubspot_mcp.blueprints.workflows.extractor import (
    ExtractionResult,
    v4_payload_to_blueprint,
)
from hubspot_mcp.blueprints.workflows.learning_log import record_unknown_actions
from hubspot_mcp.blueprints.workflows.schema import render_spec, validate_blueprint

# Blueprints the forward converter refuses (placeholder content_id / team_id,
# property-relative due dates). The extractor is only obligated to round-trip
# what the converter can build, so these are excluded from the backbone.
_SKIP = {
    "re_anniversary_touch",
    "re_open_house_followup",
    "re_buyer_appraisal_alert",
    "re_buyer_financing_alert",
    "re_buyer_inspection_alert",
    "re_vendor_expiry",
    "re_hygiene_unassigned",
}

# Per-blueprint param sets. Default {} is always first; for the parametrized
# blueprints we add a custom set and — where an ``include_if`` exists — the
# "other side" so both branches of the conditional are exercised.
_PARAM_CASES: dict[str, list[dict]] = {
    "welcome_email": [{}, {"delay_hours": 2, "sender_name": "Alice"}],  # off / on
    "lead_scoring": [{}, {"threshold": 80, "increment": 5}, {"threshold": 0}],  # on / on / off
    "deal_stage_task": [{}, {"deal_stage": "closedwon", "task_subject": "Ring the bell"}],
    "re_engagement": [{}, {"inactivity_days": 60}],
}


def _roundtrip(bp, params: dict | None = None):
    """render -> to_v4 -> extract -> render -> to_v4; return (v4_1, v4_2, result)."""
    spec = bp.build(dict(params or {}))
    spec["name"] = "RT"  # render_spec intentionally omits name; pin it for the compare
    v4_1 = blueprint_to_v4_payload(spec)
    result = v4_payload_to_blueprint(v4_1)
    bf = validate_blueprint(result.blueprint)
    spec2 = render_spec(bf, {})  # extracted blueprints have no parameters
    spec2["name"] = v4_1["name"]
    v4_2 = blueprint_to_v4_payload(spec2)
    return v4_1, v4_2, result


def _convertible_blueprints():
    return [bp for bp in list_blueprints() if bp.name not in _SKIP]


class TestRoundTripBackbone:
    """Over every convertible shipped blueprint × param sets: v4_1 == v4_2."""

    @pytest.mark.parametrize("bp", _convertible_blueprints(), ids=lambda b: b.name)
    def test_default_params(self, bp):
        v4_1, v4_2, _ = _roundtrip(bp, {})
        assert v4_1 == v4_2

    @pytest.mark.parametrize("bp", _convertible_blueprints(), ids=lambda b: b.name)
    def test_custom_params(self, bp):
        cases = _PARAM_CASES.get(bp.name, [{}])
        for params in cases:
            v4_1, v4_2, _ = _roundtrip(bp, params)
            assert v4_1 == v4_2, f"round-trip diverged for {bp.name} with {params}"

    def test_include_if_both_sides(self):
        # welcome_email: delay_hours=0 (Delay excluded) vs delay_hours=2 (included)
        # lead_scoring:   threshold=50 default (branch included) vs threshold=0 (excluded)
        for bp_name, off, on in [
            ("welcome_email", {"delay_hours": 0}, {"delay_hours": 2}),
            ("lead_scoring", {"threshold": 0}, {"threshold": 50}),
        ]:
            bp = next(b for b in list_blueprints() if b.name == bp_name)
            for params in (off, on):
                v4_1, v4_2, _ = _roundtrip(bp, params)
                assert v4_1 == v4_2, f"{bp_name} include_if side diverged for {params}"

    def test_extracted_blueprint_is_valid(self):
        bp = next(b for b in list_blueprints() if b.name == "lead_scoring")
        _, _, result = _roundtrip(bp, {})
        bf = validate_blueprint(result.blueprint)  # must not raise
        assert bf.source.origin == "extracted"
        assert bf.spec.actions  # non-empty


# ---------------------------------------------------------------------------
# Unit cases: honest partial extraction
# ---------------------------------------------------------------------------

def _payload(actions, *, object_type_id="0-1", start="1", enrollment=None, extra=None):
    """Build a minimal V4 payload with the given action nodes wired 1->2->...->N."""
    p: dict = {
        "name": "Unit",
        "isEnabled": False,
        "objectTypeId": object_type_id,
        "flowType": "WORKFLOW",
        "type": "CONTACT_FLOW",
        "enrollmentCriteria": enrollment or {"type": "LIST_BASED", "listFilterBranch": {}},
        "startActionId": start,
        "actions": actions,
    }
    if extra:
        p.update(extra)
    return p


def _setprop(action_id, next_id=None, value="lead"):
    n = {"actionId": str(action_id), "actionTypeVersion": 0, "actionTypeId": "0-5",
         "type": "SINGLE_CONNECTION", "fields": {"property_name": "lifecyclestage",
         "value": {"staticValue": value, "type": "STATIC_VALUE"}}}
    if next_id is not None:
        n["connection"] = {"edgeType": "STANDARD", "nextActionId": str(next_id)}
    return n


class TestUnknownAction:
    def test_unknown_action_type_id_kept_raw_and_recorded(self):
        unknown = {
            "actionId": "1", "actionTypeVersion": 2, "actionTypeId": "3-777",
            "type": "SINGLE_CONNECTION",
            "fields": {"some_field": "x" * 200, "another": ["a", "b"]},
        }
        payload = _payload([unknown])
        result = v4_payload_to_blueprint(payload)
        actions = result.blueprint["spec"]["actions"]
        assert len(actions) == 1
        assert actions[0].get("raw") is True
        assert actions[0]["action_type_id"] == "3-777"
        # recorded for the learning log
        assert len(result.unknown_actions) == 1
        entry = result.unknown_actions[0]
        assert entry["action_type_id"] == "3-777"
        assert "some_field" in entry["field_names"]
        # value preview truncated to ~100 chars, not the full 200
        assert len(entry["value_previews"]["some_field"]) <= 100
        assert any("not natively modeled" in w for w in result.warnings)

    def test_known_action_not_recorded_as_unknown(self):
        payload = _payload([_setprop(1)])
        result = v4_payload_to_blueprint(payload)
        assert result.unknown_actions == []
        assert result.blueprint["spec"]["actions"][0]["ui_action"] == "Set property value"


class TestNonRejoiningBranch:
    def test_non_rejoining_list_branch_kept_raw_with_warning(self):
        # True-branch chain ends (nextActionId=None) but defaultBranch points at
        # a separate action (id 3) the true chain never rejoins -> non-rejoining.
        branch = {
            "actionId": "1", "type": "LIST_BRANCH",
            "listBranches": [{
                "filterBranch": {
                    "filterBranches": [{
                        "filterBranches": [], "filters": [{
                            "property": "lifecyclestage",
                            "operation": {"operationType": "ENUMERATION",
                                          "operator": "IS_ANY_OF", "values": ["lead"],
                                          "includeObjectsWithNoValueSet": False},
                            "filterType": "PROPERTY"}],
                        "filterBranchType": "AND", "filterBranchOperator": "AND",
                    }], "filters": [], "filterBranchType": "OR", "filterBranchOperator": "OR",
                },
                "connection": {"edgeType": "STANDARD", "nextActionId": "2"},
            }],
            "defaultBranch": {"edgeType": "STANDARD", "nextActionId": "3"},
        }
        true_action = _setprop(2)  # true chain ends here (no next) — does not rejoin at 3
        after = _setprop(3)
        payload = _payload([branch, true_action, after], start="1")
        result = v4_payload_to_blueprint(payload)
        actions = result.blueprint["spec"]["actions"]
        raw = [a for a in actions if a.get("raw")]
        assert raw, "non-rejoining LIST_BRANCH must be kept raw"
        assert raw[0]["node"]["type"] == "LIST_BRANCH"
        assert any("does not re-join" in w for w in result.warnings)


class TestRejoiningBranch:
    def test_single_rejoining_branch_becomes_native_if_then(self):
        branch = {
            "actionId": "1", "type": "LIST_BRANCH",
            "listBranches": [{
                "filterBranch": {
                    "filterBranches": [{
                        "filterBranches": [], "filters": [{
                            "property": "hubspotscore",
                            "operation": {"operationType": "NUMBER", "operator": "IS_GREATER_THAN",
                                          "value": 50, "includeObjectsWithNoValueSet": False},
                            "filterType": "PROPERTY"}],
                        "filterBranchType": "AND", "filterBranchOperator": "AND",
                    }], "filters": [], "filterBranchType": "OR", "filterBranchOperator": "OR",
                },
                "connection": {"edgeType": "STANDARD", "nextActionId": "2"},
            }],
            "defaultBranch": {"edgeType": "STANDARD", "nextActionId": "3"},
        }
        true_action = _setprop(2, next_id=3)  # true chain rejoins at 3
        after = _setprop(3)
        payload = _payload([branch, true_action, after], start="1")
        result = v4_payload_to_blueprint(payload)
        actions = result.blueprint["spec"]["actions"]
        assert actions[0]["ui_action"] == "If/then branch"
        assert actions[0]["fields"]["Operator"] == "is greater than"
        assert actions[0]["fields"]["Value"] == 50
        assert actions[0]["true_branch"][0]["ui_action"] == "Set property value"
        assert all(not a.get("raw") for a in actions), "rejoining branch must not be raw"
        assert result.unknown_actions == []


class TestPortalSpecificFlags:
    def test_list_id_flagged(self):
        node = {"actionId": "1", "actionTypeVersion": 5, "actionTypeId": "0-63809083",
                "type": "SINGLE_CONNECTION", "fields": {"targetObject": "{{ enrolled_object }}",
                "listId": "123456"}}
        result = v4_payload_to_blueprint(_payload([node]))
        assert any(f["kind"] == "list_id" and f["value"] == "123456" for f in result.flags)

    def test_content_id_flagged(self):
        node = {"actionId": "1", "actionTypeVersion": 0, "actionTypeId": "0-4",
                "type": "SINGLE_CONNECTION", "fields": {"content_id": "999"}}
        result = v4_payload_to_blueprint(_payload([node]))
        assert any(f["kind"] == "content_id" and f["value"] == "999" for f in result.flags)

    def test_team_id_flagged(self):
        node = {"actionId": "1", "actionTypeVersion": 0, "actionTypeId": "0-11",
                "type": "SINGLE_CONNECTION",
                "fields": {"team_ids": ["77"], "target_property": "hubspot_owner_id",
                           "overwrite_current_owner": "false"}}
        result = v4_payload_to_blueprint(_payload([node]))
        assert any(f["kind"] == "team_id" and f["value"] == "77" for f in result.flags)

    def test_custom_object_type_flagged(self):
        # Known 148408595 custom object: invertible to "Custom object (Offers)"
        # but still flagged portal-specific.
        result = v4_payload_to_blueprint(_payload([_setprop(1)], object_type_id="2-202484492"))
        assert result.blueprint["spec"]["object_type"] == "Custom object (Offers)"
        assert any(f["kind"] == "custom_object_type" for f in result.flags)

    def test_unknown_custom_object_type_flagged_verbatim(self):
        result = v4_payload_to_blueprint(_payload([_setprop(1)], object_type_id="2-99999999"))
        assert result.blueprint["spec"]["object_type"] == "Custom object (2-99999999)"
        assert any(f["kind"] == "custom_object_type" for f in result.flags)

    def test_unknown_event_type_flagged(self):
        ec = {"type": "EVENT_BASED", "shouldReEnroll": False,
              "unEnrollObjectsNotMeetingCriteria": False,
              "eventFilterBranches": [{"eventTypeId": "4-9999", "filterBranchType": "UNIFIED_EVENTS",
                                       "operator": "HAS_COMPLETED", "filterBranches": [], "filters": [],
                                       "filterBranchOperator": "AND"}],
              "reEnrollmentTriggersFilterBranches": []}
        result = v4_payload_to_blueprint(_payload([_setprop(1)], enrollment=ec))
        assert result.blueprint["spec"]["enrollment"]["type"] == "EVENT_BASED"
        assert any(f["kind"] == "unknown_event_type" for f in result.flags)

    def test_ambiguous_event_type_flagged(self):
        # "4-1463224" is the shared eventTypeId for 3 UI triggers -> ambiguous.
        ec = {"type": "EVENT_BASED", "shouldReEnroll": False,
              "unEnrollObjectsNotMeetingCriteria": False,
              "eventFilterBranches": [{"eventTypeId": "4-1463224", "filterBranchType": "UNIFIED_EVENTS",
                                       "operator": "HAS_COMPLETED", "filterBranches": [], "filters": [],
                                       "filterBranchOperator": "AND"}],
              "reEnrollmentTriggersFilterBranches": []}
        result = v4_payload_to_blueprint(_payload([_setprop(1)], enrollment=ec))
        assert result.blueprint["spec"]["enrollment"]["trigger"] == "Contact is created"
        assert any(f["kind"] == "ambiguous_event_type" for f in result.flags)

    def test_placeholder_values_not_flagged(self):
        # A placeholder content_id ("<create email first>") is not a real portal
        # ID and must not be flagged (the converter rejects it upstream anyway).
        node = {"actionId": "1", "actionTypeVersion": 0, "actionTypeId": "0-4",
                "type": "SINGLE_CONNECTION", "fields": {"content_id": "<create email first>"}}
        result = v4_payload_to_blueprint(_payload([node]))
        assert not any(f["kind"] == "content_id" for f in result.flags)


class TestDroppedSettingsAudit:
    def test_unmodeled_setting_dropped_and_flagged(self):
        extra = {"timeWindows": [{"some": "window"}], "suppressionListIds": [11, 22]}
        result = v4_payload_to_blueprint(_payload([_setprop(1)], extra=extra))
        assert "timeWindows" in result.dropped_settings
        assert "suppressionListIds" in result.dropped_settings
        assert any(f["kind"] == "dropped_setting" and f["path"] == "<root>.timeWindows"
                   for f in result.flags)
        assert any("timeWindows" in w for w in result.warnings)

    def test_empty_settings_not_flagged(self):
        # Default/empty values for audited keys are not meaningful -> not dropped.
        extra = {"timeWindows": [], "suppressionListIds": [], "canEnrollFromSalesforce": False}
        result = v4_payload_to_blueprint(_payload([_setprop(1)], extra=extra))
        assert result.dropped_settings == []

    def test_unknown_top_level_key_dropped(self):
        result = v4_payload_to_blueprint(_payload([_setprop(1)], extra={"goals": [{"x": 1}]}))
        assert "goals" in result.dropped_settings
        assert any(f["kind"] == "unknown_setting" for f in result.flags)

    def test_recovered_keys_not_flagged(self):
        # name / actions / startActionId etc. are consumed by the converter, not
        # dropped, even when populated.
        result = v4_payload_to_blueprint(_payload([_setprop(1)]))
        assert result.dropped_settings == []


class TestEnrollmentInverse:
    def test_list_based_enrollment_carried_verbatim(self):
        lfb = {"filterBranchType": "OR", "filters": [{"property": "lifecyclestage",
                "operation": {"operationType": "ENUMERATION", "operator": "IS_ANY_OF",
                              "values": ["lead"], "includeObjectsWithNoValueSet": False},
                "filterType": "PROPERTY"}], "filterBranchOperator": "OR"}
        ec = {"type": "LIST_BASED", "shouldReEnroll": False,
              "unEnrollObjectsNotMeetingCriteria": False, "listFilterBranch": lfb,
              "reEnrollmentTriggersFilterBranches": []}
        result = v4_payload_to_blueprint(_payload([_setprop(1)], enrollment=ec))
        assert result.blueprint["spec"]["enrollment"] == {"type": "LIST_BASED", "filter_branch": lfb}


class TestLearningLog:
    def test_append_and_dedupe_per_run(self, tmp_path):
        unknown = [
            {"action_type_id": "3-777", "field_names": ["a", "b"],
             "value_previews": {"a": "v1", "b": "x" * 200}, "note": "first"},
            {"action_type_id": "3-777", "field_names": ["c"],  # same id -> deduped
             "value_previews": {"c": "v2"}, "note": "dup"},
            {"action_type_id": "3-888", "field_names": ["d"],
             "value_previews": {"d": "v3"}, "note": "second"},
        ]
        path = record_unknown_actions("148408595", "78391", unknown,
                                      base_dir=tmp_path, recorded_at="2026-07-10T16:30:00Z")
        assert path.name == "blueprint_learning.jsonl"
        assert path.parent == tmp_path / "148408595"
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2  # 3-777 deduped to one
        rec0 = json.loads(lines[0])
        assert rec0["portal_id"] == "148408595"
        assert rec0["workflow_id"] == "78391"
        assert rec0["recorded_at"] == "2026-07-10T16:30:00Z"
        assert rec0["action_type_id"] == "3-777"
        assert rec0["field_names"] == ["a", "b"]
        assert rec0["value_previews"]["b"] == "x" * 100  # truncated to 100
        assert json.loads(lines[1])["action_type_id"] == "3-888"

    def test_empty_unknown_actions_writes_nothing(self, tmp_path):
        path = record_unknown_actions("148408595", "78391", [], base_dir=tmp_path)
        assert not path.exists()

    def test_append_across_runs(self, tmp_path):
        record_unknown_actions("148408595", "1",
                                [{"action_type_id": "3-1", "value_previews": {}}], base_dir=tmp_path,
                                recorded_at="t1")
        record_unknown_actions("148408595", "2",
                                [{"action_type_id": "3-2", "value_previews": {}}], base_dir=tmp_path,
                                recorded_at="t2")
        path = tmp_path / "148408595" / "blueprint_learning.jsonl"
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["recorded_at"] == "t1"
        assert json.loads(lines[1])["recorded_at"] == "t2"


class TestExtractionResultShape:
    def test_result_has_all_fields(self):
        result = v4_payload_to_blueprint(_payload([_setprop(1)]))
        assert isinstance(result, ExtractionResult)
        assert isinstance(result.blueprint, dict)
        assert isinstance(result.flags, list)
        assert isinstance(result.unknown_actions, list)
        assert isinstance(result.warnings, list)
        assert isinstance(result.dropped_settings, list)


# ---------------------------------------------------------------------------
# Real-payload fixtures: sanitized GET /automation/v4/flows/{id} responses from
# portal 148408595. These exercise unmodeled features (MANUAL enrollment,
# STATIC_BRANCH topology, portal-specific content_id, dropped re-enrollment
# sub-settings) the round-trip backbone cannot reach, so each asserts honest
# *partial* extraction — the flags/warnings/dropped_settings the extractor
# emits — rather than a full round-trip. IDs are synthetic; names, structure,
# content_id, and actionTypeIds are kept intact.
# ---------------------------------------------------------------------------
_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "v4_flows"


def _load_fixture(name: str) -> dict:
    with (_FIXTURE_DIR / f"{name}.json").open(encoding="utf-8") as fh:
        return json.load(fh)


class TestRealFixtures:
    def test_ticket_reply_received(self):
        # Ticket, 1 Set property, LIST_BASED; re-enrollment sub-settings dropped.
        r = v4_payload_to_blueprint(_load_fixture("9001_ticket_reply_received"))
        spec = r.blueprint["spec"]
        assert spec["object_type"] == "Ticket-based"
        assert len(spec["actions"]) == 1
        assert spec["actions"][0]["ui_action"] == "Set property value"
        assert spec["enrollment"]["type"] == "LIST_BASED"
        assert "enrollmentCriteria.shouldReEnroll" in r.dropped_settings
        assert "enrollmentCriteria.reEnrollmentTriggersFilterBranches" in r.dropped_settings
        assert [f["kind"] for f in r.flags] == ["dropped_setting", "dropped_setting"]
        assert r.unknown_actions == []
        assert r.blueprint["source"]["workflow_id"] == "9001"

    def test_manual_empty(self):
        # MANUAL enrollment the converter cannot rebuild; shouldReEnroll dropped + flagged.
        r = v4_payload_to_blueprint(_load_fixture("9002_manual_empty"))
        spec = r.blueprint["spec"]
        assert spec["object_type"] == "Ticket-based"
        assert spec["actions"] == []
        assert spec["enrollment"]["type"] == "MANUAL"
        assert any(f["kind"] == "unsupported_enrollment_type" for f in r.flags)
        assert "enrollmentCriteria.shouldReEnroll" in r.dropped_settings
        assert any("MANUAL" in w for w in r.warnings)
        assert r.blueprint["source"]["workflow_id"] == "9002"

    def test_ticket_closed_email(self):
        # Clean extraction of a Send marketing email with a portal-specific content_id.
        r = v4_payload_to_blueprint(_load_fixture("9003_ticket_closed_email"))
        spec = r.blueprint["spec"]
        assert spec["object_type"] == "Ticket-based"
        assert len(spec["actions"]) == 1
        assert spec["actions"][0]["ui_action"] == "Send marketing email"
        assert spec["actions"][0]["fields"]["content_id"] == "400567279818"
        assert any(f["kind"] == "content_id" and f["value"] == "400567279818" for f in r.flags)
        assert r.dropped_settings == []
        assert r.unknown_actions == []
        assert r.warnings == []
        assert r.blueprint["source"]["workflow_id"] == "9003"

    def test_ticket_email_sent(self):
        # LIST_BASED with a complex filter (BOOL + TIME_POINT + nested) carried verbatim.
        r = v4_payload_to_blueprint(_load_fixture("9004_ticket_email_sent"))
        spec = r.blueprint["spec"]
        assert spec["object_type"] == "Ticket-based"
        assert len(spec["actions"]) == 1
        assert spec["actions"][0]["ui_action"] == "Set property value"
        enrollment = spec["enrollment"]
        assert enrollment["type"] == "LIST_BASED"
        original = _load_fixture("9004_ticket_email_sent")["enrollmentCriteria"]["listFilterBranch"]
        assert enrollment["filter_branch"] == original  # carried verbatim, incl. BOOL filter
        ops = [f["operation"].get("operationType") for f in enrollment["filter_branch"].get("filters", [])]
        assert any(o == "BOOL" for o in ops), "BOOL filter must survive verbatim"
        assert "enrollmentCriteria.shouldReEnroll" in r.dropped_settings
        assert "enrollmentCriteria.reEnrollmentTriggersFilterBranches" in r.dropped_settings
        assert r.unknown_actions == []
        assert r.blueprint["source"]["workflow_id"] == "9004"

    def test_static_branch_complex(self):
        # Company workflow starting at a STATIC_BRANCH: start node kept raw,
        # downstream actions unreachable and warned, dataSources dropped.
        r = v4_payload_to_blueprint(_load_fixture("9005_static_branch_complex"))
        spec = r.blueprint["spec"]
        assert spec["object_type"] == "Company-based"
        assert len(spec["actions"]) == 1
        action = spec["actions"][0]
        assert action.get("raw") is True
        assert action["action_type_id"] == "STATIC_BRANCH"
        assert action["node"]["type"] == "STATIC_BRANCH"
        assert len(r.unknown_actions) == 1
        assert r.unknown_actions[0]["node_type"] == "STATIC_BRANCH"
        assert "dataSources" in r.dropped_settings
        assert "enrollmentCriteria.shouldReEnroll" in r.dropped_settings
        assert [f["kind"] for f in r.flags] == ["dropped_setting", "dropped_setting", "dropped_setting"]
        assert any("was not reached from startActionId" in w for w in r.warnings)
        assert r.blueprint["source"]["workflow_id"] == "9005"

    def test_company_scoring_clean_baseline(self):
        # Clean Company-based extraction: no flags, no dropped settings, no warnings.
        r = v4_payload_to_blueprint(_load_fixture("9006_company_scoring"))
        spec = r.blueprint["spec"]
        assert spec["object_type"] == "Company-based"
        assert len(spec["actions"]) == 1
        assert spec["actions"][0]["ui_action"] == "Set property value"
        assert r.dropped_settings == []
        assert r.flags == []
        assert r.unknown_actions == []
        assert r.warnings == []
        assert r.blueprint["source"]["workflow_id"] == "9006"