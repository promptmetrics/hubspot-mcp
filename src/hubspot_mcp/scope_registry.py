from __future__ import annotations

from typing import Any

# Object-type-agnostic tool families and the scope action they require.
_OBJECT_ACTIONS = {
    "get": "read",
    "search": "read",
    "list": "read",
    "create": "write",
    "update": "write",
    "delete": "write+delete",
    "batch_upsert": "write",
    "merge": "write+delete",
    "bulk_update": "write",
    "deactivate": "write",
    "toggle": "write",
    "enroll": "write",
    "reorder": "write",
}

_STANDARD_OBJECT_TYPES = ("contacts", "companies", "deals", "tickets")


def _object_scope_set(action: str, object_type: str | None) -> set[str]:
    """Return the HubSpot OAuth scope set for an object action.

    When *object_type* is unknown, require the action across all standard
    object types so the check is conservative but still useful.
    """
    actions = action.split("+")
    types = (object_type,) if object_type else _STANDARD_OBJECT_TYPES
    scopes: set[str] = set()
    for obj in types:
        for a in actions:
            scopes.add(f"crm.objects.{obj}.{a}")
    return scopes


def _schema_scope_set(action: str, object_type: str | None) -> set[str]:
    """Return the HubSpot OAuth scope set for a property-schema action."""
    actions = action.split("+")
    types = (object_type,) if object_type else _STANDARD_OBJECT_TYPES
    scopes: set[str] = set()
    for obj in types:
        for a in actions:
            scopes.add(f"crm.schemas.{obj}.{a}")
    return scopes


def _resolve_object_tool(tool_name: str, object_type: str | None) -> set[str]:
    """Infer scope from tools named hubspot_<action>_<noun>."""
    parts = tool_name.split("_")
    if len(parts) < 2:
        return set()
    action = parts[1]

    # Property tools live under the schemas scope family.
    if parts[-1] == "property" or parts[-1] == "properties":
        return _schema_scope_set(_OBJECT_ACTIONS.get(action, "write"), object_type)

    # Generic object tools.
    if parts[-1] == "object" or parts[-1] == "objects":
        return _object_scope_set(_OBJECT_ACTIONS.get(action, "write"), object_type)

    return set()


