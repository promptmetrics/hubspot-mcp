from __future__ import annotations

from typing import Any
from urllib.parse import quote

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(name="hubspot_get_list", description="Retrieve a HubSpot list by ID.")
async def hubspot_get_list(
    list_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/crm/v3/lists/{quote(list_id, safe='')}",
            portal_id=portal_id,
            expected_scopes=["crm.lists.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_list"}


@tool(name="hubspot_list_lists", description="List all HubSpot lists.")
async def hubspot_list_lists(
    object_type: str | None = None,
    client: HubSpotClient | None = None,
    portal_id: str = "",
) -> dict[str, Any]:
    if client is None:
        return {"error": "HubSpotClient not provided", "tool": "hubspot_list_lists"}
    try:
        params = {}
        if object_type:
            params["objectTypeId"] = object_type
        resp = await client.get(
            "/crm/v3/lists",
            portal_id=portal_id,
            expected_scopes=["crm.lists.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_list_lists"}


@tool(name="hubspot_create_list", description="Create a new HubSpot list.")
async def hubspot_create_list(
    name: str,
    object_type_id: str,
    processing_type: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            "/crm/v3/lists",
            portal_id=portal_id,
            body={"name": name, "objectTypeId": object_type_id, "processingType": processing_type},
            expected_scopes=["crm.lists.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_list"}


@tool(name="hubspot_update_list", description="Update an existing HubSpot list.")
async def hubspot_update_list(
    list_id: str,
    updates: dict[str, Any],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.patch(
            f"/crm/v3/lists/{quote(list_id, safe='')}",
            portal_id=portal_id,
            body=updates,
            expected_scopes=["crm.lists.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_update_list"}


@tool(name="hubspot_add_to_list", description="Add records to a HubSpot list.")
async def hubspot_add_to_list(
    list_id: str,
    record_ids: list[str],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            f"/crm/v3/lists/{quote(list_id, safe='')}/memberships/add",
            portal_id=portal_id,
            body={"recordIds": record_ids},
            expected_scopes=["crm.lists.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_add_to_list"}


@tool(name="hubspot_remove_from_list", description="Remove records from a HubSpot list.")
async def hubspot_remove_from_list(
    list_id: str,
    record_ids: list[str],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            f"/crm/v3/lists/{quote(list_id, safe='')}/memberships/remove",
            portal_id=portal_id,
            body={"recordIds": record_ids},
            expected_scopes=["crm.lists.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_remove_from_list"}
