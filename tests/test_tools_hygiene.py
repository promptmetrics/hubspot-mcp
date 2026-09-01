import httpx
import pytest

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.tools.hygiene import (
    hubspot_bulk_update_objects,
    hubspot_find_duplicates,
    hubspot_merge_objects,
    hubspot_preview_segment,
)


@pytest.mark.asyncio
async def test_hubspot_find_duplicates(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "1", "properties": {"email": "dup@example.com"}},
            {"id": "2", "properties": {"email": "dup@example.com"}},
        ]})
    )
    result = await hubspot_find_duplicates(object_type="contacts", search_field="email", client=c, portal_id="123")
    assert result["total_duplicates"] == 2
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_merge_objects(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/merge").mock(
        return_value=httpx.Response(200, json={"id": "1"})
    )
    result = await hubspot_merge_objects(primary_object_id="1", object_id_to_merge="2", client=c, portal_id="123")
    assert result["id"] == "1"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_bulk_update_objects(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/batch/update").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "1"}], "errors": []})
    )
    result = await hubspot_bulk_update_objects(object_type="contacts", records=[{"id": "1", "properties": {"email": "new@example.com"}}], client=c, portal_id="123")
    assert result["succeeded"] == 1
    await c.close()


# ---------------------------------------------------------------------------
# M9: merge honors object_type (default contacts) and expects write+delete
# scopes, matching the registry's destructive classification.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_objects_companies_endpoint(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    route = respx_mock.post("https://api.hubapi.com/crm/v3/objects/companies/merge").mock(
        return_value=httpx.Response(200, json={"id": "9"})
    )
    result = await hubspot_merge_objects(
        primary_object_id="9", object_id_to_merge="10", client=c, portal_id="123", object_type="companies"
    )
    assert result["id"] == "9"
    import json as _json
    body = _json.loads(route.calls[0].request.content)
    assert body == {"primaryObjectId": "9", "objectIdToMerge": "10"}
    await c.close()


@pytest.mark.asyncio
async def test_merge_objects_invalid_object_type_raises(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    with pytest.raises(ValueError, match="Invalid object_type"):
        await hubspot_merge_objects(
            primary_object_id="1", object_id_to_merge="2", client=c, portal_id="123",
            object_type="contacts/merge?",
        )
    assert len(respx_mock.calls) == 0
    await c.close()


# ---------------------------------------------------------------------------
# M8: object_type must be validated before URL construction — a crafted value
# like "contacts/batch/archive?" would otherwise redirect a bulk update to the
# archive endpoint without tripping the destructive-count gate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_type", ["contacts/batch/archive?", "../pipelines", "contacts#frag"])
async def test_find_duplicates_rejects_crafted_object_type(respx_mock, bad_type):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    with pytest.raises(ValueError, match="Invalid object_type"):
        await hubspot_find_duplicates(object_type=bad_type, search_field="email", client=c, portal_id="123")
    assert len(respx_mock.calls) == 0
    await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_type", ["contacts/batch/archive?", "../pipelines"])
async def test_bulk_update_rejects_crafted_object_type(respx_mock, bad_type):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    with pytest.raises(ValueError, match="Invalid object_type"):
        await hubspot_bulk_update_objects(
            object_type=bad_type,
            records=[{"id": "1", "properties": {"email": "a@example.com"}}],
            client=c,
            portal_id="123",
        )
    assert len(respx_mock.calls) == 0
    await c.close()


@pytest.mark.asyncio
async def test_preview_segment_rejects_crafted_object_type(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    with pytest.raises(ValueError, match="Invalid object_type"):
        await hubspot_preview_segment(object_type="contacts/batch/archive?", query={}, client=c, portal_id="123")
    assert len(respx_mock.calls) == 0
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_preview_segment(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(200, json={"total": 5, "results": [{"id": "1"}]})
    )
    result = await hubspot_preview_segment(object_type="contacts", query={}, client=c, portal_id="123")
    assert result["total"] == 5
    await c.close()


# ---------------------------------------------------------------------------
# Bug 3: find_duplicates must request `properties` + `limit` and paginate, or
# phone/domain duplicates come back empty and large portals look dup-free past
# the first 100-record page.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_duplicates_requests_properties_and_limit(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    route = respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await hubspot_find_duplicates(object_type="contacts", search_field="email", client=c, portal_id="123")
    import json as _json
    body = _json.loads(route.calls[0].request.content)
    assert body["properties"] == ["email"]
    assert body["limit"] == 100
    await c.close()


@pytest.mark.asyncio
async def test_find_duplicates_phone_requests_calculated_field(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    route = respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await hubspot_find_duplicates(object_type="contacts", search_field="phone", client=c, portal_id="123")
    import json as _json
    body = _json.loads(route.calls[0].request.content)
    assert "hs_searchable_calculated_phone_number" in body["properties"]
    assert "phone" in body["properties"]
    await c.close()


@pytest.mark.asyncio
async def test_find_duplicates_paginates_across_pages(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    same_email = "dup@example.com"
    page1 = httpx.Response(
        200,
        json={
            "results": [
                {"id": "1", "properties": {"email": same_email}},
                {"id": "2", "properties": {"email": same_email}},
            ],
            "paging": {"next": {"after": "2"}},
        },
    )
    page2 = httpx.Response(
        200,
        json={"results": [{"id": "3", "properties": {"email": same_email}}]},
    )

    calls = {"i": 0}

    def _side(request):
        calls["i"] += 1
        return page1 if calls["i"] == 1 else page2

    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/search").mock(side_effect=_side)
    result = await hubspot_find_duplicates(object_type="contacts", search_field="email", client=c, portal_id="123")
    assert calls["i"] == 2  # paginated to a second page
    assert result["total_duplicates"] == 3
    assert result["records_scanned"] == 3
    assert result["truncated"] is False
    await c.close()


# ---------------------------------------------------------------------------
# Bug A (0.2.4): bulk update silently no-opped on mis-shaped records.  HubSpot's
# /batch/update applies only each input's ``properties`` sub-object; a flat
# record ({"id": ..., "closedate": ...}) or empty ``properties`` produces an
# empty update that returns 200 echoing the OLD values, which the tool counted
# as "succeeded".  The tool must reject the shape before any HTTP call and
# report advisory verification of the echoed values.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_update_rejects_flat_records(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    route = respx_mock.post("https://api.hubapi.com/crm/v3/objects/deals/batch/update").mock(
        return_value=httpx.Response(200, json={"results": [], "errors": []})
    )
    result = await hubspot_bulk_update_objects(
        object_type="deals",
        records=[
            {"id": "1", "properties": {"closedate": "2026-09-30"}},
            {"id": "2", "closedate": "2026-09-30"},
        ],
        client=c,
        portal_id="123",
    )
    assert result["error"] == "validation_failed"
    assert result["tool"] == "hubspot_bulk_update_objects"
    reasons = {e["index"]: e["reason"] for e in result["validation_errors"]}
    assert 1 in reasons and "properties" in reasons[1]
    assert 0 not in reasons
    assert len(route.calls) == 0  # rejected before any HTTP call
    await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_record",
    [
        {"id": "1"},  # properties missing
        {"id": "1", "properties": {}},  # properties empty
        {"id": "1", "properties": "closedate=2026-09-30"},  # properties not a dict
        {"properties": {"closedate": "2026-09-30"}},  # id missing
        "not-a-dict",
    ],
)
async def test_bulk_update_rejects_empty_or_malformed_records(respx_mock, bad_record):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    route = respx_mock.post("https://api.hubapi.com/crm/v3/objects/deals/batch/update").mock(
        return_value=httpx.Response(200, json={"results": [], "errors": []})
    )
    result = await hubspot_bulk_update_objects(
        object_type="deals", records=[bad_record], client=c, portal_id="123"
    )
    assert result["error"] == "validation_failed"
    assert result["validation_errors"][0]["index"] == 0
    assert len(route.calls) == 0
    await c.close()


@pytest.mark.asyncio
async def test_bulk_update_rejects_empty_records_list(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    result = await hubspot_bulk_update_objects(object_type="deals", records=[], client=c, portal_id="123")
    assert result["error"] == "validation_failed"
    assert len(respx_mock.calls) == 0
    await c.close()


@pytest.mark.asyncio
async def test_bulk_update_sends_properties_in_outbound_body(respx_mock):
    """The regression test that would have caught Bug A: assert the actual
    HTTP body, not just the tool's success envelope."""
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    route = respx_mock.post("https://api.hubapi.com/crm/v3/objects/deals/batch/update").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"id": "1", "properties": {"closedate": "2026-09-30"}},
                    {"id": "2", "properties": {"closedate": "2026-09-30"}},
                ],
                "errors": [],
            },
        )
    )
    records = [
        {"id": "1", "properties": {"closedate": "2026-09-30"}},
        {"id": "2", "properties": {"closedate": "2026-09-30"}},
    ]
    result = await hubspot_bulk_update_objects(object_type="deals", records=records, client=c, portal_id="123")
    assert result["succeeded"] == 2
    import json as _json
    body = _json.loads(route.calls[0].request.content)
    assert body["inputs"] == records
    for inp in body["inputs"]:
        assert inp["properties"], "batch/update input must carry non-empty properties"
    await c.close()


