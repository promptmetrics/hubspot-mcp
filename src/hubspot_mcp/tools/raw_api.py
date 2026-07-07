from __future__ import annotations

from typing import Any

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(
    name="hubspot_raw_api",
    description="Direct HubSpot API call for uncovered endpoints. Power-user escape hatch.")
async def hubspot_raw_api(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    client: HubSpotClient | None = None,
    portal_id: str = "",
    expected_scopes: list[str] | None = None,
) -> dict[str, Any]:
    if client is None:
        return {"error": "HubSpotClient not provided", "tool": "hubspot_raw_api"}
    try:
        if method.upper() == "GET":
            resp = await client.get(path, portal_id=portal_id, expected_scopes=expected_scopes)
        elif method.upper() == "POST":
            resp = await client.post(path, portal_id=portal_id, body=body, expected_scopes=expected_scopes)
        elif method.upper() == "PATCH":
            resp = await client.patch(path, portal_id=portal_id, body=body, expected_scopes=expected_scopes)
        elif method.upper() == "DELETE":
            resp = await client.delete(path, portal_id=portal_id, expected_scopes=expected_scopes)
        else:
            return {"error": f"Unsupported method: {method}", "tool": "hubspot_raw_api"}
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_raw_api"}
