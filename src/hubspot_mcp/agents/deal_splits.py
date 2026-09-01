from __future__ import annotations

import hubspot_mcp.tools.deal_splits  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_batch_read_deal_splits",
    "hubspot_batch_upsert_deal_splits",
]

_DOMAIN = (
    "You manage HubSpot deal revenue splits. "
    "You read existing splits and perform batch upserts. "
    "Each deal's splits must sum to 1.0 (100%)."
)


def get_deal_splits_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Deal Splits Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("deal_splits")
async def _build_deal_splits_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    if intent.intent_type in ("search", "list", "get"):
        deal_ids: list[str] = []
        result = await invoke_tool(
            "hubspot_batch_read_deal_splits",
            portal_id,
            deal_ids=deal_ids,
            client=client,
        )
        if "error" in result:
            return PreviewResult(
                preview={"error": result["error"]},
                impact_count=0,
                risk_level=intent.risk_level,
            )
        records = result.get("results", [])
        return PreviewResult(
            preview={"deal_splits": records},
            impact_count=len(records),
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={},
        )

    if intent.intent_type in ("create", "update"):
        return PreviewResult(
            preview={"message": "Will batch upsert deal splits"},
            impact_count=intent.estimated_impact or 1,
            risk_level=intent.risk_level,
            proposed_payload={"inputs": []},
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on deal splits"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("deal_splits")
async def _execute_deal_splits(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    if intent.intent_type in ("search", "list", "get"):
        deal_ids: list[str] = []
        result = await invoke_tool(
            "hubspot_batch_read_deal_splits",
            portal_id,
            deal_ids=deal_ids,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type in ("create", "update"):
        payload = proposed_payload or {}
        inputs = payload.get("inputs", [])
        result = await invoke_tool(
            "hubspot_batch_upsert_deal_splits",
            portal_id,
            inputs=inputs,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    return {"status": "success", "message": f"Executed deal_splits for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("deal_splits")
async def _reconcile_deal_splits(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    if intent.intent_type not in ("create", "update"):
        return {"status": "unknown", "message": "Reconciliation not implemented for this intent"}

    inputs = expected_payload.get("inputs", [])
    if not inputs:
        return {"status": "unknown", "message": "No inputs in expected payload for reconciliation"}

    deal_ids = [item["deal_id"] for item in inputs if "deal_id" in item]
    if not deal_ids:
        return {"status": "unknown", "message": "No deal_ids found in expected payload inputs"}

    result = await invoke_tool(
        "hubspot_batch_read_deal_splits",
        portal_id,
        deal_ids=deal_ids,
        client=client,
    )

    if "error" in result:
        return {
            "status": "discrepancy",
            "message": f"Failed to read deal splits for reconciliation: {result['error']}",
            "expected": expected_payload,
            "actual": result,
        }

    actual_results = {r.get("id"): r.get("splits", []) for r in result.get("results", [])}
    expected_splits = {item["deal_id"]: item.get("splits", []) for item in inputs}

    for deal_id, expected in expected_splits.items():
        actual = actual_results.get(deal_id)
        if actual is None:
            return {
                "status": "discrepancy",
                "message": f"Deal {deal_id} splits not found after expected upsert.",
                "expected": expected_payload,
                "actual": actual_results,
            }
        expected_sorted = sorted(expected, key=lambda s: (s.get("ownerId", ""), s.get("percentage", 0)))
        actual_sorted = sorted(actual, key=lambda s: (s.get("ownerId", ""), s.get("percentage", 0)))
        if expected_sorted != actual_sorted:
            return {
                "status": "discrepancy",
                "message": f"Deal {deal_id} splits do not match expected values.",
                "expected": expected_payload,
                "actual": actual_results,
            }

    return {
        "status": "verified",
        "message": f"Deal splits verified for {len(deal_ids)} deal(s).",
        "expected": expected_payload,
        "actual": actual_results,
    }
