"""Inverse converter: turn a V4 Flows API payload into a reviewable blueprint.

Mirrors ``converter.blueprint_to_v4_payload`` field-for-field so a workflow
extracted from a portal can be re-created. The round-trip backbone
(``render -> to_v4 -> extract -> render -> to_v4``) keeps V4 payloads identical
across every shipped blueprint that forward-converts.

Honest partial extraction (R1): a single-branch rejoining ``LIST_BRANCH`` becomes
a native ``If/then branch`` + ``true_branch``; a multi-branch or non-rejoining
branch is kept raw with a warning (the forward converter refuses raw
``LIST_BRANCH`` nodes, so the user must rebuild those by hand). Unknown
``actionTypeId``s are kept raw and recorded for the learning log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hubspot_mcp.blueprints.workflows.action_type_map import (
    INVERSE_ACTION_TYPES,
    INVERSE_CUSTOM_OBJECT_MAP,
    INVERSE_EVENT_TYPE_MAP,
    INVERSE_OBJECT_TYPE_IDS,
    INVERSE_OPERATOR_MAP,
    AMBIGUOUS_EVENT_TYPE_IDS,
)

_UNIT_DELAY_WORD = {"MINUTES": "minutes", "DAYS": "days", "HOURS": "hours"}
_UNIT_DUE_CHAR = {"DAYS": "d", "HOURS": "h", "MINUTES": "m", "MONTHS": "month"}

# Payload keys the forward converter either recovers or intentionally
# hardcodes to a safe default. These are NOT flagged as dropped.
_RECOVERED_KEYS = {
    "name", "isEnabled", "objectTypeId", "flowType", "type",
    "enrollmentCriteria", "actions", "startActionId",
}
# Read-only workflow metadata the converter does not (and should not) re-create
# — auto-generated identifiers/timestamps. Silently ignored; ``id`` is captured
# into ``source.workflow_id`` and ``description`` into the blueprint instead.
_METADATA_KEYS = {
    "id", "uuid", "revisionId", "nextAvailableActionId", "description",
    "createdAt", "updatedAt", "crmObjectCreationStatus",
}
# Behavioral settings the forward converter hardcodes to empty/default. If a
# real workflow populates them, they cannot be re-created -> dropped_setting.
# (``dataSources`` is here, not in _RECOVERED_KEYS, because the converter
# hardcodes ``[]`` even though real workflows carry association data sources.)
_AUDITED_KEYS = {
    "timeWindows", "blockedDates", "customProperties", "dataSources",
    "suppressionListIds", "canEnrollFromSalesforce",
}


@dataclass
class ExtractionResult:
    blueprint: dict[str, Any]
    flags: list[dict[str, Any]] = field(default_factory=list)
    unknown_actions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dropped_settings: list[str] = field(default_factory=list)


def _invert_operator(api_op: str) -> str:
    if api_op in INVERSE_OPERATOR_MAP:
        return INVERSE_OPERATOR_MAP[api_op]
    return api_op.lower().replace("_", " ")


def _invert_branch_condition(filt: dict[str, Any]) -> dict[str, Any]:
    op = filt.get("operation", {}) or {}
    api_op = op.get("operator", "")
    op_type = op.get("operationType", "")
    ui: dict[str, Any] = {
        "Condition property": filt.get("property", ""),
        "Operator": _invert_operator(api_op),
    }
    if op_type == "ALL_PROPERTY":
        pass  # IS_KNOWN / IS_UNKNOWN carry no Value
    elif op_type == "NUMBER":
        ui["Value"] = op.get("value")
    else:  # ENUMERATION (and anything else with `values`)
        vals = op.get("values", [])
        if isinstance(vals, list) and len(vals) == 1:
            ui["Value"] = vals[0]
        else:
            ui["Value"] = vals
    return ui


def _extract_branch_condition(node: dict[str, Any]) -> dict[str, Any]:
    try:
        filt = node["listBranches"][0]["filterBranch"]["filterBranches"][0]["filters"][0]
    except (KeyError, IndexError, TypeError):
        return {}
    return _invert_branch_condition(filt)


def _delay_label(delta: Any, unit: str) -> str:
    word = _UNIT_DELAY_WORD.get(unit, (unit or "minutes").lower())
    return f"{delta} {word}"


def _due_label(due_time: dict[str, Any]) -> str:
    delta = due_time.get("delta", 1)
    unit = due_time.get("timeUnit", "DAYS")
    char = _UNIT_DUE_CHAR.get(unit, "d")
    return f"{{{{timestamp + {delta}{char}}}}}"


def _strip_p(body: str) -> str:
    if isinstance(body, str) and body.startswith("<p>") and body.endswith("</p>"):
        return body[3:-4]
    return body


def _invert_action(node: dict[str, Any]) -> dict[str, Any] | None:
    """Return a UI action dict for a known actionTypeId, or None if unknown."""
    action_type_id = node.get("actionTypeId", "")
    ui_action = INVERSE_ACTION_TYPES.get(action_type_id)
    if ui_action is None:
        return None
    f = node.get("fields", {}) or {}

    if ui_action == "Delay":
        return {"ui_action": "Delay", "fields": {"Delay for": _delay_label(f.get("delta"), f.get("time_unit", "MINUTES"))}}
    if ui_action == "Delay until date":
        prop = (f.get("date") or {}).get("propertyName", "")
        return {"ui_action": "Delay until date", "fields": {"Delay until": f"{{{{contact.{prop}}}}}" if prop else ""}}
    if ui_action == "Create task":
        priority = (f.get("priority") or "NONE").capitalize()
        return {"ui_action": "Create task", "fields": {
            "Title": f.get("subject", ""),
            "Priority": priority,
            "Notes": f.get("body", ""),
            "Due date": _due_label(f.get("due_time") or {}),
        }}
    if ui_action == "Set property value":
        value = f.get("value") or {}
        # The forward converter only models STATIC_VALUE. INCREMENT (and any
        # other mode) can't be re-created natively -> keep raw so it is not
        # silently flattened to an empty static value.
        if value.get("type") not in ("STATIC_VALUE", "", None):
            return None
        return {"ui_action": "Set property value", "fields": {
            "Property": f.get("property_name", ""),
            "Value": value.get("staticValue", ""),
        }}
    if ui_action == "Send internal email notification":
        # Reconstruct the ``Send to`` UI field so the forward converter's
        # ``"hubspot_owner_id" in send_to`` check reproduces owner_properties.
        owner_props = f.get("owner_properties") or []
        if "hubspot_owner_id" in owner_props:
            send_to = "{{contact.hubspot_owner_id}}"
        else:
            send_to = ""
        return {"ui_action": "Send internal email notification", "fields": {
            "Send to": send_to,
            "Subject": f.get("subject", ""),
            "Body": _strip_p(f.get("body", "")),
        }}
    if ui_action == "Send marketing email":
        return {"ui_action": "Send marketing email", "fields": {"content_id": f.get("content_id", "")}}
    if ui_action == "Add to static list":
        return {"ui_action": "Add to static list", "fields": {"List": f.get("listId", "")}}
    if ui_action == "Rotate leads":
        team_ids = f.get("team_ids") or []
        return {"ui_action": "Rotate leads", "fields": {"Rotate to": team_ids[0] if team_ids else ""}}
    return None


def _raw_action(node: dict[str, Any], note: str = "") -> dict[str, Any]:
    return {
        "raw": True,
        "action_type_id": node.get("actionTypeId") or node.get("type") or "UNKNOWN",
        "node": node,
        "note": note,
    }


def _unknown_entry(node: dict[str, Any]) -> dict[str, Any]:
    import json
    fields = node.get("fields", {}) or {}
    previews: dict[str, str] = {}
    for k, v in fields.items():
        s = v if isinstance(v, str) else json.dumps(v, default=str)
        previews[k] = s[:100]
    return {
        "action_type_id": node.get("actionTypeId", ""),
        "node_type": node.get("type", ""),
        "field_names": list(fields.keys()),
        "value_previews": previews,
    }


def _flag_portal_specific(node: dict[str, Any], ui_action: dict[str, Any], result: ExtractionResult) -> None:
    """Flag portal-specific IDs that won't exist in another portal."""
    action_id = node.get("actionId", "?")
    f = node.get("fields", {}) or {}
    label = ui_action.get("ui_action", "")

    def _is_real(value: Any) -> bool:
        return bool(value) and not str(value).startswith("<")

    if label == "Add to static list" and _is_real(f.get("listId")):
        result.flags.append({"path": f"actions[{action_id}].List", "kind": "list_id",
                             "value": f.get("listId"), "suggestion": "Static list ID is portal-specific; replace or parameterize before re-creating."})
    elif label == "Send marketing email" and _is_real(f.get("content_id")):
        result.flags.append({"path": f"actions[{action_id}].content_id", "kind": "content_id",
                             "value": f.get("content_id"), "suggestion": "Marketing email content_id is portal-specific; replace or parameterize."})
    elif label == "Rotate leads" and _is_real((f.get("team_ids") or [None])[0]):
        result.flags.append({"path": f"actions[{action_id}].Rotate to", "kind": "team_id",
                             "value": (f.get("team_ids") or [None])[0], "suggestion": "Team ID is portal-specific; replace or parameterize."})
    user_ids = f.get("user_ids") or []
    if isinstance(user_ids, list) and user_ids:
        result.flags.append({"path": f"actions[{action_id}].user_ids", "kind": "user_ids",
                             "value": user_ids, "suggestion": "User IDs are portal-specific; replace or parameterize."})
    owner_props = f.get("owner_properties") or []
    if owner_props:
        result.flags.append({"path": f"actions[{action_id}].owner_properties", "kind": "owner_properties",
                             "value": owner_props, "suggestion": "Owner-property recipients are carried verbatim; verify in the target portal."})


