from __future__ import annotations

from typing import Any
from urllib.parse import quote

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool

_VALID_OBJECT_TYPES = frozenset({"contacts", "companies", "deals", "tickets"})


def _validate_object_type(object_type: str) -> None:
    if object_type not in _VALID_OBJECT_TYPES:
        raise ValueError(f"Invalid object_type '{object_type}'")


@tool(name="hubspot_get_property", description="Retrieve a HubSpot custom property definition.")
async def hubspot_get_property(
    property_name: str,
    object_type: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    _validate_object_type(object_type)
    try:
        resp = await client.get(
            f"/crm/v3/properties/{object_type}/{quote(property_name, safe='')}",
            portal_id=portal_id,
            expected_scopes=[f"crm.schemas.{object_type}.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_property"}


@tool(name="hubspot_list_properties", description="List all properties for a HubSpot object type.")
async def hubspot_list_properties(
    object_type: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    _validate_object_type(object_type)
    try:
        resp = await client.get(
            f"/crm/v3/properties/{object_type}",
            portal_id=portal_id,
            expected_scopes=[f"crm.schemas.{object_type}.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_list_properties"}


@tool(name="hubspot_create_property", description="Create a new HubSpot custom property.")
async def hubspot_create_property(
    object_type: str,
    name: str,
    label: str,
    property_type: str,
    field_type: str,
    group_name: str = "contactinformation",
    client: HubSpotClient | None = None,
    portal_id: str = "",
) -> dict[str, Any]:
    if client is None:
        return {"error": "HubSpotClient not provided", "tool": "hubspot_create_property"}
    _validate_object_type(object_type)
    try:
        resp = await client.post(
            f"/crm/v3/properties/{object_type}",
            portal_id=portal_id,
            body={
                "name": name,
                "label": label,
                "type": property_type,
                "fieldType": field_type,
                "groupName": group_name,
            },
            expected_scopes=[f"crm.schemas.{object_type}.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_property"}


@tool(name="hubspot_update_property", description="Update an existing HubSpot custom property.")
async def hubspot_update_property(
    property_name: str,
    object_type: str,
    updates: dict[str, Any],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    _validate_object_type(object_type)
    try:
        resp = await client.patch(
            f"/crm/v3/properties/{object_type}/{quote(property_name, safe='')}",
            portal_id=portal_id,
            body=updates,
            expected_scopes=[f"crm.schemas.{object_type}.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_update_property"}


@tool(name="hubspot_delete_property", description="Delete a HubSpot custom property.")
async def hubspot_delete_property(
    property_name: str,
    object_type: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    _validate_object_type(object_type)
    try:
        resp = await client.delete(
            f"/crm/v3/properties/{object_type}/{quote(property_name, safe='')}",
            portal_id=portal_id,
            expected_scopes=[f"crm.schemas.{object_type}.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_delete_property"}
