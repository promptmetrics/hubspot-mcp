from __future__ import annotations

from typing import Any
from urllib.parse import quote

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(name="hubspot_list_forms", description="List all HubSpot marketing forms.")
async def hubspot_list_forms(
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            "/marketing/v3/forms",
            portal_id=portal_id,
            expected_scopes=["forms"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_list_forms"}


@tool(name="hubspot_get_form", description="Retrieve a HubSpot marketing form by ID.")
async def hubspot_get_form(
    form_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/marketing/v3/forms/{quote(form_id, safe='')}",
            portal_id=portal_id,
            expected_scopes=["forms"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_form"}


@tool(name="hubspot_create_form", description="Create a new HubSpot marketing form.")
async def hubspot_create_form(
    name: str,
    form_type: str,
    fields: list[dict],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            "/marketing/v3/forms",
            portal_id=portal_id,
            body={"name": name, "formType": form_type, "fields": fields},
            expected_scopes=["forms"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_form"}
