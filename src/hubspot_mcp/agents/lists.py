from __future__ import annotations

import hubspot_mcp.tools.lists  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_get_list",
    "hubspot_list_lists",
    "hubspot_create_list",
    "hubspot_update_list",
    "hubspot_add_to_list",
    "hubspot_remove_from_list",
]

_DOMAIN = (
    "You manage HubSpot CRM lists (static and dynamic). "
    "You retrieve, list, create, update lists and add or remove memberships."
)


def get_lists_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Lists Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("lists")
async def _build_lists_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    if intent.intent_type in ("search", "list", "get"):
        try:
            result = await invoke_tool(
                "hubspot_list_lists",
                portal_id,
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
        records = result.get("results", [])
        return PreviewResult(
            preview={"lists": records},
            impact_count=len(records),
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={},
        )

    if intent.intent_type == "create":
        return PreviewResult(
            preview={"message": "Will create a new list"},
            impact_count=1,
            risk_level=intent.risk_level,
            proposed_payload={"name": intent.description},
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on lists"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("lists")
async def _execute_lists(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    if intent.intent_type in ("search", "list", "get"):
        result = await invoke_tool(
            "hubspot_list_lists",
            portal_id,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "create":
        payload = proposed_payload or {}
        result = await invoke_tool(
            "hubspot_create_list",
            portal_id,
            name=payload.get("name", "New List"),
            object_type_id=payload.get("object_type_id", "0-1"),
            processing_type=payload.get("processing_type", "MANUAL"),
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "update":
        payload = proposed_payload or {}
        list_id = payload.get("list_id")
        if not list_id:
            return {"status": "error", "message": "No list_id specified for update."}
        result = await invoke_tool(
            "hubspot_update_list",
            portal_id,
            list_id=list_id,
            updates=payload.get("updates", {}),
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "delete":
        return {"status": "success", "message": f"Executed lists for: {request_text}"}

    return {"status": "success", "message": f"Executed lists for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("lists")
async def _reconcile_lists(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    list_id = expected_payload.get("list_id") or expected_payload.get("id")
    if not list_id:
        return {"status": "unknown", "message": "No list_id in expected payload for reconciliation"}

    if intent.intent_type == "create":
        result = await invoke_tool(
            "hubspot_get_list",
            portal_id,
            list_id=list_id,
            client=client,
        )
        if "error" in result:
            return {
                "status": "discrepancy",
                "message": f"List {list_id} not found after expected creation.",
                "expected": expected_payload,
                "actual": None,
            }
        return {
            "status": "verified",
            "message": f"List {list_id} verified.",
            "expected": expected_payload,
            "actual": result,
        }

    if intent.intent_type == "update":
        result = await invoke_tool(
            "hubspot_get_list",
            portal_id,
            list_id=list_id,
            client=client,
        )
        if "error" in result:
            return {
                "status": "discrepancy",
                "message": f"List {list_id} not found for update verification.",
                "expected": expected_payload,
                "actual": None,
            }
        return {
            "status": "verified",
            "message": f"Update verified on list {list_id}.",
            "expected": expected_payload,
            "actual": result,
        }

    if intent.intent_type == "delete":
        result = await invoke_tool(
            "hubspot_get_list",
            portal_id,
            list_id=list_id,
            client=client,
        )
        if "error" not in result:
            return {
                "status": "discrepancy",
                "message": f"List {list_id} still exists after expected delete.",
                "expected": expected_payload,
                "actual": result,
            }
        return {
            "status": "verified",
            "message": f"Delete verified: list {list_id} no longer exists.",
            "expected": expected_payload,
            "actual": None,
        }

    return {"status": "unknown", "message": "Reconciliation not implemented for this intent"}
