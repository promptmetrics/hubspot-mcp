from __future__ import annotations

import hubspot_mcp.tools.commerce  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_list_payments",
    "hubspot_get_payment",
    "hubspot_create_refund",
]

_DOMAIN = (
    "You manage HubSpot commerce payments and refunds. "
    "You retrieve, list payments and create refunds."
)


def get_commerce_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Commerce Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("commerce")
async def _build_commerce_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    if intent.intent_type in ("search", "list", "get"):
        try:
            result = await invoke_tool(
                "hubspot_list_payments",
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
            preview={"payments": records},
            impact_count=len(records),
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={},
        )

    if intent.intent_type == "create":
        return PreviewResult(
            preview={"message": "Will create a refund"},
            impact_count=1,
            risk_level=intent.risk_level,
            proposed_payload={"payment_id": "", "reason": intent.description, "amount": None},
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on commerce"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("commerce")
async def _execute_commerce(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    if intent.intent_type in ("search", "list", "get"):
        result = await invoke_tool(
            "hubspot_list_payments",
            portal_id,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "create":
        payload = proposed_payload or {}
        result = await invoke_tool(
            "hubspot_create_refund",
            portal_id,
            payment_id=payload.get("payment_id", ""),
            amount=payload.get("amount"),
            reason=payload.get("reason", ""),
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    return {"status": "success", "message": f"Executed commerce for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("commerce")
async def _reconcile_commerce(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    refund_id = expected_payload.get("refund_id") or expected_payload.get("id")
    if not refund_id:
        return {"status": "unknown", "message": "No refund_id in expected payload for reconciliation"}

    if intent.intent_type == "create":
        result = await invoke_tool(
            "hubspot_get_payment",
            portal_id,
            payment_id=refund_id,
            client=client,
        )
        if "error" in result:
            return {
                "status": "discrepancy",
                "message": f"Refund {refund_id} not found after expected creation.",
                "expected": expected_payload,
                "actual": None,
            }
        return {
            "status": "verified",
            "message": f"Refund {refund_id} verified.",
            "expected": expected_payload,
            "actual": result,
        }

    return {"status": "unknown", "message": "Reconciliation not implemented for this intent"}
