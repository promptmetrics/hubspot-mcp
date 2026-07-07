"""Convert WorkflowBlueprint UI specs to HubSpot V4 Flows API payloads.

Source: reverse-engineered from portal 148408595 via GET /automation/v4/flows/{id}
"""

from __future__ import annotations

import re
from typing import Any

from hubspot_mcp.blueprints.workflows.action_type_map import (
    ACTION_TYPE_REGISTRY,
    BRANCH_TYPES,
    EVENT_TYPE_MAP,
    resolve_flow_type,
    resolve_object_type_id,
)


def _parse_delay(fields: dict[str, Any]) -> tuple[int, str]:
    raw = fields.get("Delay for", "")
    m = re.match(r"(\d+)\s*(hour|hours)", raw, re.I)
    if m:
        return int(m.group(1)) * 60, "MINUTES"
    m = re.match(r"(\d+)\s*(minute|minutes|min|m)", raw, re.I)
    if m:
        return int(m.group(1)), "MINUTES"
    m = re.match(r"(\d+)\s*(day|days|d)", raw, re.I)
    if m:
        return int(m.group(1)), "DAYS"
    raise ValueError(f"Cannot parse delay: {raw}")


def _parse_due(raw: str) -> tuple[int, str]:
    if "{{" in raw and "timestamp" not in raw:
        raise ValueError(
            f"Property-relative due dates are not supported by the V4 API: {raw}"
        )
    m = re.search(r"\+\s*(\d+)\s*(h|hour|hours)", raw)
    if m:
        return int(m.group(1)), "HOURS"
    m = re.search(r"\+\s*(\d+)\s*(m|min|minute|minutes)", raw)
    if m:
        return int(m.group(1)), "MINUTES"
    m = re.search(r"\+\s*(\d+)\s*(d|day|days)", raw)
    if m:
        return int(m.group(1)), "DAYS"
    m = re.search(r"\+\s*(\d+)\s*(month|months)", raw)
    if m:
        return int(m.group(1)), "MONTHS"
    m = re.search(r"\+\s*(\d+)\s*(year|years)", raw)
    if m:
        return int(m.group(1)) * 12, "MONTHS"
    raise ValueError(f"Cannot parse due date: {raw}")


def _parse_delay_until(fields: dict[str, Any]) -> str:
    raw = fields.get("Delay until", "")
    m = re.match(r"\{\{contact\.(.+?)\}\}", raw)
    if m:
        return m.group(1)
    m = re.match(r"\{\{object\.(.+?)\}\}", raw)
    if m:
        return m.group(1)
    m = re.match(r"\{\{(.+?)\}\}", raw)
    if m:
        return m.group(1)
    if raw and not raw.startswith("<"):
        return raw
    raise ValueError(f"Cannot parse delay until date: {raw}")


def _parse_rotate_leads(fields: dict[str, Any]) -> str:
    team_id = fields.get("Rotate to", "")
    if not team_id or team_id.startswith("<"):
        raise ValueError("Rotate leads requires a valid team_id (not a placeholder)")
    return team_id


def _count_actions(actions: list[dict[str, Any]]) -> int:
    count = 0
    for action in actions:
        count += 1
        if "true_branch" in action:
            count += _count_actions(action["true_branch"])
    return count


def _size(action: dict[str, Any]) -> int:
    s = 1
    if "true_branch" in action:
        s += _count_actions(action["true_branch"])
    return s


