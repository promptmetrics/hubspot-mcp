from __future__ import annotations

import hubspot_mcp.tools.service  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_get_knowledge_base_article",
    "hubspot_list_kb_articles",
    "hubspot_get_ticket_pipeline",
    "hubspot_create_ticket_pipeline",
    "hubspot_list_service_automation",
    "hubspot_get_feedback_survey",
]

_DOMAIN = (
    "You manage HubSpot Service Hub resources. "
    "You retrieve knowledge base articles, ticket pipelines, service automation rules, and customer feedback surveys. "
    "You create ticket pipelines with proper stage definitions."
)


def get_service_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Service Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("service")
async def _build_service_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    if intent.intent_type in ("search", "list", "get"):
        try:
            result = await invoke_tool(
                "hubspot_list_kb_articles",
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
            preview={"articles": records},
            impact_count=len(records),
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={},
        )

    if intent.intent_type == "create":
        return PreviewResult(
            preview={"message": "Will create a new service resource"},
            impact_count=1,
            risk_level=intent.risk_level,
            proposed_payload={},
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on service"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("service")
async def _execute_service(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    payload = proposed_payload or {}

    if intent.intent_type in ("search", "list", "get"):
        result = await invoke_tool(
            "hubspot_list_kb_articles",
            portal_id,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "create":
        resource_type = payload.get("resource_type", "ticket_pipeline")
        if resource_type == "ticket_pipeline":
            result = await invoke_tool(
                "hubspot_create_ticket_pipeline",
                portal_id,
                label=payload.get("label", "New Ticket Pipeline"),
                display_order=payload.get("display_order", 0),
                stages=payload.get("stages", []),
                client=client,
            )
            return {"status": "success", "data": {"result": result}}
        return {"status": "success", "message": f"Executed service for: {request_text}"}

    return {"status": "success", "message": f"Executed service for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("service")
async def _reconcile_service(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    pipeline_id = expected_payload.get("pipeline_id") or expected_payload.get("id")
    if pipeline_id:
        if intent.intent_type == "create":
            result = await invoke_tool(
                "hubspot_get_ticket_pipeline",
                portal_id,
                pipeline_id=pipeline_id,
                client=client,
            )
            if "error" in result:
                return {
                    "status": "discrepancy",
                    "message": f"Ticket pipeline {pipeline_id} not found after expected creation.",
                    "expected": expected_payload,
                    "actual": None,
                }
            return {
                "status": "verified",
                "message": f"Ticket pipeline {pipeline_id} verified.",
                "expected": expected_payload,
                "actual": result,
            }
        if intent.intent_type == "delete":
            result = await invoke_tool(
                "hubspot_get_ticket_pipeline",
                portal_id,
                pipeline_id=pipeline_id,
                client=client,
            )
            if "error" not in result:
                return {
                    "status": "discrepancy",
                    "message": f"Ticket pipeline {pipeline_id} still exists after expected delete.",
                    "expected": expected_payload,
                    "actual": result,
                }
            return {
                "status": "verified",
                "message": f"Delete verified: ticket pipeline {pipeline_id} no longer exists.",
                "expected": expected_payload,
                "actual": None,
            }

    return {"status": "unknown", "message": "Reconciliation not implemented for service"}
