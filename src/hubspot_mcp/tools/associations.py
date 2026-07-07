from __future__ import annotations

from typing import Any
from urllib.parse import quote

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(name="hubspot_get_association_schema", description="Retrieve association labels between two object types.")
async def hubspot_get_association_schema(
    from_object_type: str,
    to_object_type: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/crm/v4/associations/{from_object_type}/{to_object_type}/labels",
            portal_id=portal_id,
            expected_scopes=["crm.objects.associations.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_association_schema"}


@tool(name="hubspot_create_association_schema", description="Create a new association label between two object types.")
async def hubspot_create_association_schema(
    from_object_type: str,
    to_object_type: str,
    name: str,
    label: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            f"/crm/v4/associations/{from_object_type}/{to_object_type}/labels",
            portal_id=portal_id,
            body={"name": name, "label": label},
            expected_scopes=["crm.objects.associations.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_association_schema"}


@tool(name="hubspot_associate_records", description="Create an association between two HubSpot records.")
async def hubspot_associate_records(
    from_object_type: str,
    from_object_id: str,
    to_object_type: str,
    to_object_id: str,
    association_type_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.put(
            f"/crm/v4/objects/{from_object_type}/{quote(from_object_id, safe='')}/associations/{to_object_type}/{quote(to_object_id, safe='')}",
            portal_id=portal_id,
            body=[{"associationTypeId": association_type_id}],
            expected_scopes=[f"crm.objects.{from_object_type}.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_associate_records"}


@tool(name="hubspot_disassociate_records", description="Remove an association between two HubSpot records.")
async def hubspot_disassociate_records(
    from_object_type: str,
    from_object_id: str,
    to_object_type: str,
    to_object_id: str,
    association_type_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.delete(
            f"/crm/v4/objects/{from_object_type}/{quote(from_object_id, safe='')}/associations/{to_object_type}/{quote(to_object_id, safe='')}/{quote(association_type_id, safe='')}",
            portal_id=portal_id,
            expected_scopes=[f"crm.objects.{from_object_type}.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_disassociate_records"}


@tool(name="hubspot_list_associated_records", description="List all records of a target type associated with a given source record.")
async def hubspot_list_associated_records(
    from_object_type: str,
    from_object_id: str,
    to_object_type: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/crm/v4/objects/{from_object_type}/{quote(from_object_id, safe='')}/associations/{to_object_type}",
            portal_id=portal_id,
            expected_scopes=[f"crm.objects.{from_object_type}.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_list_associated_records"}
