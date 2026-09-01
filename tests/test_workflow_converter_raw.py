"""Phase 3: forward converter handling of raw action nodes.

A raw action carries a verbatim V4 node captured by the extractor so
unknown / portal-specific actions can still be re-created. The converter
deep-copies it, stamps a fresh ``actionId``, and rewires (or strips) its
``connection`` to the neighbor. Raw ``LIST_BRANCH`` nodes are refused —
their branch edges can't be rewired generically.
"""
from __future__ import annotations

import copy

import pytest

from hubspot_mcp.blueprints.workflows.converter import blueprint_to_v4_payload


def _raw(node: dict, note: str = "captured action") -> dict:
    return {
        "raw": True,
        "action_type_id": node.get("actionTypeId", "3-999"),
        "node": node,
        "note": note,
    }


def _set_property(prop: str, val: str) -> dict:
    return {"ui_action": "Set property value", "fields": {"Property": prop, "Value": val}}


def _spec(actions: list[dict]) -> dict:
    return {
        "name": "Raw Test",
        "object_type": "Contact-based",
        "enrollment": {"type": "EVENT_BASED", "trigger": "Contact is created"},
        "actions": actions,
    }


def _raw_node(action_type_id="3-999", custom_fields=None, connection=None) -> dict:
    node: dict = {
        "actionId": "999",  # stale -> must be overwritten
        "actionTypeVersion": 1,
        "actionTypeId": action_type_id,
        "type": "ACTION",
        "fields": custom_fields or {"some_portal_field": "value"},
    }
    if connection is not None:
        node["connection"] = connection
    else:
        # stale connection to a neighbor that won't exist post-rewire
        node["connection"] = {"edgeType": "STANDARD", "nextActionId": "888"}
    return node


class TestRawActionRoundTrip:
    def test_raw_mid_sequence_connects_to_next_neighbor(self):
        node = _raw_node()
        spec = _spec([_raw(node), _set_property("lifecyclestage", "lead")])
        payload = blueprint_to_v4_payload(spec)

        actions = payload["actions"]
        # raw node first: fresh actionId, connection rewired to the next node
        raw_out = actions[0]
        assert raw_out["actionId"] == "1"
        assert raw_out["actionTypeId"] == "3-999"
        assert raw_out["type"] == "ACTION"
        assert raw_out["connection"] == {"edgeType": "STANDARD", "nextActionId": "2"}
        # the following native node is actionId 2, last -> no connection
        assert actions[1]["actionId"] == "2"
        assert "connection" not in actions[1]

    def test_raw_last_in_sequence_strips_stale_connection(self):
        node = _raw_node(connection={"edgeType": "STANDARD", "nextActionId": "888"})
        spec = _spec([_set_property("lifecyclestage", "lead"), _raw(node)])
        payload = blueprint_to_v4_payload(spec)

        actions = payload["actions"]
        # native node first connects to raw; raw is last -> stale connection stripped
        assert actions[0]["actionId"] == "1"
        assert actions[0]["connection"]["nextActionId"] == "2"
        raw_out = actions[1]
        assert raw_out["actionId"] == "2"
        assert "connection" not in raw_out

    def test_raw_fields_preserved_verbatim(self):
        custom = {
            "objectTypeId": "2-7",
            "associationCategory": "HUBSPOT_DEFINED",
            "toObjectTypeIds": [{"toObjectTypeId": "2-8", "associationTypeId": 5}],
            "a_string": "keep me",
            "a_number": 42,
            "a_bool": True,
            "a_list": ["x", "y"],
        }
        node = _raw_node(custom_fields=custom)
        spec = _spec([_raw(node), _set_property("x", "y")])
        payload = blueprint_to_v4_payload(spec)

        raw_out = payload["actions"][0]
        # every stored field is carried through untouched
        assert raw_out["fields"] == custom
        # only actionId + connection were mutated
        assert raw_out["actionId"] == "1"

    def test_raw_does_not_mutate_stored_node(self):
        node = _raw_node()
        original = copy.deepcopy(node)
        spec = _spec([_raw(node), _set_property("x", "y")])
        blueprint_to_v4_payload(spec)
        # the caller's stored node is left intact (deep copy, not in-place edit)
        assert node == original

    def test_raw_only_action_has_no_connection(self):
        node = _raw_node(connection={"edgeType": "STANDARD", "nextActionId": "888"})
        spec = _spec([_raw(node)])
        payload = blueprint_to_v4_payload(spec)

        raw_out = payload["actions"][0]
        assert raw_out["actionId"] == "1"
        assert "connection" not in raw_out


class TestRawBranchRefusal:
    def test_raw_list_branch_raises(self):
        list_branch_node = {
            "actionId": "999",
            "type": "LIST_BRANCH",
            "listBranches": [{"filterBranch": {}}],
            "defaultBranch": {"edgeType": "STANDARD", "nextActionId": "5"},
        }
        spec = _spec([_raw(list_branch_node), _set_property("x", "y")])
        with pytest.raises(ValueError, match="LIST_BRANCH"):
            blueprint_to_v4_payload(spec)

    def test_raw_missing_node_raises(self):
        spec = _spec([{"raw": True, "action_type_id": "3-1", "note": "x"}, _set_property("x", "y")])
        with pytest.raises(ValueError, match="missing its stored 'node'"):
            blueprint_to_v4_payload(spec)


class TestRawEnrollmentIndependent:
    def test_raw_works_with_list_based_enrollment(self):
        node = _raw_node()
        spec = {
            "name": "Raw List Enroll",
            "object_type": "Contact-based",
            "enrollment": {"type": "LIST_BASED", "filter_branch": {"filters": []}},
            "actions": [_raw(node), _set_property("x", "y")],
        }
        payload = blueprint_to_v4_payload(spec)
        assert payload["enrollmentCriteria"]["type"] == "LIST_BASED"
        assert payload["actions"][0]["actionId"] == "1"
        assert payload["actions"][0]["connection"]["nextActionId"] == "2"