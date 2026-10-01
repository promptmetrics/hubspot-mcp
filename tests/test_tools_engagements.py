import httpx
import pytest

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.tools.engagements import (
    hubspot_create_call,
    hubspot_create_email,
    hubspot_create_meeting,
    hubspot_create_note,
    hubspot_create_task,
    hubspot_get_engagement,
    hubspot_search_engagements,
)


@pytest.mark.asyncio
async def test_hubspot_get_engagement(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/crm/v3/objects/engagements/1").mock(
        return_value=httpx.Response(200, json={"id": "1"})
    )
    result = await hubspot_get_engagement(engagement_id="1", client=c, portal_id="123")
    assert result["id"] == "1"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_search_engagements(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/engagements/search").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "1"}]})
    )
    result = await hubspot_search_engagements(query={}, client=c, portal_id="123")
    assert len(result["results"]) == 1
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_note(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/notes").mock(
        return_value=httpx.Response(201, json={"id": "2"})
    )
    result = await hubspot_create_note(body="Test note", client=c, portal_id="123")
    assert result["id"] == "2"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_task(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/tasks").mock(
        return_value=httpx.Response(201, json={"id": "3"})
    )
    result = await hubspot_create_task(subject="Call", status="NOT_STARTED", timestamp="2024-01-01", client=c, portal_id="123")
    assert result["id"] == "3"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_email(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/emails").mock(
        return_value=httpx.Response(201, json={"id": "4"})
    )
    result = await hubspot_create_email(subject="Hello", body="World", client=c, portal_id="123")
    assert result["id"] == "4"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_meeting(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/meetings").mock(
        return_value=httpx.Response(201, json={"id": "5"})
    )
    result = await hubspot_create_meeting(title="Sync", start_time="2024-01-01T10:00:00Z", client=c, portal_id="123")
    assert result["id"] == "5"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_call(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/calls").mock(
        return_value=httpx.Response(201, json={"id": "6"})
    )
    result = await hubspot_create_call(title="Call", duration_ms=60000, client=c, portal_id="123")
    assert result["id"] == "6"
    await c.close()


async def test_create_note_posts_to_notes_with_a_timestamp_and_no_engagement_type(respx_mock):
    import json

    from hubspot_mcp.client import HubSpotClient
    from hubspot_mcp.config import PortalConfig
    from hubspot_mcp.tools.engagements import hubspot_create_note

    route = respx_mock.post("https://api.hubapi.com/crm/v3/objects/notes").mock(
        return_value=__import__("httpx").Response(200, json={"id": "9"})
    )
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    try:
        await hubspot_create_note(body="hi", client=c, portal_id="123")
    finally:
        await c.close()
    sent = json.loads(route.calls.last.request.content)["properties"]
    assert sent["hs_note_body"] == "hi"
    assert sent["hs_timestamp"], "HubSpot requires hs_timestamp on every engagement"
    assert "hs_engagement_type" not in sent
