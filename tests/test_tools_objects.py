import httpx
import pytest

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.tools.objects import (
    hubspot_batch_upsert_objects,
    hubspot_create_object,
    hubspot_delete_object,
    hubspot_get_object,
    hubspot_search_objects,
    hubspot_update_object,
)


@pytest.mark.asyncio
async def test_hubspot_get_object(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(200, json={"id": "1", "properties": {"email": "a@b.com"}})
    )
    result = await hubspot_get_object(object_id="1", object_type="contacts", client=c, portal_id="123")
    assert result["id"] == "1"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_get_object_scopes_requested_properties(respx_mock):
    # Bug B (0.2.4): snapshot pre-fetches must scope the GET to the properties
    # being changed so undo snapshots don't carry read-only system fields.
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    route = respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(200, json={"id": "1", "properties": {"email": "a@b.com"}})
    )
    result = await hubspot_get_object(
        object_id="1", object_type="contacts", client=c, portal_id="123",
        properties=["email", "firstname"],
    )
    assert result["id"] == "1"
    assert route.calls[0].request.url.params["properties"] == "email,firstname"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_search_objects(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "1"}]})
    )
    result = await hubspot_search_objects(object_type="contacts", query={}, client=c, portal_id="123")
    assert len(result["results"]) == 1
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_object(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts").mock(
        return_value=httpx.Response(201, json={"id": "2"})
    )
    result = await hubspot_create_object(object_type="contacts", properties={"email": "new@example.com"}, client=c, portal_id="123")
    assert result["id"] == "2"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_update_object(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.patch("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(200, json={"id": "1"})
    )
    result = await hubspot_update_object(object_id="1", object_type="contacts", properties={"email": "updated@example.com"}, client=c, portal_id="123")
    assert result["id"] == "1"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_delete_object(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.delete("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(204)
    )
    result = await hubspot_delete_object(object_id="1", object_type="contacts", client=c, portal_id="123")
    assert "error" not in result
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_batch_upsert_objects(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/batch/create").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "3"}], "errors": []})
    )
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/batch/update").mock(
        return_value=httpx.Response(200, json={"results": [], "errors": []})
    )
    result = await hubspot_batch_upsert_objects(
        object_type="contacts",
        records=[{"email": "batch@example.com"}],
        client=c,
        portal_id="123",
    )
    assert result["succeeded"] == 1
    assert "progress" in result
    assert result["progress"]["action_id"] == result["action_id"]
    assert result["progress"]["total_chunks"] == 1
    assert result["progress"]["completed_chunks"] == 1
    assert result["skipped_duplicates"] == 0
    await c.close()


@pytest.mark.asyncio
async def test_batch_upsert_counts_skipped_duplicates(respx_mock):
    """Bug 8d: repeated unique_keys are dropped but counted, and the result
    reconciles: succeeded + failed + skipped_duplicates == total."""
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/batch/create").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "3"}, {"id": "4"}], "errors": []})
    )
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/batch/update").mock(
        return_value=httpx.Response(200, json={"results": [], "errors": []})
    )
    records = [
        {"email": "dup@example.com"},
        {"email": "dup@example.com"},  # duplicate of row 0 → skipped
        {"email": "dup@example.com"},  # duplicate of row 0 → skipped
        {"email": "unique@example.com"},
    ]
    result = await hubspot_batch_upsert_objects(
        object_type="contacts", records=records, client=c, portal_id="123"
    )
    assert result["skipped_duplicates"] == 2
    assert result["succeeded"] == 2
    assert result["failed"] == 0
    assert result["succeeded"] + result["failed"] + result["skipped_duplicates"] == result["total"]


# ---------------------------------------------------------------------------
# M7: read-only id/hs_object_id keys must be stripped from batch properties
# (HubSpot 400s the whole batch otherwise).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_upsert_strips_record_ids_from_properties(respx_mock):
    import json as _json

    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    create_route = respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/batch/create").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "3"}], "errors": []})
    )
    update_route = respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/batch/update").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "101"}, {"id": "102"}], "errors": []})
    )
    records = [
        {"id": "101", "email": "a@example.com", "city": "Berlin"},
        {"hs_object_id": "102", "email": "b@example.com"},
        {"email": "c@example.com"},
    ]
    await hubspot_batch_upsert_objects(object_type="contacts", records=records, client=c, portal_id="123")

    update_inputs = _json.loads(update_route.calls[0].request.content)["inputs"]
    assert {i["id"] for i in update_inputs} == {"101", "102"}
    for i in update_inputs:
        assert "id" not in i["properties"]
        assert "hs_object_id" not in i["properties"]
    assert update_inputs[0]["properties"]["email"] == "a@example.com"
    assert update_inputs[0]["properties"]["city"] == "Berlin"

    create_inputs = _json.loads(create_route.calls[0].request.content)["inputs"]
    assert create_inputs == [{"properties": {"email": "c@example.com"}}]
    await c.close()


# ---------------------------------------------------------------------------
# M6: checkpoint must record what actually happened — a whole-chunk failure is
# succeeded=0 / failed=len(chunk), and create/update chunk indices must not
# collide in the shared JSONL.
# ---------------------------------------------------------------------------


def _checkpoint_entries(action_id: str) -> list[dict]:
    import json as _json
    from pathlib import Path

    base = Path.home() / ".claude" / "hubspot" / "123"
    for sub in ("completed", "in_flight"):
        path = base / sub / f"{action_id}.jsonl"
        if path.exists():
            return [_json.loads(line) for line in path.read_text().strip().splitlines()]
    raise AssertionError(f"no checkpoint file for {action_id}")


@pytest.mark.asyncio
async def test_checkpoint_exception_chunk_records_zero_succeeded(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/batch/create").mock(
        return_value=httpx.Response(400, json={"message": "bad batch"})
    )
    records = [{"email": "a@example.com"}, {"email": "b@example.com"}]
    result = await hubspot_batch_upsert_objects(
        object_type="contacts", records=records, client=c, portal_id="123", action_id="ckpt-fail"
    )

    assert result["succeeded"] == 0
    entries = _checkpoint_entries("ckpt-fail")
    assert entries[0]["succeeded"] == 0
    assert entries[0]["failed"] == len(records)
    await c.close()


@pytest.mark.asyncio
async def test_checkpoint_chunk_indices_do_not_collide(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/batch/create").mock(
        return_value=httpx.Response(200, json={"results": [], "errors": []})
    )
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts/batch/update").mock(
        return_value=httpx.Response(200, json={"results": [], "errors": []})
    )
    # 101 creates -> chunks 0,1; 101 updates -> chunks 2,3 (offset, no collision)
    records = [{"email": f"c{i}@example.com"} for i in range(101)]
    records += [{"id": str(1000 + i), "email": f"u{i}@example.com"} for i in range(101)]
    await hubspot_batch_upsert_objects(
        object_type="contacts", records=records, client=c, portal_id="123", action_id="ckpt-idx"
    )

    entries = _checkpoint_entries("ckpt-idx")
    assert [e["chunk_index"] for e in entries] == [0, 1, 2, 3]
    assert [e["operation"] for e in entries] == [
        "batch_create", "batch_create", "batch_update", "batch_update",
    ]
    await c.close()