def _walk(start_id: str | None, stop_id: str | None, by_id: dict[str, dict[str, Any]],
          result: ExtractionResult, visited: set[str]) -> tuple[list[dict[str, Any]], bool]:
    """Walk the action graph from ``start_id`` until ``stop_id`` (exclusive).

    Returns ``(actions, reached_stop)``. ``reached_stop`` is True iff the chain
    halted because it rejoined at ``stop_id`` (or ``start_id == stop_id``);
    False if it ran off the end or hit a dangling pointer -> non-rejoining
    topology, and the caller must fall back to a raw node. ``visited`` collects
    every actionId touched so the caller can warn about actions that were never
    reached (e.g. downstream of an un-traversable branch type).
    """
    actions: list[dict[str, Any]] = []
    current: str | None = start_id
    if current == stop_id:
        return actions, True  # empty branch trivially rejoined

    while current is not None and current != stop_id:
        node = by_id.get(str(current))
        if node is None:
            return actions, False  # dangling pointer
        visited.add(str(current))
        ntype = node.get("type")

        if ntype == "LIST_BRANCH":
            # The true-edge ``connection`` lives inside ``listBranches[0]``
            # (see converter ``_build_branch_node``), while the default/rejoin
            # edge lives at the node top level as ``defaultBranch``.
            lb = (node.get("listBranches") or [{}])[0] or {}
            true_start = (lb.get("connection") or {}).get("nextActionId")
            rejoin = (node.get("defaultBranch") or {}).get("nextActionId")
            true_actions, reached = _walk(true_start, rejoin, by_id, result, visited)
            if not reached:
                result.warnings.append(
                    f"Branch action {node.get('actionId')} does not re-join cleanly; "
                    "kept as raw. Its true-branch actions were not extracted and the "
                    "forward converter will refuse to re-create it — rebuild by hand."
                )
                result.unknown_actions.append(_unknown_entry(node))
                actions.append(_raw_action(node, "non-rejoining LIST_BRANCH; rebuild by hand"))
                current = rejoin
                continue
            cond = _extract_branch_condition(node)
            actions.append({"ui_action": "If/then branch", "fields": cond, "true_branch": true_actions})
            current = rejoin
        else:
            inv = _invert_action(node)
            if inv is None:
                result.warnings.append(
                    f"Action {node.get('actionId')} (typeId {node.get('actionTypeId')}) "
                    "is not natively modeled; kept as raw."
                )
                result.unknown_actions.append(_unknown_entry(node))
                actions.append(_raw_action(node, "unknown actionTypeId; kept verbatim"))
            else:
                _flag_portal_specific(node, inv, result)
                actions.append(inv)
            current = (node.get("connection") or {}).get("nextActionId")

    return actions, (current == stop_id)


