from __future__ import annotations

from typing import Any

import hubspot_mcp.tools.hygiene  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_find_duplicates",
    "hubspot_merge_objects",
    "hubspot_bulk_update_objects",
    "hubspot_preview_segment",
]

_DOMAIN = (
    "You manage data hygiene in HubSpot. "
    "You find duplicate records, merge objects, perform bulk updates, and preview segments before changes."
)


def get_hygiene_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Hygiene Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("hygiene")
async def _build_hygiene_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    object_type = intent.target_object or "contacts"
    search_field = "email"

    if intent.intent_type in ("search", "list", "get"):
        try:
            result = await invoke_tool(
                "hubspot_find_duplicates",
                portal_id,
                object_type=object_type,
                search_field=search_field,
                client=client,
            )
        except Exception as exc:
            return PreviewResult(
                preview={"error": str(exc)},
                impact_count=0,
                risk_level=intent.risk_level,
            )
        if "error" in result:
            return PreviewResult(
                preview={"error": result["error"]},
                impact_count=0,
                risk_level=intent.risk_level,
            )
        duplicate_groups = result.get("duplicate_groups", {})
        total = result.get("total_duplicates", 0)
        return PreviewResult(
            preview={"duplicate_groups": duplicate_groups},
            impact_count=total,
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={},
        )

    if intent.intent_type == "update":
        payload: dict[str, Any] = {}
        try:
            result = await invoke_tool(
                "hubspot_preview_segment",
                portal_id,
                object_type=object_type,
                query=payload.get("query", {}),
                client=client,
            )
        except Exception as exc:
            return PreviewResult(
                preview={"error": str(exc)},
                impact_count=0,
                risk_level=intent.risk_level,
            )
        if "error" in result:
            return PreviewResult(
                preview={"error": result["error"]},
                impact_count=0,
                risk_level=intent.risk_level,
            )
        total = result.get("total", 0)
        return PreviewResult(
            preview={"segment": result.get("results", [])},
            impact_count=total,
            risk_level=intent.risk_level,
            proposed_payload=payload,
            original_values={},
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on hygiene"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("hygiene")
async def _execute_hygiene(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    object_type = intent.target_object or "contacts"

    if intent.intent_type in ("search", "list", "get"):
        result = await invoke_tool(
            "hubspot_find_duplicates",
            portal_id,
            object_type=object_type,
            search_field="email",
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "update":
        payload = proposed_payload or {}
        records = payload.get("records", [])
        if records:
            result = await invoke_tool(
                "hubspot_bulk_update_objects",
                portal_id,
                object_type=object_type,
                records=records,
                client=client,
            )
            return {"status": "success", "data": {"result": result}}
        return {"status": "success", "message": f"Executed hygiene for: {request_text}"}

    if intent.intent_type == "merge":
        payload = proposed_payload or {}
        primary = payload.get("primary_object_id")
        secondary = payload.get("object_id_to_merge")
        if not primary or not secondary:
            return {"status": "error", "message": "Missing primary_object_id or object_id_to_merge for merge."}
        result = await invoke_tool(
            "hubspot_merge_objects",
            portal_id,
            primary_object_id=primary,
            object_id_to_merge=secondary,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    return {"status": "success", "message": f"Executed hygiene for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("hygiene")
async def _reconcile_hygiene(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    return {"status": "unknown", "message": "Reconciliation not implemented for hygiene"}
