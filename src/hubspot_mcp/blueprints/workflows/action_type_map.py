"""Mapping of HubSpot Workflow UI actions to V4 Flows API actionTypeIds and field schemas.

Source: reverse-engineered from portal 148408595 via GET /automation/v4/flows/{id}
"""

from __future__ import annotations

from typing import Any


def _delay_fields(delta: int, time_unit: str = "MINUTES") -> dict[str, Any]:
    return {"delta": str(delta), "time_unit": time_unit}


def _delay_until_fields(
    property_name: str,
    delta: int = 0,
    time_unit: str = "MINUTES",
    hour: int = 8,
    minute: int = 30,
) -> dict[str, Any]:
    return {
        "date": {"propertyName": property_name, "type": "OBJECT_PROPERTY"},
        "delta": str(delta),
        "time_unit": time_unit,
        "time_of_day": {"hour": hour, "minute": minute},
    }


def _create_task_fields(
    subject: str,
    priority: str = "NONE",
    due_delta: int = 1,
    due_time_unit: str = "DAYS",
    body: str = "",
    task_type: str = "TODO",
) -> dict[str, Any]:
    return {
        "task_type": task_type,
        "subject": subject,
        "body": body,
        "associations": [
            {
                "target": {
                    "associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": 204,
                },
                "value": {"type": "ENROLLED_OBJECT"},
            }
        ],
        "use_explicit_associations": "true",
        "priority": priority,
        "due_time": {
            "delta": due_delta,
            "timeUnit": due_time_unit,
            "timeOfDay": {"hour": 8, "minute": 0},
            "daysOfWeek": [],
        },
    }


def _set_property_fields(property_name: str, static_value: str) -> dict[str, Any]:
    return {
        "property_name": property_name,
        "value": {"staticValue": static_value, "type": "STATIC_VALUE"},
    }


def _send_internal_email_fields(
    subject: str,
    body: str,
    owner_properties: list[str] | None = None,
    user_ids: list[str] | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "subject": subject,
        "body": f"<p>{body}</p>" if not body.startswith("<") else body,
    }
    if user_ids:
        fields["user_ids"] = user_ids
    else:
        fields["user_ids"] = []
    if owner_properties:
        fields["owner_properties"] = owner_properties
    return fields


def _send_marketing_email_fields(content_id: str) -> dict[str, Any]:
    return {"content_id": content_id}


def _add_to_list_fields(list_id: str, target_object: str = "{{ enrolled_object }}") -> dict[str, Any]:
    return {"targetObject": target_object, "listId": list_id}


def _rotate_leads_fields(
    team_id: str,
    target_property: str = "hubspot_owner_id",
    overwrite_current_owner: bool = False,
) -> dict[str, Any]:
    return {
        "team_ids": [team_id],
        "target_property": target_property,
        "overwrite_current_owner": "true" if overwrite_current_owner else "false",
    }


ACTION_TYPE_REGISTRY: dict[str, dict[str, Any]] = {
    "Delay": {
        "actionTypeId": "0-1",
        "actionTypeVersion": 0,
        "type": "SINGLE_CONNECTION",
        "build_fields": _delay_fields,
    },
    "Delay until date": {
        "actionTypeId": "0-35",
        "actionTypeVersion": 0,
        "type": "SINGLE_CONNECTION",
        "build_fields": _delay_until_fields,
    },
    "Create task": {
        "actionTypeId": "0-3",
        "actionTypeVersion": 0,
        "type": "SINGLE_CONNECTION",
        "build_fields": _create_task_fields,
    },
    "Set property value": {
        "actionTypeId": "0-5",
        "actionTypeVersion": 0,
        "type": "SINGLE_CONNECTION",
        "build_fields": _set_property_fields,
    },
    "Send marketing email": {
        "actionTypeId": "0-4",
        "actionTypeVersion": 0,
        "type": "SINGLE_CONNECTION",
        "build_fields": _send_marketing_email_fields,
    },
    "Send internal email notification": {
        "actionTypeId": "0-8",
        "actionTypeVersion": 0,
        "type": "SINGLE_CONNECTION",
        "build_fields": _send_internal_email_fields,
    },
    "Add to static list": {
        "actionTypeId": "0-63809083",
        "actionTypeVersion": 5,
        "type": "SINGLE_CONNECTION",
        "build_fields": _add_to_list_fields,
    },
    "Rotate leads": {
        "actionTypeId": "0-11",
        "actionTypeVersion": 0,
        "type": "SINGLE_CONNECTION",
        "build_fields": _rotate_leads_fields,
    },
}

