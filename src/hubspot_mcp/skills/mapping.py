"""Runtime header for a served skill: connector tool names → this server's tools.

HubSpot's skills were written against HubSpot's official MCP connector and name
its tools. This server has different tools, so every served skill carries a
header that maps the names and explains the write gate. The skill text itself
is never changed (see NOTICE).
"""
from __future__ import annotations

# Connector tool → the tools here that do that job. Ordered so the common case
# comes first. Verified against the registered tool list in tests.
CONNECTOR_TO_SERVER: dict[str, tuple[str, ...]] = {
    "search_crm_objects": ("hubspot_search_objects",),
    "get_crm_objects": ("hubspot_get_object", "hubspot_list_associated_records"),
    "manage_crm_objects": (
        "hubspot_create_object",
        "hubspot_update_object",
        "hubspot_delete_object",
        "hubspot_associate_records",
        "hubspot_create_note",
        "hubspot_create_task",
        "hubspot_create_call",
        "hubspot_create_email",
        "hubspot_create_meeting",
    ),
    "get_organization_details": ("hubspot_list_pipelines", "hubspot_get_pipeline", "hubspot_list_users"),
    "get_user_details": ("hubspot_get_user", "hubspot_list_users", "hubspot_status"),
    "get_properties": ("hubspot_list_properties", "hubspot_get_property"),
    "search_properties": ("hubspot_list_properties", "hubspot_get_property"),
    "discover_hubspot_schema": ("hubspot_list_properties", "hubspot_get_association_schema"),
    "search_owners": ("hubspot_list_users", "hubspot_get_user"),
    "tool_guidance": ("hubspot_docs_search",),
}

_NOTES = {
    "manage_crm_objects": "create, update, delete or associate a record, or create a note, task, call, email or meeting",
    "get_crm_objects": "one record by id, or the records associated with it",
    "get_organization_details": "pipelines and stages, and the users on the account",
    "get_user_details": "the current user comes from hubspot_status plus hubspot_list_users",
}


def server_tools_for(connector_names: tuple[str, ...] | list[str]) -> list[str]:
    """Flat, de-duplicated list of this server's tools behind the given connector names."""
    out: list[str] = []
    for n in connector_names:
        for t in CONNECTOR_TO_SERVER.get(n, ()):
            if t not in out:
                out.append(t)
    return out


def skill_header(name: str, tools: tuple[str, ...], *, routed: bool, helper: bool) -> str:
    """The text prepended to a served skill or helper."""
    how = (
        "Run every HubSpot tool through call_hubspot(tool=<name>, args={...}); the tools are not exposed directly."
        if routed
        else "The tools below are exposed directly on this server; call them by name."
    )
    lines = [
        f"# Runtime for {'helper' if helper else 'skill'} {name}",
        "This text is HubSpot's, served unmodified by hubspot-mcp. It names tools from HubSpot's official "
        "connector. On this server use these instead:",
    ]
    for n in tools or tuple(CONNECTOR_TO_SERVER):
        targets = CONNECTOR_TO_SERVER.get(n)
        if not targets:
            continue
        note = f" ({_NOTES[n]})" if n in _NOTES else ""
        lines.append(f"- {n} -> {', '.join(targets)}{note}")
    lines += [
        how,
        "A write may return status='preview' with an action_id and an approval tier: show the preview, and "
        + (
            "once the user confirms call call_hubspot(tool='hubspot_approve_write', args={'action_id': ...})."
            if routed
            else "once the user confirms call hubspot_approve_write(action_id=...)."
        )
        + " AUTO-tier writes apply at once and return an undo id.",
        "Gmail, calendar and Slack connectors the skill mentions are not part of this server; use them only if "
        "the host has them connected, otherwise skip those steps.",
        "Where the skill delegates to the account-research or brief-fetcher agent, load that helper with "
        + ("load_skill" if routed else "hubspot_load_skill")
        + " and run its fetches yourself.",
        "",
    ]
    return "\n".join(lines) + "\n"
