from __future__ import annotations

from typing import Any

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(name="hubspot_find_duplicates", description="Find duplicate HubSpot contacts by email, phone, or domain.")
async def hubspot_find_duplicates(
    object_type: str,
    search_field: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        query = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": search_field,
                            "operator": "HAS_PROPERTY",
                        }
                    ]
                }
            ]
        }
        resp = await client.post(
            f"/crm/v3/objects/{object_type}/search",
            portal_id=portal_id,
            body=query,
            expected_scopes=[f"crm.objects.{object_type}.read"],
        )
        results = resp.body.get("results", [])
        seen: dict[str, list[dict[str, Any]]] = {}
        for record in results:
            val = record.get("properties", {}).get(search_field, "").lower().strip()
            if val:
                seen.setdefault(val, []).append(record)
        duplicates = {k: v for k, v in seen.items() if len(v) > 1}
        return {
            "object_type": object_type,
            "search_field": search_field,
            "duplicate_groups": duplicates,
            "total_duplicates": sum(len(v) for v in duplicates.values()),
        }
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_find_duplicates"}


@tool(name="hubspot_merge_objects", description="Merge two HubSpot contact records.")
async def hubspot_merge_objects(
    primary_object_id: str,
    object_id_to_merge: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            "/crm/v3/objects/contacts/merge",
            portal_id=portal_id,
            body={
                "primaryObjectId": primary_object_id,
                "objectIdToMerge": object_id_to_merge,
            },
            expected_scopes=["crm.objects.contacts.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_merge_objects"}


_BATCH_SIZE = 100


@tool(name="hubspot_bulk_update_objects", description="Bulk update HubSpot objects.")
async def hubspot_bulk_update_objects(
    object_type: str,
    records: list[dict[str, Any]],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    succeeded = 0

    for i in range(0, len(records), _BATCH_SIZE):
        chunk = records[i : i + _BATCH_SIZE]
        try:
            resp = await client.post(
                f"/crm/v3/objects/{object_type}/batch/update",
                portal_id=portal_id,
                body={"inputs": chunk},
                expected_scopes=[f"crm.objects.{object_type}.write"],
            )
            body = resp.body
            results.extend(body.get("results", []))
            errors.extend(body.get("errors", []))
            succeeded += len(body.get("results", []))
        except (HubSpotError, RateLimitError, ScopeError) as exc:
            errors.append({"message": str(exc), "category": "BATCH_UPDATE"})

    return {
        "succeeded": succeeded,
        "failed": len(errors),
        "total": len(records),
        "results": results,
        "errors": errors,
    }


@tool(name="hubspot_preview_segment", description="Preview a segment of HubSpot objects matching filters.")
async def hubspot_preview_segment(
    object_type: str,
    query: dict[str, Any],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            f"/crm/v3/objects/{object_type}/search",
            portal_id=portal_id,
            body=query,
            expected_scopes=[f"crm.objects.{object_type}.read"],
        )
        body = resp.body
        return {
            "object_type": object_type,
            "total": body.get("total", 0),
            "results": body.get("results", []),
            "preview": True,
        }
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_preview_segment"}