def _flag_enrollment_subsettings(ec: dict[str, Any], result: ExtractionResult) -> None:
    """The forward converter hardcodes these enrollment sub-fields to defaults;
    if the real workflow uses non-default values they are lost on re-creation."""
    if ec.get("shouldReEnroll"):
        result.dropped_settings.append("enrollmentCriteria.shouldReEnroll")
        result.flags.append({
            "path": "spec.enrollment.shouldReEnroll", "kind": "dropped_setting", "value": True,
            "suggestion": "Re-enrollment is not modeled; the re-created workflow will not re-enroll.",
        })
        result.warnings.append("Enrollment shouldReEnroll=true is not modeled; it will be dropped on re-creation.")
    if ec.get("unEnrollObjectsNotMeetingCriteria"):
        result.dropped_settings.append("enrollmentCriteria.unEnrollObjectsNotMeetingCriteria")
        result.flags.append({
            "path": "spec.enrollment.unEnrollObjectsNotMeetingCriteria", "kind": "dropped_setting", "value": True,
            "suggestion": "Un-enroll-on-criteria-mismatch is not modeled; it will be dropped on re-creation.",
        })
        result.warnings.append("Enrollment unEnrollObjectsNotMeetingCriteria=true is not modeled; it will be dropped on re-creation.")
    re_enroll = ec.get("reEnrollmentTriggersFilterBranches") or []
    if re_enroll:
        result.dropped_settings.append("enrollmentCriteria.reEnrollmentTriggersFilterBranches")
        result.flags.append({
            "path": "spec.enrollment.reEnrollmentTriggersFilterBranches", "kind": "dropped_setting",
            "value": re_enroll,
            "suggestion": "Re-enrollment trigger filters are not modeled; they will be dropped on re-creation.",
        })
        result.warnings.append("Enrollment reEnrollmentTriggersFilterBranches is not modeled; it will be dropped on re-creation.")