def _build_action_node(
    action_id: int,
    action: dict[str, Any],
    next_id: int | None,
) -> dict[str, Any]:
    ui_action = action["ui_action"]
    fields = action.get("fields", {})

    if ui_action in ACTION_TYPE_REGISTRY:
        reg = ACTION_TYPE_REGISTRY[ui_action]
        node: dict[str, Any] = {
            "actionId": str(action_id),
            "actionTypeVersion": reg["actionTypeVersion"],
            "actionTypeId": reg["actionTypeId"],
            "type": reg["type"],
        }

        if ui_action == "Delay":
            delta, unit = _parse_delay(fields)
            node["fields"] = reg["build_fields"](delta, unit)

        elif ui_action == "Delay until date":
            property_name = _parse_delay_until(fields)
            node["fields"] = reg["build_fields"](property_name)

        elif ui_action == "Create task":
            subject = fields.get("Title", "")
            priority = fields.get("Priority", "NONE").upper()
            body = fields.get("Notes", "")
            due_raw = fields.get("Due date", "")
            due_delta, due_unit = _parse_due(due_raw) if due_raw else (1, "DAYS")
            node["fields"] = reg["build_fields"](
                subject=subject,
                priority=priority,
                due_delta=due_delta,
                due_time_unit=due_unit,
                body=body,
            )

        elif ui_action == "Set property value":
            prop = fields.get("Property", "")
            val = fields.get("Value", "")
            node["fields"] = reg["build_fields"](prop, val)

        elif ui_action == "Send internal email notification":
            subject = fields.get("Subject", "")
            body = fields.get("Body", "")
            send_to = fields.get("Send to", "")
            owner_properties = None
            if "hubspot_owner_id" in send_to:
                owner_properties = ["hubspot_owner_id"]
            node["fields"] = reg["build_fields"](
                subject, body, owner_properties=owner_properties
            )

        elif ui_action == "Send marketing email":
            content_id = fields.get("content_id", "")
            if not content_id or content_id == "<create email first>":
                raise ValueError(
                    "Send marketing email requires a valid content_id (not a placeholder)"
                )
            node["fields"] = reg["build_fields"](content_id)

        elif ui_action == "Add to static list":
            list_id = fields.get("List", "")
            if not list_id:
                raise ValueError("Add to static list requires a list_id")
            node["fields"] = reg["build_fields"](list_id)

        elif ui_action == "Rotate leads":
            team_id = _parse_rotate_leads(fields)
            node["fields"] = reg["build_fields"](team_id)

        if next_id is not None:
            node["connection"] = {"edgeType": "STANDARD", "nextActionId": str(next_id)}
        return node

    raise ValueError(f"Unsupported ui_action: {ui_action}")


def _build_branch_node(
    action_id: int,
    action: dict[str, Any],
    yes_next_id: int | None,
    default_next_id: int | None,
) -> dict[str, Any]:
    ui_action = action["ui_action"]
    fields = action.get("fields", {})

    if ui_action == "If/then branch":
        cond_property = fields.get("Condition property", "")
        operator = fields.get("Operator", "")
        value = fields.get("Value", "")

        op_map = {
            "is equal to any of": "IS_ANY_OF",
            "is not equal to any of": "IS_NONE_OF",
            "is known": "IS_KNOWN",
            "is unknown": "IS_UNKNOWN",
        }
        api_op = op_map.get(operator, operator.upper().replace(" ", "_"))

        if api_op in ("IS_KNOWN", "IS_UNKNOWN"):
            op_type = "ALL_PROPERTY"
            operation: dict[str, Any] = {
                "operationType": op_type,
                "operator": api_op,
                "includeObjectsWithNoValueSet": False,
            }
        elif api_op in (
            "IS_EQUAL_TO",
            "IS_NOT_EQUAL_TO",
            "IS_GREATER_THAN",
            "IS_GREATER_THAN_OR_EQUAL_TO",
            "IS_LESS_THAN",
            "IS_LESS_THAN_OR_EQUAL_TO",
        ):
            operation = {
                "operationType": "NUMBER",
                "operator": api_op,
                "value": int(value) if str(value).isdigit() else value,
                "includeObjectsWithNoValueSet": False,
            }
        else:
            op_type = "ENUMERATION"
            values = [value] if isinstance(value, str) else value
            operation = {
                "operationType": op_type,
                "operator": api_op,
                "values": values,
                "includeObjectsWithNoValueSet": False,
            }

        branch = {
            "filterBranch": {
                "filterBranches": [
                    {
                        "filterBranches": [],
                        "filters": [
                            {
                                "property": cond_property,
                                "operation": operation,
                                "filterType": "PROPERTY",
                            }
                        ],
                        "filterBranchType": "AND",
                        "filterBranchOperator": "AND",
                    }
                ],
                "filters": [],
                "filterBranchType": "OR",
                "filterBranchOperator": "OR",
            },
            "branchName": f"{cond_property} {operator} {value}"[:50],
        }
        if yes_next_id is not None:
            branch["connection"] = {
                "edgeType": "STANDARD",
                "nextActionId": str(yes_next_id),
            }

        result: dict[str, Any] = {
            "actionId": str(action_id),
            "listBranches": [branch],
            "type": "LIST_BRANCH",
        }
        if default_next_id is not None:
            result["defaultBranch"] = {
                "edgeType": "STANDARD",
                "nextActionId": str(default_next_id),
            }
        return result

    raise ValueError(f"Unsupported branch type: {ui_action}")


