from __future__ import annotations

from typing import Any

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool
from hubspot_mcp.tools.objects import _validate_object_type
from hubspot_mcp.validation import validate_properties


_BATCH_SIZE = 100
# HubSpot /search caps a single page at 100 results; duplicate detection must
# page through all matches (bug 3) instead of inspecting only the first page.
_SEARCH_PAGE_SIZE = 100
# Cap on total records scanned so a huge portal can't exhaust the rate budget;
# a search reaching this cap reports ``truncated: true`` rather than silently
# looking duplicate-free past the cap.
_MAX_SCAN_RECORDS = 2000


@tool(name="hubspot_find_duplicates", description="Find duplicate HubSpot contacts by email, phone, or domain.")
async def hubspot_find_duplicates(
    object_type: str,
    search_field: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    _validate_object_type(object_type, portal_id)
    try:
        # Bug 3: the search body previously omitted ``properties``, so HubSpot
        # returned records with an empty properties map — phone/domain values
        # never came back (email only worked because it's a default-returned
        # property) and the grouping step saw "" for every record → 0 groups.
        # Request the search field explicitly, plus the normalized phone field
        # when searching by phone (HubSpot stores the searchable normalized
        # value there; ``phone`` itself is the display value we group on).
        properties = [search_field]
        if search_field == "phone":
            properties.append("hs_searchable_calculated_phone_number")
        query: dict[str, Any] = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": search_field,
                            "operator": "HAS_PROPERTY",
                        }
                    ]
                }
            ],
            "properties": properties,
            "limit": _SEARCH_PAGE_SIZE,
        }

        # Paginate via ``paging.next.after`` — a single search page returns at
        # most 100 records, so a portal with more matching records than that
        # would silently look duplicate-free past the first page.  Cap the total
        # scanned so a giant portal can't exhaust the rate budget, and report
        # the cap so a silent truncation is visible rather than a false "no
        # duplicates".
        seen: dict[str, list[dict[str, Any]]] = {}
        records_scanned = 0
        truncated = False
        after: str | None = None
        while records_scanned < _MAX_SCAN_RECORDS:
            page_query = dict(query)
            if after is not None:
                page_query["after"] = after
            resp = await client.post(
                f"/crm/v3/objects/{object_type}/search",
                portal_id=portal_id,
                body=page_query,
                expected_scopes=[f"crm.objects.{object_type}.read"],
            )
            body = resp.body
            results = body.get("results", [])
            for record in results:
                val = record.get("properties", {}).get(search_field, "").lower().strip()
                if val:
                    seen.setdefault(val, []).append(record)
            records_scanned += len(results)
            next_after = (body.get("paging") or {}).get("next", {}).get("after")
            if not next_after or not results:
                break
            after = next_after
        else:
            truncated = True

        duplicates = {k: v for k, v in seen.items() if len(v) > 1}
        return {
            "object_type": object_type,
            "search_field": search_field,
            "duplicate_groups": duplicates,
            "total_duplicates": sum(len(v) for v in duplicates.values()),
            "records_scanned": records_scanned,
            "truncated": truncated,
        }
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_find_duplicates"}


