import httpx
import pytest

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.tools.lists import (
    hubspot_get_list,
    hubspot_list_lists,
    hubspot_create_list,
    hubspot_update_list,
    hubspot_add_to_list,
    hubspot_remove_from_list,
)


@pytest.mark.asyncio
async def test_hubspot_get_list(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/crm/v3/lists/1").mock(
        return_value=httpx.Response(200, json={"id": "1", "name": "Test"})
    )
    result = await hubspot_get_list(list_id="1", client=c, portal_id="123")
    assert result["id"] == "1"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_list_lists(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/crm/v3/lists").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "1"}]})
    )
    result = await hubspot_list_lists(client=c, portal_id="123")
    assert len(result["results"]) == 1
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_list(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/lists").mock(
        return_value=httpx.Response(201, json={"id": "2"})
    )
    result = await hubspot_create_list(name="New", object_type_id="0-1", processing_type="STATIC", client=c, portal_id="123")
    assert result["id"] == "2"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_update_list(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.patch("https://api.hubapi.com/crm/v3/lists/1").mock(
        return_value=httpx.Response(200, json={"id": "1"})
    )
    result = await hubspot_update_list(list_id="1", updates={"name": "Updated"}, client=c, portal_id="123")
    assert result["id"] == "1"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_add_to_list(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/lists/1/memberships/add").mock(
        return_value=httpx.Response(200)
    )
    result = await hubspot_add_to_list(list_id="1", record_ids=["101"], client=c, portal_id="123")
    assert "error" not in result
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_remove_from_list(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/lists/1/memberships/remove").mock(
        return_value=httpx.Response(200)
    )
    result = await hubspot_remove_from_list(list_id="1", record_ids=["101"], client=c, portal_id="123")
    assert "error" not in result
    await c.close()
