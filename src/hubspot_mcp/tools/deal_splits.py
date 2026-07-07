from __future__ import annotations

from typing import Any

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(
    name="hubspot_batch_read_deal_splits",
    description="Read deal revenue splits for multiple deals in a batch request.",
)
async def hubspot_batch_read_deal_splits(
    deal_ids: list[str],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            "/crm/v3/objects/deals/splits/batch/read",
            portal_id=portal_id,
            body={"inputs": [{"id": deal_id} for deal_id in deal_ids]},
            expected_scopes=["crm.objects.deals.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_batch_read_deal_splits"}


@tool(
    name="hubspot_batch_upsert_deal_splits",
    description="Upsert deal revenue splits for multiple deals in a batch request. Each deal's splits must sum to 1.0 (100%).",
)
async def hubspot_batch_upsert_deal_splits(
    inputs: list[dict],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    for item in inputs:
        deal_id = item.get("deal_id", "unknown")
        splits = item.get("splits", [])
        total = sum(split.get("percentage", 0.0) for split in splits)
        if round(total, 6) != 1.0:
            return {
                "error": f"Splits for deal {deal_id} sum to {total}, expected 1.0",
                "tool": "hubspot_batch_upsert_deal_splits",
            }

    try:
        resp = await client.post(
            "/crm/v3/objects/deals/splits/batch/upsert",
            portal_id=portal_id,
            body={
                "inputs": [
                    {"id": item["deal_id"], "splits": item["splits"]}
                    for item in inputs
                ]
            },
            expected_scopes=["crm.objects.deals.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_batch_upsert_deal_splits"}
