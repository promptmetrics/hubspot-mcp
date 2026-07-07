from __future__ import annotations

from typing import Any
from urllib.parse import quote

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(
    name="hubspot_list_payments",
    description="List all HubSpot payments.",
)
async def hubspot_list_payments(
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            "/crm/v3/objects/payments",
            portal_id=portal_id,
            expected_scopes=["crm.objects.payments.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_list_payments"}


@tool(
    name="hubspot_get_payment",
    description="Retrieve a HubSpot payment by ID.",
)
async def hubspot_get_payment(
    payment_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/crm/v3/objects/payments/{quote(payment_id, safe='')}",
            portal_id=portal_id,
            expected_scopes=["crm.objects.payments.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_payment"}


@tool(
    name="hubspot_create_refund",
    description="Create a refund for a HubSpot payment.",
)
async def hubspot_create_refund(
    payment_id: str,
    amount: float,
    reason: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            "/crm/v3/objects/refunds",
            portal_id=portal_id,
            body={
                "paymentId": payment_id,
                "amount": amount,
                "reason": reason,
            },
            expected_scopes=["crm.objects.payments.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_refund"}
