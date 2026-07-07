from __future__ import annotations

from typing import Any

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(name="hubspot_get_analytics_report", description="Fetch raw report data from HubSpot analytics.")
async def hubspot_get_analytics_report(
    report_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/analytics/v2/reports/{report_id}",
            portal_id=portal_id,
            expected_scopes=["analytics.behavioral_events.send"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_analytics_report"}


@tool(name="hubspot_calculate_metrics", description="Calculate conversion rate, average deal size, and win rate from deal data.")
async def hubspot_calculate_metrics(
    data: list[dict[str, Any]],
    client: HubSpotClient | None = None,
    portal_id: str = "",
) -> dict[str, Any]:
    total = len(data)
    if total == 0:
        return {"conversion_rate": 0.0, "average_deal_size": 0.0, "win_rate": 0.0}

    closed_won = [d for d in data if d.get("dealstage") == "closedwon"]
    closed = [d for d in data if d.get("dealstage") in ("closedwon", "closedlost")]
    amounts = [float(d.get("amount", 0) or 0) for d in data]

    return {
        "conversion_rate": round(len(closed_won) / total * 100, 2) if total else 0.0,
        "average_deal_size": round(sum(amounts) / total, 2) if total else 0.0,
        "win_rate": round(len(closed_won) / len(closed) * 100, 2) if closed else 0.0,
    }


@tool(name="hubspot_pipeline_velocity", description="Calculate average days between stage transitions from deal history.")
async def hubspot_pipeline_velocity(
    deals: list[dict[str, Any]],
    client: HubSpotClient | None = None,
    portal_id: str = "",
) -> dict[str, Any]:
    from datetime import datetime

    stage_durations: dict[str, list[int]] = {}

    for deal in deals:
        history = deal.get("stage_history", [])
        for i in range(1, len(history)):
            prev = history[i - 1]
            curr = history[i]
            try:
                enter = datetime.fromisoformat(prev.get("entered_at", "").replace("Z", "+00:00"))
                exit_ = datetime.fromisoformat(curr.get("entered_at", "").replace("Z", "+00:00"))
                days = (exit_ - enter).days
                stage = prev.get("stage_id", "unknown")
                stage_durations.setdefault(stage, []).append(max(days, 0))
            except (ValueError, TypeError):
                continue

    velocity = {}
    for stage, durations in stage_durations.items():
        velocity[stage] = round(sum(durations) / len(durations), 2) if durations else 0.0

    return {"velocity_by_stage": velocity, "total_deals_analyzed": len(deals)}
