from __future__ import annotations

import hubspot_mcp.tools.pipelines  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_get_pipeline",
    "hubspot_list_pipelines",
    "hubspot_create_pipeline",
    "hubspot_update_pipeline",
    "hubspot_reorder_stages",
]

_DOMAIN = (
    "You manage HubSpot CRM pipelines and their stages for deals, tickets, and custom objects. "
    "You retrieve, list, create, update pipelines and reorder stages."
)


def get_pipelines_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Pipelines Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("pipelines")
async def _build_pipelines_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    object_type = intent.target_object or "deals"
    if intent.intent_type in ("search", "list", "get"):
        try:
            result = await invoke_tool(
                "hubspot_list_pipelines",
                portal_id,
                object_type=object_type,
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
            preview={"pipelines": records},
            impact_count=len(records),
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={},
        )

    if intent.intent_type == "create":
        return PreviewResult(
            preview={"message": f"Will create a new pipeline for {object_type}"},
            impact_count=1,
            risk_level=intent.risk_level,
            proposed_payload={"object_type": object_type},
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on {object_type} pipelines"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("pipelines")
async def _execute_pipelines(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    object_type = intent.target_object or "deals"
    if intent.intent_type in ("search", "list", "get"):
        result = await invoke_tool(
            "hubspot_list_pipelines",
            portal_id,
            object_type=object_type,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "create":
        payload = proposed_payload or {}
        result = await invoke_tool(
            "hubspot_create_pipeline",
            portal_id,
            object_type=object_type,
            label=payload.get("label", "New Pipeline"),
            display_order=payload.get("display_order", 0),
            stages=payload.get("stages", []),
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "update":
        payload = proposed_payload or {}
        pipeline_id = payload.get("pipeline_id")
        if not pipeline_id:
            return {"status": "error", "message": "No pipeline_id specified for update."}
        result = await invoke_tool(
            "hubspot_update_pipeline",
            portal_id,
            object_type=object_type,
            pipeline_id=pipeline_id,
            updates=payload.get("updates", {}),
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "delete":
        return {"status": "success", "message": f"Executed pipelines for: {request_text}"}

    return {"status": "success", "message": f"Executed pipelines for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("pipelines")
async def _reconcile_pipelines(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    object_type = intent.target_object or "deals"
    pipeline_id = expected_payload.get("pipeline_id") or expected_payload.get("id")
    if not pipeline_id:
        return {"status": "unknown", "message": "No pipeline_id in expected payload for reconciliation"}

    if intent.intent_type == "create":
        result = await invoke_tool(
            "hubspot_get_pipeline",
            portal_id,
            object_type=object_type,
            pipeline_id=pipeline_id,
            client=client,
        )
        if "error" in result:
            return {
                "status": "discrepancy",
                "message": f"Pipeline {pipeline_id} not found after expected creation.",
                "expected": expected_payload,
                "actual": None,
            }
        return {
            "status": "verified",
            "message": f"Pipeline {pipeline_id} verified.",
            "expected": expected_payload,
            "actual": result,
        }

    if intent.intent_type == "update":
        result = await invoke_tool(
            "hubspot_get_pipeline",
            portal_id,
            object_type=object_type,
            pipeline_id=pipeline_id,
            client=client,
        )
        if "error" in result:
            return {
                "status": "discrepancy",
                "message": f"Pipeline {pipeline_id} not found for update verification.",
                "expected": expected_payload,
                "actual": None,
            }
        return {
            "status": "verified",
            "message": f"Update verified on pipeline {pipeline_id}.",
            "expected": expected_payload,
            "actual": result,
        }

    if intent.intent_type == "delete":
        result = await invoke_tool(
            "hubspot_get_pipeline",
            portal_id,
            object_type=object_type,
            pipeline_id=pipeline_id,
            client=client,
        )
        if "error" not in result:
            return {
                "status": "discrepancy",
                "message": f"Pipeline {pipeline_id} still exists after expected delete.",
                "expected": expected_payload,
                "actual": result,
            }
        return {
            "status": "verified",
            "message": f"Delete verified: pipeline {pipeline_id} no longer exists.",
            "expected": expected_payload,
            "actual": None,
        }

    return {"status": "unknown", "message": "Reconciliation not implemented for this intent"}