def _invert_enrollment(ec: dict[str, Any], result: ExtractionResult) -> dict[str, Any]:
    etype = ec.get("type", "LIST_BASED")
    # Sub-settings audit applies to every enrollment type.
    _flag_enrollment_subsettings(ec, result)
    if etype == "LIST_BASED":
        # The forward converter collapses PROPERTY_BASED and LIST_BASED into V4
        # "LIST_BASED"; the inverse cannot distinguish them, so round-trip is
        # only guaranteed at the V4-payload level (both map to the same V4 type).
        return {"type": "LIST_BASED", "filter_branch": ec.get("listFilterBranch") or {}}
    if etype == "EVENT_BASED":
        branches = ec.get("eventFilterBranches") or [{}]
        efb = branches[0] if branches else {}
        event_type_id = efb.get("eventTypeId", "")
        trigger = INVERSE_EVENT_TYPE_MAP.get(event_type_id, f"<unknown event {event_type_id}>")
        if event_type_id in AMBIGUOUS_EVENT_TYPE_IDS:
            result.flags.append({
                "path": "spec.enrollment.trigger",
                "kind": "ambiguous_event_type",
                "value": trigger,
                "suggestion": "Several UI triggers map to this eventTypeId; verify in the HubSpot UI.",
            })
        if event_type_id not in INVERSE_EVENT_TYPE_MAP:
            result.flags.append({
                "path": "spec.enrollment.trigger",
                "kind": "unknown_event_type",
                "value": event_type_id,
                "suggestion": "eventTypeId is not in the known map; verify before re-creating.",
            })
        filters = efb.get("filters", []) or []
        if filters:
            filter_branch = {
                "filterBranchType": "OR", "filterBranchOperator": "OR", "filters": [],
                "filterBranches": [{"filterBranchType": "AND", "filterBranchOperator": "AND", "filters": filters}],
            }
        else:
            filter_branch = {}
        return {"type": "EVENT_BASED", "trigger": trigger, "filter_branch": filter_branch}
    # MANUAL or any other enrollment type the forward converter can't build.
    result.flags.append({
        "path": "spec.enrollment.type", "kind": "unsupported_enrollment_type", "value": etype,
        "suggestion": f"Enrollment type '{etype}' cannot be re-created by the converter; rebuild by hand.",
    })
    result.warnings.append(
        f"Enrollment type '{etype}' cannot be re-created by the converter; "
        "rebuild the enrollment by hand after extraction."
    )
    return {"type": etype}