# Tool-specific scope overrides. Keys are exact tool names; values may be either
# a static set of scopes or a callable ``(target_object) -> set[str]``.
_TOOL_SCOPES: dict[str, set[str] | Any] = {
    # Objects
    "hubspot_get_object": lambda ot: _object_scope_set("read", ot),
    "hubspot_search_objects": lambda ot: _object_scope_set("read", ot),
    "hubspot_create_object": lambda ot: _object_scope_set("write", ot),
    "hubspot_update_object": lambda ot: _object_scope_set("write", ot),
    "hubspot_delete_object": lambda ot: _object_scope_set("write+delete", ot),
    "hubspot_batch_upsert_objects": lambda ot: _object_scope_set("write", ot),

    # Properties (schemas)
    "hubspot_get_property": lambda ot: _schema_scope_set("read", ot),
    "hubspot_list_properties": lambda ot: _schema_scope_set("read", ot),
    "hubspot_create_property": lambda ot: _schema_scope_set("write", ot),
    "hubspot_update_property": lambda ot: _schema_scope_set("write", ot),
    "hubspot_delete_property": lambda ot: _schema_scope_set("write+delete", ot),

    # Lists
    "hubspot_list_lists": {"crm.lists.read"},
    "hubspot_get_list": {"crm.lists.read"},
    "hubspot_create_list": {"crm.lists.write"},
    "hubspot_update_list": {"crm.lists.write"},
    "hubspot_add_to_list": {"crm.lists.write"},
    "hubspot_remove_from_list": {"crm.lists.write"},

    # Workflows — HubSpot exposes a single `automation` read/write scope
    # (Pro/Enterprise portals only).
    "hubspot_list_workflows": {"automation"},
    "hubspot_get_workflow": {"automation"},
    "hubspot_create_workflow": {"automation"},
    "hubspot_update_workflow": {"automation"},
    "hubspot_toggle_workflow": {"automation"},
    "hubspot_enroll_workflow": {"automation"},
    "hubspot_create_workflow_from_blueprint": {"automation"},

    # Pipelines — only `crm.pipelines.orders.*` exists; deal pipelines are
    # gated by crm.objects.deals.* / crm.schemas.deals.* (already required).
    "hubspot_list_pipelines": {"crm.pipelines.orders.read"},
    "hubspot_get_pipeline": {"crm.pipelines.orders.read"},
    "hubspot_create_pipeline": {"crm.pipelines.orders.write"},
    "hubspot_update_pipeline": {"crm.pipelines.orders.write"},
    "hubspot_reorder_stages": {"crm.pipelines.orders.write"},
    "hubspot_create_ticket_pipeline": {"crm.pipelines.orders.write"},
    "hubspot_get_ticket_pipeline": {"crm.pipelines.orders.read"},

    # Users
    "hubspot_list_users": {"settings.users.read"},
    "hubspot_get_user": {"settings.users.read"},
    "hubspot_create_user": {"settings.users.write"},
    "hubspot_update_user": {"settings.users.write"},
    "hubspot_deactivate_user": {"settings.users.write"},

    # Engagements — HubSpot has no single engagements scope; each engagement
    # type carries its own granular object scope. Read tools need the union.
    # NOTE: crm.objects.notes/calls/tasks/emails.* are HubSpot "hidden scopes"
    # (referenced in API 403s but not selectable in any app picker), so they are
    # deliberately NOT requested at authorize time (see setup.REQUIRED_SCOPES).
    # They remain listed here as the honest API requirement; on OAuth portals
    # the scope pre-check is skipped (scopes_granted is empty) and these tools
    # 403 at call time. crm.objects.appointments.* IS selectable and is the
    # correct scope for meetings.
    "hubspot_search_engagements": {
        "crm.objects.notes.read",
        "crm.objects.calls.read",
        "crm.objects.appointments.read",
        "crm.objects.tasks.read",
        "crm.objects.emails.read",
        "sales-email-read",
    },
    "hubspot_get_engagement": {
        "crm.objects.notes.read",
        "crm.objects.calls.read",
        "crm.objects.appointments.read",
        "crm.objects.tasks.read",
        "crm.objects.emails.read",
        "sales-email-read",
    },
    "hubspot_create_call": {"crm.objects.calls.write"},
    "hubspot_create_email": {"crm.objects.emails.write", "sales-email-read"},
    "hubspot_create_meeting": {"crm.objects.appointments.write"},
    "hubspot_create_note": {"crm.objects.notes.write"},
    "hubspot_create_task": {"crm.objects.tasks.write"},

    # Associations — tickets use the single `tickets` scope (read+write).
    "hubspot_get_association_schema": {
        "crm.objects.contacts.read",
        "crm.objects.companies.read",
        "crm.objects.deals.read",
        "tickets",
    },
    "hubspot_create_association_schema": {
        "crm.objects.contacts.write",
        "crm.objects.companies.write",
        "crm.objects.deals.write",
        "tickets",
    },
    "hubspot_associate_records": {
        "crm.objects.contacts.write",
        "crm.objects.companies.write",
        "crm.objects.deals.write",
        "tickets",
    },
    "hubspot_disassociate_records": {
        "crm.objects.contacts.write",
        "crm.objects.companies.write",
        "crm.objects.deals.write",
        "tickets",
    },
    "hubspot_list_associated_records": {
        "crm.objects.contacts.read",
        "crm.objects.companies.read",
        "crm.objects.deals.read",
        "tickets",
    },

    # Hygiene (object writes on the target object type)
    "hubspot_find_duplicates": lambda ot: _object_scope_set("read", ot),
    "hubspot_merge_objects": lambda ot: _object_scope_set("write+delete", ot),
    "hubspot_bulk_update_objects": lambda ot: _object_scope_set("write", ot),
    "hubspot_preview_segment": lambda ot: _object_scope_set("read", ot),

    # Analytics / reporting (read-only)
    "hubspot_get_analytics_report": {"crm.objects.deals.read"},
    "hubspot_calculate_metrics": {"crm.objects.deals.read"},
    "hubspot_pipeline_velocity": {"crm.objects.deals.read"},
    "hubspot_create_report": {"crm.objects.deals.read"},
    "hubspot_get_report": {"crm.objects.deals.read"},
    "hubspot_create_dashboard": {"crm.objects.deals.read"},
    "hubspot_schedule_email": {"settings.users.read"},

    # Forms / service / docs / raw-api / commerce / data — no explicit scopes in
    # the standard CRM OAuth set, or they are covered by broader portal access.
    "hubspot_create_form": set(),
    "hubspot_get_form": set(),
    "hubspot_list_forms": set(),
    "hubspot_list_kb_articles": set(),
    "hubspot_get_knowledge_base_article": set(),
    "hubspot_list_service_automation": set(),
    "hubspot_get_feedback_survey": set(),
    "hubspot_docs_search": set(),
    "hubspot_raw_api": set(),
    "hubspot_list_payments": set(),
    "hubspot_get_payment": set(),
    "hubspot_create_refund": set(),
    "hubspot_import_data": set(),
    "hubspot_export_data": set(),
    "hubspot_get_import_status": set(),
    "hubspot_batch_read_deal_splits": {"crm.objects.deals.read"},
    "hubspot_batch_upsert_deal_splits": {"crm.objects.deals.write"},
}


