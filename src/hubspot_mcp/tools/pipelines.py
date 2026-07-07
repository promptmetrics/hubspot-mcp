from __future__ import annotations

from typing import Any
from urllib.parse import quote

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(name="hubspot_get_pipeline", description="Retrieve a HubSpot pipeline by ID.")
async def hubspot_get_pipeline(
    object_type: str,
    pipeline_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/crm/v3/pipelines/{object_type}/{quote(pipeline_id, safe='')}",
            portal_id=portal_id,
            expected_scopes=["crm.pipelines.orders.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_pipeline"}


@tool(name="hubspot_list_pipelines", description="List all HubSpot pipelines for an object type.")
async def hubspot_list_pipelines(
    object_type: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/crm/v3/pipelines/{object_type}",
            portal_id=portal_id,
            expected_scopes=["crm.pipelines.orders.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_list_pipelines"}


@tool(name="hubspot_create_pipeline", description="Create a new HubSpot pipeline.")
async def hubspot_create_pipeline(
    object_type: str,
    label: str,
    display_order: int,
    stages: list[dict[str, Any]],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            f"/crm/v3/pipelines/{object_type}",
            portal_id=portal_id,
            body={"label": label, "displayOrder": display_order, "stages": stages},
            expected_scopes=["crm.pipelines.orders.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_pipeline"}


@tool(name="hubspot_update_pipeline", description="Update an existing HubSpot pipeline.")
async def hubspot_update_pipeline(
    object_type: str,
    pipeline_id: str,
    updates: dict[str, Any],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.patch(
            f"/crm/v3/pipelines/{object_type}/{quote(pipeline_id, safe='')}",
            portal_id=portal_id,
            body=updates,
            expected_scopes=["crm.pipelines.orders.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_update_pipeline"}


@tool(name="hubspot_reorder_stages", description="Reorder stages in a HubSpot pipeline.")
async def hubspot_reorder_stages(
    object_type: str,
    pipeline_id: str,
    stages: list[dict[str, Any]],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.patch(
            f"/crm/v3/pipelines/{object_type}/{quote(pipeline_id, safe='')}/stages",
            portal_id=portal_id,
            body={"stages": stages},
            expected_scopes=["crm.pipelines.orders.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_reorder_stages"}