@tool(name="hubspot_merge_objects", description="Merge two HubSpot object records of the same object type.")
async def hubspot_merge_objects(
    primary_object_id: str,
    object_id_to_merge: str,
    client: HubSpotClient,
    portal_id: str,
    object_type: str = "contacts",
) -> dict[str, Any]:
    _validate_object_type(object_type, portal_id)
    try:
        resp = await client.post(
            f"/crm/v3/objects/{object_type}/merge",
            portal_id=portal_id,
            body={
                "primaryObjectId": primary_object_id,
                "objectIdToMerge": object_id_to_merge,
            },
            # Matches the registry's write+delete classification for merge —
            # the secondary record is destroyed, so a 403 should name both.
            expected_scopes=[
                f"crm.objects.{object_type}.write",
                f"crm.objects.{object_type}.delete",
            ],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_merge_objects"}


def validate_bulk_update_records(records: list[Any]) -> list[dict[str, Any]]:
    """Shape-check batch-update records; return one error dict per bad record.

    HubSpot's ``/batch/update`` applies only each input's ``properties``
    sub-object and answers 200 (echoing the OLD values) for an input without
    one — so a flat record like ``{"id": ..., "closedate": ...}`` executes as
    a silent no-op counted as success.  The shape must therefore be refused
    before any HTTP call rather than forwarded verbatim.
    """
    if not isinstance(records, list) or not records:
        return [{"index": None, "reason": "records must be a non-empty list"}]

    errors: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append({"index": index, "reason": "record must be an object with 'id' and 'properties'"})
            continue
        if not record.get("id") and not record.get("hs_object_id"):
            errors.append({"index": index, "reason": "record is missing a non-empty 'id'"})
            continue
        properties = record.get("properties")
        if not isinstance(properties, dict) or not properties:
            errors.append({
                "index": index,
                "reason": (
                    "record has no non-empty 'properties' object — flat record shape; "
                    "wrap property values in 'properties' ({\"id\": ..., \"properties\": {...}})"
                ),
            })
            continue
        extra = set(record) - {"id", "hs_object_id", "idProperty", "properties"}
        if extra:
            errors.append({
                "index": index,
                "reason": (
                    f"unexpected top-level keys {sorted(extra)} — property values belong inside 'properties'"
                ),
            })
    return errors


def _tolerant_equal(requested: Any, echoed: Any) -> bool:
    return str(requested).strip() == str(echoed).strip()


def _verify_echoed_results(
    records: list[dict[str, Any]], results: list[dict[str, Any]]
) -> dict[str, int]:
    """Advisory check of HubSpot's echo against the requested values.

    ``/batch/update`` echoes each record as it looks after the write, so an
    echo still carrying the old values means the update silently didn't apply.
    A record is *verified* only when at least one requested key is present in
    its echo and every present key matches (tolerant string compare — HubSpot
    normalizes dates/numbers on echo, so absent keys are skipped rather than
    counted against).  Advisory only: never alters ``succeeded``/``failed``.
    """
    echoed_by_id = {
        str(r.get("id")): r.get("properties") or {} for r in results if isinstance(r, dict)
    }
    verified = 0
    unverified = 0
    for record in records:
        requested = record.get("properties") or {}
        echo = echoed_by_id.get(str(record.get("id") or record.get("hs_object_id")))
        comparable = 0 if echo is None else sum(1 for k in requested if k in echo)
        if echo is not None and comparable and all(
            _tolerant_equal(v, echo[k]) for k, v in requested.items() if k in echo
        ):
            verified += 1
        else:
            unverified += 1
    return {"verified": verified, "unverified": unverified}


@tool(
    name="hubspot_bulk_update_objects",
    description=(
        "Bulk update HubSpot objects. Each record MUST be shaped "
        '{"id": "<record id>", "properties": {"<property>": <value>, ...}} — '
        "flat records (property keys at the top level) or records with an "
        "empty 'properties' object are rejected before any API call."
    ),
)
async def hubspot_bulk_update_objects(
    object_type: str,
    records: list[dict[str, Any]],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    _validate_object_type(object_type, portal_id)
    shape_errors = validate_bulk_update_records(records)
    if shape_errors:
        return {
            "error": "validation_failed",
            "tool": "hubspot_bulk_update_objects",
            "validation_errors": shape_errors,
        }
    for index, record in enumerate(records):
        validation = validate_properties(object_type, record["properties"], portal_id)
        if not validation["valid"]:
            return {
                "error": "validation_failed",
                "tool": "hubspot_bulk_update_objects",
                "validation_errors": [
                    {"index": index, **err} for err in validation["errors"]
                ],
                "refreshed": validation.get("refreshed", False),
            }

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
        "verification": _verify_echoed_results(records, results),
    }


@tool(name="hubspot_preview_segment", description="Preview a segment of HubSpot objects matching filters.")
async def hubspot_preview_segment(
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
        body = resp.body
        return {
            "object_type": object_type,
            "total": body.get("total", 0),
            "results": body.get("results", []),
            "preview": True,
        }
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_preview_segment"}
