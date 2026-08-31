from __future__ import annotations

import hubspot_mcp.tools.engagements  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_get_engagement",
    "hubspot_search_engagements",
    "hubspot_create_note",
    "hubspot_create_task",
    "hubspot_create_email",
    "hubspot_create_meeting",
    "hubspot_create_call",
]

_DOMAIN = (
    "You manage HubSpot engagements (notes, tasks, emails, meetings, calls). "
    "You retrieve, search, and create engagement records."
)


def get_engagements_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Engagements Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("engagements")
async def _build_engagements_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    if intent.intent_type in ("search", "list", "get"):
        try:
            result = await invoke_tool(
                "hubspot_search_engagements",
                portal_id,
                query={},
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
            preview={"engagements": records},
            impact_count=len(records),
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={},
        )

    if intent.intent_type == "create":
        return PreviewResult(
            preview={"message": "Will create a new engagement"},
            impact_count=1,
            risk_level=intent.risk_level,
            proposed_payload={"engagement_type": "note"},
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on engagements"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("engagements")
async def _execute_engagements(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    payload = proposed_payload or {}
    engagement_type = payload.get("engagement_type", "note")

    if intent.intent_type in ("search", "list", "get"):
        result = await invoke_tool(
            "hubspot_search_engagements",
            portal_id,
            query=payload.get("query", {}),
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "create":
        if engagement_type == "note":
            result = await invoke_tool(
                "hubspot_create_note",
                portal_id,
                body=payload.get("body", ""),
                associations=payload.get("associations"),
                client=client,
            )
        elif engagement_type == "task":
            result = await invoke_tool(
                "hubspot_create_task",
                portal_id,
                subject=payload.get("subject", ""),
                status=payload.get("status", "NOT_STARTED"),
                timestamp=payload.get("timestamp", ""),
                associations=payload.get("associations"),
                client=client,
            )
        elif engagement_type == "email":
            result = await invoke_tool(
                "hubspot_create_email",
                portal_id,
                subject=payload.get("subject", ""),
                body=payload.get("body", ""),
                associations=payload.get("associations"),
                client=client,
            )
        elif engagement_type == "meeting":
            result = await invoke_tool(
                "hubspot_create_meeting",
                portal_id,
                title=payload.get("title", ""),
                start_time=payload.get("start_time", ""),
                associations=payload.get("associations"),
                client=client,
            )
        elif engagement_type == "call":
            result = await invoke_tool(
                "hubspot_create_call",
                portal_id,
                title=payload.get("title", ""),
                duration_ms=payload.get("duration_ms", 0),
                associations=payload.get("associations"),
                client=client,
            )
        else:
            return {"status": "error", "message": f"Unknown engagement type: {engagement_type}"}
        return {"status": "success", "data": {"result": result}}

    return {"status": "success", "message": f"Executed engagements for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("engagements")
async def _reconcile_engagements(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    engagement_id = expected_payload.get("engagement_id") or expected_payload.get("id")
    if not engagement_id:
        return {"status": "unknown", "message": "No engagement_id in expected payload for reconciliation"}

    if intent.intent_type == "create":
        result = await invoke_tool(
            "hubspot_get_engagement",
            portal_id,
            engagement_id=engagement_id,
            client=client,
        )
        if "error" in result:
            return {
                "status": "discrepancy",
                "message": f"Engagement {engagement_id} not found after expected creation.",
                "expected": expected_payload,
                "actual": None,
            }
        return {
            "status": "verified",
            "message": f"Engagement {engagement_id} verified.",
            "expected": expected_payload,
            "actual": result,
        }

    return {"status": "unknown", "message": "Reconciliation not implemented for this intent"}