# Reverse of ACTION_TYPE_REGISTRY: V4 actionTypeId -> UI action label.
# Every shipped actionTypeId is unique, so this is a clean one-to-one inverse.
# An actionTypeId absent here is unknown to the converter -> extractor keeps it
# raw and records it in the learning log.
INVERSE_ACTION_TYPES: dict[str, str] = {
    v["actionTypeId"]: name for name, v in ACTION_TYPE_REGISTRY.items()
}

BRANCH_TYPES = {
    "If/then branch": "LIST_BRANCH",
}

# UI operator label -> V4 API operator. Shared between the forward converter
# (``_build_branch_node``) and the inverse extractor so they stay in lockstep.
OPERATOR_MAP: dict[str, str] = {
    "is equal to any of": "IS_ANY_OF",
    "is not equal to any of": "IS_NONE_OF",
    "is known": "IS_KNOWN",
    "is unknown": "IS_UNKNOWN",
}
INVERSE_OPERATOR_MAP: dict[str, str] = {v: k for k, v in OPERATOR_MAP.items()}

# Mapping from blueprint enrollment trigger text to HubSpot eventTypeId.
# These are Unified Events IDs from the V4 Flows API.
EVENT_TYPE_MAP: dict[str, str] = {
    "Contact is created": "4-1463224",
    "Offer record is created": "4-1463224",
    "Showing record is created": "4-1463224",
}

# Many-to-one: several UI triggers map to the same eventTypeId. The inverse
# picks a canonical trigger and attaches an ambiguity flag rather than guessing.
INVERSE_EVENT_TYPE_MAP: dict[str, str] = {
    "4-1463224": "Contact is created",
}
AMBIGUOUS_EVENT_TYPE_IDS: set[str] = {
    v for v in EVENT_TYPE_MAP.values()
    if sum(1 for _v in EVENT_TYPE_MAP.values() if _v == v) > 1
}

OBJECT_TYPE_MAP: dict[str, str] = {
    "Contact-based": "0-1",
    "Company-based": "0-2",
    "Deal-based": "0-3",
    "Ticket-based": "0-5",
    "Listing-based": "0-420",
}
# Inverse of OBJECT_TYPE_MAP for the extractor. Custom-object IDs (``2-*``)
# are intentionally NOT in this map — the extractor flags those as
# portal-specific rather than trusting ``CUSTOM_OBJECT_MAP``, which holds IDs
# valid only in portal 148408595.
INVERSE_OBJECT_TYPE_IDS: dict[str, str] = {v: k for k, v in OBJECT_TYPE_MAP.items()}

CUSTOM_OBJECT_MAP: dict[str, str] = {
    "Showings": "2-202484491",
    "Offers": "2-202484492",
    "Open Houses": "2-202481647",
    "Commissions": "2-202481648",
}
# Inverse of CUSTOM_OBJECT_MAP for the extractor. These IDs are valid only in
# portal 148408595 (the reverse-engineering source), so the extractor still
# FLAGS every ``2-*`` objectTypeId as portal-specific even when it can invert
# the name — the flag is the honest warning, the inversion keeps round-trip
# working for the shipped blueprints. A ``2-*`` ID not in this map is an
# unknown custom object and is kept verbatim in the object_type string.
INVERSE_CUSTOM_OBJECT_MAP: dict[str, str] = {v: k for k, v in CUSTOM_OBJECT_MAP.items()}


def resolve_object_type_id(object_type: str) -> str:
    if object_type in OBJECT_TYPE_MAP:
        return OBJECT_TYPE_MAP[object_type]
    for key, oid in CUSTOM_OBJECT_MAP.items():
        if key in object_type:
            return oid
    return "0-1"


def resolve_flow_type(object_type_id: str) -> str:
    if object_type_id == "0-1":
        return "CONTACT_FLOW"
    return "PLATFORM_FLOW"
