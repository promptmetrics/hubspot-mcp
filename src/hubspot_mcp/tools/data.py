from __future__ import annotations

from typing import Any
from urllib.parse import quote

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(
    name="hubspot_import_data",
    description="Import data into HubSpot via the CRM imports API.",
)
async def hubspot_import_data(
    import_name: str,
    import_file: str,
    object_type: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            "/crm/v3/imports",
            portal_id=portal_id,
            body={
                "name": import_name,
                "files": [{"fileName": import_file, "fileFormat": "CSV"}],
                "importRequest": {"objectType": object_type},
            },
            expected_scopes=["crm.objects.imports.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_import_data"}


@tool(
    name="hubspot_export_data",
    description="Export data from HubSpot via the CRM exports API.",
)
async def hubspot_export_data(
    export_name: str,
    object_type: str,
    properties: list[str],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            "/crm/v3/exports",
            portal_id=portal_id,
            body={
                "name": export_name,
                "objectType": object_type,
                "format": "CSV",
                "properties": properties,
            },
            expected_scopes=["crm.objects.exports.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_export_data"}


@tool(
    name="hubspot_get_import_status",
    description="Retrieve the status of a HubSpot CRM import by ID.",
)
async def hubspot_get_import_status(
    import_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/crm/v3/imports/{quote(import_id, safe='')}",
            portal_id=portal_id,
            expected_scopes=["crm.objects.imports.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_import_status"}