@pytest.mark.asyncio
async def test_bulk_update_reports_unverified_on_echoed_old_values(respx_mock):
    """A 200 echoing the OLD values must show up as unverified — never as a
    silent success.  ``succeeded`` stays count-based (advisory verification
    does not flip it)."""
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/deals/batch/update").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"id": "1", "properties": {"closedate": "2025-12-10"}}],  # old value echoed
                "errors": [],
            },
        )
    )
    result = await hubspot_bulk_update_objects(
        object_type="deals",
        records=[{"id": "1", "properties": {"closedate": "2026-09-30"}}],
        client=c,
        portal_id="123",
    )
    assert result["succeeded"] == 1  # unchanged semantics
    assert result["verification"] == {"verified": 0, "unverified": 1}
    await c.close()


@pytest.mark.asyncio
async def test_bulk_update_reports_verified_on_echoed_new_values(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/deals/batch/update").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"id": "1", "properties": {"closedate": "2026-09-30", "hs_lastmodifieddate": "x"}}],
                "errors": [],
            },
        )
    )
    result = await hubspot_bulk_update_objects(
        object_type="deals",
        records=[{"id": "1", "properties": {"closedate": "2026-09-30"}}],
        client=c,
        portal_id="123",
    )
    assert result["verification"] == {"verified": 1, "unverified": 0}
    await c.close()


@pytest.mark.asyncio
async def test_find_duplicates_reports_truncated_at_scan_cap(respx_mock, monkeypatch):
    from hubspot_mcp.tools import hygiene as hygiene_mod

    # Force a tiny cap so we don't need 21 real pages to exercise truncation.
    monkeypatch.setattr(hygiene_mod, "_MAX_SCAN_RECORDS", 2)

    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    page = httpx.Response(
        200,
        json={
            "results": [{"id": "1", "properties": {"email": "a@example.com"}}],
            "paging": {"next": {"after": "1"}},  # always another page -> hits the cap
        },
    )
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/search").mock(return_value=page)
    result = await hubspot_find_duplicates(object_type="contacts", search_field="email", client=c, portal_id="123")
    assert result["truncated"] is True
    assert result["records_scanned"] == 2
    await c.close()