# Mapping from agent name to the tool names the agent may invoke.
# This mirrors each agent's `_TOOL_NAMES` list and lets us check scopes at the
# dispatch layer without importing every agent module.
_AGENT_TOOLS: dict[str, list[str]] = {
    "objects": [
        "hubspot_get_object",
        "hubspot_search_objects",
        "hubspot_create_object",
        "hubspot_update_object",
        "hubspot_delete_object",
        "hubspot_batch_upsert_objects",
    ],
    "properties": [
        "hubspot_get_property",
        "hubspot_list_properties",
        "hubspot_create_property",
        "hubspot_update_property",
        "hubspot_delete_property",
    ],
    "lists": [
        "hubspot_get_list",
        "hubspot_list_lists",
        "hubspot_create_list",
        "hubspot_update_list",
        "hubspot_add_to_list",
        "hubspot_remove_from_list",
    ],
    "workflows": [
        "hubspot_get_workflow",
        "hubspot_list_workflows",
        "hubspot_create_workflow",
        "hubspot_update_workflow",
        "hubspot_enroll_workflow",
        "hubspot_toggle_workflow",
        "hubspot_create_workflow_from_blueprint",
    ],
    "pipelines": [
        "hubspot_list_pipelines",
        "hubspot_get_pipeline",
        "hubspot_create_pipeline",
        "hubspot_update_pipeline",
        "hubspot_reorder_stages",
    ],
    "users": [
        "hubspot_get_user",
        "hubspot_list_users",
        "hubspot_create_user",
        "hubspot_update_user",
        "hubspot_deactivate_user",
    ],
    "hygiene": [
        "hubspot_find_duplicates",
        "hubspot_merge_objects",
        "hubspot_bulk_update_objects",
        "hubspot_preview_segment",
    ],
    "analytics": [
        "hubspot_get_analytics_report",
        "hubspot_calculate_metrics",
        "hubspot_pipeline_velocity",
    ],
    "associations": [
        "hubspot_get_association_schema",
        "hubspot_create_association_schema",
        "hubspot_associate_records",
        "hubspot_disassociate_records",
        "hubspot_list_associated_records",
    ],
    "engagements": [
        "hubspot_get_engagement",
        "hubspot_search_engagements",
        "hubspot_create_call",
        "hubspot_create_email",
        "hubspot_create_meeting",
        "hubspot_create_note",
        "hubspot_create_task",
    ],
    "service": [
        "hubspot_list_kb_articles",
        "hubspot_get_knowledge_base_article",
        "hubspot_get_ticket_pipeline",
        "hubspot_create_ticket_pipeline",
        "hubspot_list_service_automation",
        "hubspot_get_feedback_survey",
    ],
    "forms": [
        "hubspot_list_forms",
        "hubspot_get_form",
        "hubspot_create_form",
    ],
    "raw_api": ["hubspot_raw_api"],
    "custom_objects": ["hubspot_raw_api"],
    "commerce": [
        "hubspot_list_payments",
        "hubspot_get_payment",
        "hubspot_create_refund",
    ],
    "data": [
        "hubspot_import_data",
        "hubspot_export_data",
        "hubspot_get_import_status",
    ],
    "deal_splits": [
        "hubspot_batch_read_deal_splits",
        "hubspot_batch_upsert_deal_splits",
    ],
    "reporting": [
        "hubspot_create_report",
        "hubspot_get_report",
        "hubspot_create_dashboard",
        "hubspot_schedule_email",
    ],
}


def _scopes_for_tool(tool_name: str, target_object: str | None = None) -> set[str]:
    rule = _TOOL_SCOPES.get(tool_name)
    if rule is None:
        return _resolve_object_tool(tool_name, target_object)
    if callable(rule):
        return rule(target_object)
    return set(rule)


def get_required_scopes(tool_names: list[str], target_object: str | None = None) -> set[str]:
    """Return the union of required HubSpot OAuth scopes for a list of tools."""
    scopes: set[str] = set()
    for name in tool_names:
        scopes.update(_scopes_for_tool(name, target_object))
    return scopes


def get_required_scopes_for_agent(agent_name: str, target_object: str | None = None) -> set[str]:
    """Return required scopes for an agent based on its registered tool set."""
    tools = _AGENT_TOOLS.get(agent_name, [])
    if not tools:
        return set()
    return get_required_scopes(tools, target_object)
