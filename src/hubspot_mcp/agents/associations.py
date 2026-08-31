from __future__ import annotations

import hubspot_mcp.tools.associations  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_get_association_schema",
    "hubspot_create_association_schema",
    "hubspot_associate_records",
    "hubspot_disassociate_records",
    "hubspot_list_associated_records",
]

_DOMAIN = (
    "You manage HubSpot associations between objects. "
    "You retrieve and create association schemas, link or unlink records, "
    "and list associated records for a given source record."
)

# Simple object-type extraction from natural language
_OBJ_KEYWORDS: dict[str, str] = {
    "contact": "contacts",
    "contacts": "contacts",
    "company": "companies",
    "companies": "companies",
    "deal": "deals",
    "deals": "deals",
    "ticket": "tickets",
    "tickets": "tickets",
}

_ASSOC_TRIGGERS = ["associated with", "linked to", "related to", "at", "for", "of"]


def _extract_association_pair(request_text: str) -> tuple[str | None, str | None]:
    """Extract (source_type, target_type) from a cross-object request.

    Heuristic: look for two object types joined by an association trigger.
    E.g. 'contacts associated with companies' -> ('companies', 'contacts').
    Returns (None, None) if extraction fails.
    """
    text = request_text.lower()
    found: list[str] = []
    for keyword, obj_type in _OBJ_KEYWORDS.items():
        if keyword in text and obj_type not in found:
            found.append(obj_type)

    if len(found) >= 2:
        # Default: first mentioned = target (what user wants), second = source
        # But if trigger words appear, try to infer direction
        for trigger in _ASSOC_TRIGGERS:
            if trigger in text:
                parts = text.split(trigger, 1)
                before = parts[0]
                after = parts[1] if len(parts) > 1 else ""
                before_objs = [o for k, o in _OBJ_KEYWORDS.items() if k in before]
                after_objs = [o for k, o in _OBJ_KEYWORDS.items() if k in after]
                if before_objs and after_objs:
                    # after = source, before = target
                    return (after_objs[0], before_objs[0])
        return (found[1], found[0])
    return (None, None)


def get_associations_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Associations Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("associations")
async def _build_associations_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    source_type, target_type = _extract_association_pair(intent.description)

    if source_type and target_type:
        try:
            schema = await invoke_tool(
                "hubspot_get_association_schema",
                portal_id,
                from_object_type=source_type,
                to_object_type=target_type,
                client=client,
            )
        except Exception as exc:
            schema = {"error": str(exc)}

        labels = []
        if "results" in schema:
            labels = [
                r.get("label") or r.get("name") or "unnamed"
                for r in schema["results"]
            ]
        elif "error" not in schema:
            labels = [schema.get("label") or schema.get("name") or "unnamed"]

        return PreviewResult(
            preview={
                "message": (
                    f"Would traverse {source_type} → {target_type} associations. "
                    f"Available labels: {', '.join(labels) if labels else 'unknown'}."
                ),
                "source_type": source_type,
                "target_type": target_type,
                "labels": labels,
            },
            impact_count=intent.estimated_impact or 1,
            risk_level=intent.risk_level,
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on associations"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("associations")
async def _execute_associations(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    payload = proposed_payload or {}

    if intent.intent_type == "create":
        result = await invoke_tool(
            "hubspot_associate_records",
            portal_id,
            from_object_type=payload.get("from_object_type", ""),
            from_object_id=payload.get("from_object_id", ""),
            to_object_type=payload.get("to_object_type", ""),
            to_object_id=payload.get("to_object_id", ""),
            association_type_id=payload.get("association_type_id", ""),
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "delete":
        result = await invoke_tool(
            "hubspot_disassociate_records",
            portal_id,
            from_object_type=payload.get("from_object_type", ""),
            from_object_id=payload.get("from_object_id", ""),
            to_object_type=payload.get("to_object_type", ""),
            to_object_id=payload.get("to_object_id", ""),
            association_type_id=payload.get("association_type_id", ""),
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "search":
        source_type, target_type = _extract_association_pair(request_text)
        if source_type and target_type:
            # Try to fetch a sample source record, then list its associations
            try:
                search_result = await invoke_tool(
                    "hubspot_search_objects",
                    portal_id,
                    object_type=source_type,
                    query={"query": "*", "limit": 1, "properties": ["name", "firstname", "lastname"]},
                    client=client,
                )
                if "error" in search_result:
                    return {
                        "status": "error",
                        "message": f"Could not search {source_type}: {search_result['error']}",
                    }
                results = search_result.get("results", [])
                if not results:
                    return {
                        "status": "success",
                        "message": f"No {source_type} records found to sample associations.",
                        "data": {"source_type": source_type, "target_type": target_type},
                    }
                sample_id = results[0].get("id")
                assoc_result = await invoke_tool(
                    "hubspot_list_associated_records",
                    portal_id,
                    from_object_type=source_type,
                    from_object_id=sample_id,
                    to_object_type=target_type,
                    client=client,
                )
                if "error" in assoc_result:
                    return {
                        "status": "error",
                        "message": f"Could not list associations: {assoc_result['error']}",
                    }
                associated = assoc_result.get("results", [])
                return {
                    "status": "success",
                    "message": (
                        f"Found {len(associated)} {target_type} associated with sample {source_type} "
                        f"({sample_id}). Use the objects agent to fetch full properties for these IDs."
                    ),
                    "data": {
                        "source_type": source_type,
                        "target_type": target_type,
                        "sample_source_id": sample_id,
                        "associated_ids": [r.get("id") or r.get("toObjectId") for r in associated],
                        "association_details": associated,
                    },
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "message": f"Association traversal failed: {exc}",
                }

    return {"status": "success", "message": f"Executed associations for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("associations")
async def _reconcile_associations(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    return {"status": "unknown", "message": "Reconciliation not implemented for associations"}