def _invert_object_type(object_type_id: str, result: ExtractionResult) -> str:
    if object_type_id in INVERSE_OBJECT_TYPE_IDS:
        return INVERSE_OBJECT_TYPE_IDS[object_type_id]
    if str(object_type_id).startswith("2-"):
        # Flag every custom object as portal-specific (these IDs only exist in
        # one portal). If the ID is a known 148408595 custom object, invert to
        # its name so the shipped blueprints round-trip; otherwise keep the ID
        # verbatim so the user can see what they're dealing with.
        result.flags.append({
            "path": "spec.object_type", "kind": "custom_object_type", "value": object_type_id,
            "suggestion": "Portal-specific custom objectTypeId; verify it exists in the target portal.",
        })
        name = INVERSE_CUSTOM_OBJECT_MAP.get(object_type_id)
        if name is not None:
            return f"Custom object ({name})"
        return f"Custom object ({object_type_id})"
    return f"Unknown object ({object_type_id})"


def _audit_settings(payload: dict[str, Any], result: ExtractionResult) -> None:
    for k, v in payload.items():
        if k in _RECOVERED_KEYS or k in _METADATA_KEYS:
            continue  # consumed by the converter, or read-only metadata
        if k in _AUDITED_KEYS:
            if v in (None, "", [], {}, False, 0):
                continue
            result.dropped_settings.append(k)
            result.flags.append({
                "path": f"<root>.{k}", "kind": "dropped_setting", "value": v,
                "suggestion": f"Workflow setting '{k}' is not modeled and will not be re-created.",
            })
            result.warnings.append(f"Workflow setting '{k}' is not modeled; it will be dropped on re-creation.")
        else:
            # Unknown top-level key — a genuinely unmodeled behavioral setting.
            if v in (None, "", [], {}, False, 0):
                continue
            result.dropped_settings.append(k)
            result.flags.append({
                "path": f"<root>.{k}", "kind": "unknown_setting", "value": v,
                "suggestion": f"Unknown workflow setting '{k}'; not modeled, will not be re-created.",
            })
            result.warnings.append(f"Unknown workflow setting '{k}' will be dropped on re-creation.")


def v4_payload_to_blueprint(payload: dict[str, Any]) -> ExtractionResult:
    """Extract a reviewable blueprint dict from a V4 Flows API payload."""
    result = ExtractionResult(blueprint={})

    object_type = _invert_object_type(payload.get("objectTypeId", "0-1"), result)
    enrollment = _invert_enrollment(payload.get("enrollmentCriteria") or {}, result)

    by_id = {str(n.get("actionId")): n for n in (payload.get("actions") or []) if isinstance(n, dict)}
    start_id = payload.get("startActionId")
    visited: set[str] = set()
    actions, _ = _walk(start_id, None, by_id, result, visited)

    # Actions that exist in the payload but were never reached from
    # startActionId — typically downstream of an un-traversable branch type
    # (e.g. STATIC_BRANCH) that the walker can't follow. Honest warning: those
    # actions are not represented in the extracted blueprint.
    for aid, node in by_id.items():
        if aid in visited:
            continue
        result.warnings.append(
            f"Action {aid} (typeId {node.get('actionTypeId')}, type {node.get('type')}) "
            "was not reached from startActionId — likely downstream of an un-traversable "
            "branch — and was not extracted."
        )

    _audit_settings(payload, result)

    blueprint = {
        "format_version": 1,
        "name": payload.get("name") or "Extracted workflow",
        "description": payload.get("description")
            or "Extracted from an existing HubSpot workflow; review and parameterize before re-use.",
        "tags": ["extracted"],
        "source": {
            "origin": "extracted",
            "portal_id": None,
            "workflow_id": str(payload["id"]) if payload.get("id") is not None else None,
            "extracted_at": None,
        },
        "notes": list(result.warnings),
        "flags": list(result.flags),
        "parameters": {},
        "spec": {
            "ui_path": "Settings > Automation > Workflows > Create workflow",
            "object_type": object_type,
            "enrollment": enrollment,
            "actions": actions,
            "prerequisites": [],
            "validation": [],
        },
    }
    result.blueprint = blueprint
    return result