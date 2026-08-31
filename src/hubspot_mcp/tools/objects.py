from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from hubspot_mcp.checkpoint import CheckpointManager
from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.progress import ProgressTracker
from hubspot_mcp.tools import tool
from hubspot_mcp.cache import STANDARD_OBJECT_TYPES as _VALID_OBJECT_TYPES, SchemaCache
from hubspot_mcp.validation import validate_properties


def _validate_object_type(object_type: str, portal_id: str) -> None:
    if object_type in _VALID_OBJECT_TYPES:
        return
    cache = SchemaCache(portal_id)
    if cache.get(object_type) is not None:
        return
    raise ValueError(
        f"Invalid object_type '{object_type}'. "
        f"Must be one of: {', '.join(sorted(_VALID_OBJECT_TYPES))} "
        f"or a discovered custom object type."
    )


@tool(name="hubspot_get_object", description="Retrieve a HubSpot object by ID.")
async def hubspot_get_object(
    object_id: str,
    object_type: str,
    client: HubSpotClient,
    portal_id: str,
    properties: list[str] | None = None,
) -> dict[str, Any]:
    _validate_object_type(object_type, portal_id)
    path = f"/crm/v3/objects/{object_type}/{quote(object_id, safe='')}"
    if properties:
        path += "?" + urlencode({"properties": ",".join(properties)})
    try:
        resp = await client.get(
            path,
            portal_id=portal_id,
            expected_scopes=[f"crm.objects.{object_type}.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_object"}


@tool(name="hubspot_search_objects", description="Search HubSpot objects using filter groups.")
async def hubspot_search_objects(
    object_type: str,
    query: dict[str, Any],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    _validate_object_type(object_type, portal_id)
    try:
        resp = await client.post(
            f"/crm/v3/objects/{object_type}/search",
            portal_id=portal_id,
            body=query,
            expected_scopes=[f"crm.objects.{object_type}.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_search_objects"}


@tool(name="hubspot_create_object", description="Create a new HubSpot object record.")
async def hubspot_create_object(
    object_type: str,
    properties: dict[str, Any],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    _validate_object_type(object_type, portal_id)
    validation = validate_properties(object_type, properties, portal_id)
    if not validation["valid"]:
        return {
            "error": "validation_failed",
            "tool": "hubspot_create_object",
            "validation_errors": validation["errors"],
            "refreshed": validation.get("refreshed", False),
        }
    try:
        resp = await client.post(
            f"/crm/v3/objects/{object_type}",
            portal_id=portal_id,
            body={"properties": properties},
            expected_scopes=[f"crm.objects.{object_type}.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_object"}


@tool(name="hubspot_update_object", description="Update an existing HubSpot object record.")
async def hubspot_update_object(
    object_id: str,
    object_type: str,
    properties: dict[str, Any],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    _validate_object_type(object_type, portal_id)
    validation = validate_properties(object_type, properties, portal_id)
    if not validation["valid"]:
        return {
            "error": "validation_failed",
            "tool": "hubspot_update_object",
            "validation_errors": validation["errors"],
            "refreshed": validation.get("refreshed", False),
        }
    try:
        resp = await client.patch(
            f"/crm/v3/objects/{object_type}/{quote(object_id, safe='')}",
            portal_id=portal_id,
            body={"properties": properties},
            expected_scopes=[f"crm.objects.{object_type}.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_update_object"}


@tool(name="hubspot_delete_object", description="Permanently delete a HubSpot object record.")
async def hubspot_delete_object(
    object_id: str,
    object_type: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    _validate_object_type(object_type, portal_id)
    try:
        resp = await client.delete(
            f"/crm/v3/objects/{object_type}/{quote(object_id, safe='')}",
            portal_id=portal_id,
            expected_scopes=[f"crm.objects.{object_type}.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_delete_object"}


_BATCH_SIZE = 100


def _partition_records(
    records: list[dict[str, Any]], unique_key: str
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    seen: dict[str, dict[str, Any]] = {}
    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    skipped_duplicates = 0
    for record in records:
        key = str(record.get(unique_key, "")).lower().strip()
        if key and key in seen:
            # Bug 8d: a repeated unique_key is dropped silently — count it so the
            # result's succeeded + failed + skipped_duplicates reconciles to total.
            skipped_duplicates += 1
            continue
        if key:
            seen[key] = record
        obj_id = record.get("id") or record.get("hs_object_id")
        # id/hs_object_id are read-only in HubSpot; leaving them inside
        # `properties` 400s the whole batch.
        props = {k: v for k, v in record.items() if k not in ("id", "hs_object_id")}
        if obj_id:
            updates.append({"id": str(obj_id), "properties": props})
        else:
            creates.append({"properties": props})
    return seen, creates, updates, skipped_duplicates


def _chunk(inputs: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [inputs[i : i + size] for i in range(0, len(inputs), size)]


@tool(name="hubspot_batch_upsert_objects", description="Batch create or update HubSpot objects with input-side deduplication.")
async def hubspot_batch_upsert_objects(
    object_type: str,
    records: list[dict[str, Any]],
    client: HubSpotClient,
    portal_id: str,
    unique_key: str = "email",
    action_id: str | None = None,
) -> dict[str, Any]:
    _validate_object_type(object_type, portal_id)
    _, creates, updates, skipped_duplicates = _partition_records(records, unique_key)

    import uuid
    aid = action_id or str(uuid.uuid4())[:8]
    checkpoint = CheckpointManager(portal_id, aid)

    create_chunks = _chunk(creates, _BATCH_SIZE)
    update_chunks = _chunk(updates, _BATCH_SIZE)
    total_chunks = len(create_chunks) + len(update_chunks)
    progress = ProgressTracker(portal_id, aid, len(records), total_chunks)

    created_count = 0
    updated_count = 0
    errors: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for idx, chunk in enumerate(create_chunks):
        chunk_errors: list[dict[str, Any]] = []
        chunk_succeeded = 0
        try:
            resp = await client.post(
                f"/crm/v3/objects/{object_type}/batch/create",
                portal_id=portal_id,
                body={"inputs": chunk},
                expected_scopes=[f"crm.objects.{object_type}.write"],
            )
            body = resp.body
            chunk_succeeded = len(body.get("results", []))
            created_count += chunk_succeeded
            results.extend(body.get("results", []))
            chunk_errors.extend(body.get("errors", []))
        except (HubSpotError, RateLimitError, ScopeError) as exc:
            chunk_errors.append({"message": str(exc), "category": "BATCH_CREATE"})
            # The whole POST failed: nothing in this chunk landed.
            chunk_failed = len(chunk)
        else:
            chunk_failed = len(chunk_errors)
        errors.extend(chunk_errors)
        last_error = chunk_errors[0].get("message") if chunk_errors else None
        checkpoint.record_chunk(idx, "batch_create", chunk_succeeded, chunk_failed, chunk_errors)
        progress.record_chunk(idx, chunk_succeeded, len(chunk_errors), last_error)

    for idx, chunk in enumerate(update_chunks):
        chunk_errors = []
        chunk_succeeded = 0
        try:
            resp = await client.post(
                f"/crm/v3/objects/{object_type}/batch/update",
                portal_id=portal_id,
                body={"inputs": chunk},
                expected_scopes=[f"crm.objects.{object_type}.write"],
            )
            body = resp.body
            chunk_succeeded = len(body.get("results", []))
            updated_count += chunk_succeeded
            results.extend(body.get("results", []))
            chunk_errors.extend(body.get("errors", []))
        except (HubSpotError, RateLimitError, ScopeError) as exc:
            chunk_errors.append({"message": str(exc), "category": "BATCH_UPDATE"})
            chunk_failed = len(chunk)
        else:
            chunk_failed = len(chunk_errors)
        errors.extend(chunk_errors)
        last_error = chunk_errors[0].get("message") if chunk_errors else None
        # Offset like progress does so create/update chunks don't collide in
        # the shared checkpoint JSONL (last_completed_chunk stays monotonic).
        checkpoint.record_chunk(len(create_chunks) + idx, "batch_update", chunk_succeeded, chunk_failed, chunk_errors)
        progress.record_chunk(len(create_chunks) + idx, chunk_succeeded, len(chunk_errors), last_error)

    checkpoint.finalize()
    progress.finalize()

    return {
        "succeeded": created_count + updated_count,
        "failed": len(errors),
        "skipped_duplicates": skipped_duplicates,
        "total": len(records),
        "results": results,
        "errors": errors,
        "action_id": aid,
        "checkpoint": checkpoint.get_resume_state(),
        "progress": progress.snapshot(),
    }