def _build_enrollment_criteria(enrollment: dict[str, Any]) -> dict[str, Any]:
    enc_type = enrollment.get("type", "LIST_BASED")

    if enc_type == "PROPERTY_BASED" or enc_type == "LIST_BASED":
        filter_branch = enrollment.get("filter_branch", {})
        return {
            "type": "LIST_BASED",
            "shouldReEnroll": False,
            "unEnrollObjectsNotMeetingCriteria": False,
            "listFilterBranch": filter_branch,
            "reEnrollmentTriggersFilterBranches": [],
        }

    if enc_type == "EVENT_BASED":
        trigger = enrollment.get("trigger") or enrollment.get("event", "")
        event_type_id = EVENT_TYPE_MAP.get(trigger)
        if not event_type_id:
            raise ValueError(
                f"EVENT_BASED enrollment trigger '{trigger}' is not supported. "
                f"Supported triggers: {list(EVENT_TYPE_MAP.keys())}"
            )
        filter_branch = enrollment.get("filter_branch", {})
        # Flatten nested filters into the top-level event filter branch.
        # The V4 API does not allow nested filterBranches inside eventFilterBranches.
        filters: list[dict[str, Any]] = []
        for nested in filter_branch.get("filterBranches", []):
            filters.extend(nested.get("filters", []))
        if not filters:
            filters = filter_branch.get("filters", [])

        return {
            "type": "EVENT_BASED",
            "shouldReEnroll": False,
            "unEnrollObjectsNotMeetingCriteria": False,
            "eventFilterBranches": [
                {
                    "eventTypeId": event_type_id,
                    "filterBranchType": "UNIFIED_EVENTS",
                    "operator": "HAS_COMPLETED",
                    "filterBranches": [],
                    "filters": filters,
                    "filterBranchOperator": "AND",
                }
            ],
            "reEnrollmentTriggersFilterBranches": [],
        }

    raise ValueError(f"Unsupported enrollment type: {enc_type}")


def _build_graph(
    actions: list[dict[str, Any]],
    start_id: int,
    after_id: int | None,
) -> tuple[list[dict[str, Any]], int]:
    """Recursively build action nodes with correct graph connections."""
    nodes: list[dict[str, Any]] = []
    current_id = start_id

    for i, action in enumerate(actions):
        action_id = current_id
        is_last = i == len(actions) - 1
        this_size = _size(action)

        if is_last:
            continuation_id = after_id
        else:
            continuation_id = action_id + this_size

        if "true_branch" in action:
            true_branch_start = action_id + 1
            true_branch_nodes, _ = _build_graph(
                action["true_branch"], true_branch_start, continuation_id
            )

            yes_next_id = true_branch_start if true_branch_nodes else continuation_id
            branch_node = _build_branch_node(
                action_id, action, yes_next_id, continuation_id
            )
            nodes.append(branch_node)
            nodes.extend(true_branch_nodes)
            current_id = action_id + this_size
        else:
            node = _build_action_node(action_id, action, continuation_id)
            nodes.append(node)
            current_id += 1

    return nodes, current_id


def blueprint_to_v4_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """Convert a blueprint UI spec to a V4 Flows API payload."""
    name = spec.get("name", "Untitled workflow")
    object_type = spec.get("object_type", "Contact-based")
    enrollment = spec.get("enrollment", {})
    actions = spec.get("actions", [])

    object_type_id = resolve_object_type_id(object_type)
    flow_type = resolve_flow_type(object_type_id)

    enrollment_criteria = _build_enrollment_criteria(enrollment)
    action_nodes, _ = _build_graph(actions, start_id=1, after_id=None)

    start_action_id = "1" if action_nodes else None

    payload: dict[str, Any] = {
        "name": name,
        "isEnabled": False,
        "objectTypeId": object_type_id,
        "flowType": "WORKFLOW",
        "type": flow_type,
        "enrollmentCriteria": enrollment_criteria,
        "timeWindows": [],
        "blockedDates": [],
        "customProperties": {},
        "dataSources": [],
        "suppressionListIds": [],
        "canEnrollFromSalesforce": False,
    }

    if start_action_id:
        payload["startActionId"] = start_action_id
        payload["actions"] = action_nodes

    return payload
