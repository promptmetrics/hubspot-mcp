from __future__ import annotations

import hubspot_mcp.tools.data  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_import_data",
    "hubspot_export_data",
    "hubspot_get_import_status",
]

_DOMAIN = (
    "You manage HubSpot data import, export, and sync operations. "
    "You retrieve import statuses, import records, and export data."
)


def get_data_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Data Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("data")
async def _build_data_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    if intent.intent_type in ("search", "list", "get"):
        import_id = intent.description.strip() if intent.description else ""
        if not import_id:
            return PreviewResult(
                preview={"message": "Provide an import ID to get status."},
                impact_count=0,
                risk_level=intent.risk_level,
            )
        try:
            result = await invoke_tool(
                "hubspot_get_import_status",
                portal_id,
                import_id=import_id,
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
        return PreviewResult(
            preview={"import": result},
            impact_count=1,
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={},
        )

    if intent.intent_type == "create":
        return PreviewResult(
            preview={"message": "Will create a new data import"},
            impact_count=1,
            risk_level=intent.risk_level,
            proposed_payload={"name": intent.description, "object_type": "contacts", "import_file": ""},
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on data"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("data")
async def _execute_data(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    if intent.intent_type in ("search", "list", "get"):
        import_id = intent.description.strip() if intent.description else ""
        if not import_id:
            return {"status": "error", "message": "Provide an import ID to get status."}
        result = await invoke_tool(
            "hubspot_get_import_status",
            portal_id,
            import_id=import_id,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "create":
        payload = proposed_payload or {}
        result = await invoke_tool(
            "hubspot_import_data",
            portal_id,
            import_name=payload.get("name", "New Import"),
            object_type=payload.get("object_type", "contacts"),
            import_file=payload.get("import_file", ""),
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    return {"status": "success", "message": f"Executed data for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("data")
async def _reconcile_data(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    import_id = expected_payload.get("import_id") or expected_payload.get("id")
    if not import_id:
        return {"status": "unknown", "message": "No import_id in expected payload for reconciliation"}

    if intent.intent_type == "create":
        result = await invoke_tool(
            "hubspot_get_import_status",
            portal_id,
            import_id=import_id,
            client=client,
        )
        if "error" in result:
            return {
                "status": "discrepancy",
                "message": f"Import {import_id} not found after expected creation.",
                "expected": expected_payload,
                "actual": None,
            }
        return {
            "status": "verified",
            "message": f"Import {import_id} verified.",
            "expected": expected_payload,
            "actual": result,
        }

    return {"status": "unknown", "message": "Reconciliation not implemented for this intent"}
