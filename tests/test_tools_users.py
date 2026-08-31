import httpx
import pytest

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.tools.users import (
    hubspot_get_user,
    hubspot_list_users,
    hubspot_create_user,
    hubspot_update_user,
    hubspot_deactivate_user,
)


@pytest.mark.asyncio
async def test_hubspot_get_user(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/settings/v3/users/1").mock(
        return_value=httpx.Response(200, json={"id": "1", "email": "a@b.com"})
    )
    result = await hubspot_get_user(user_id="1", client=c, portal_id="123")
    assert result["id"] == "1"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_list_users(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/settings/v3/users").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "1"}]})
    )
    result = await hubspot_list_users(client=c, portal_id="123")
    assert len(result["results"]) == 1
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_user(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/settings/v3/users").mock(
        return_value=httpx.Response(201, json={"id": "2"})
    )
    result = await hubspot_create_user(email="new@example.com", role_id="admin", client=c, portal_id="123")
    assert result["id"] == "2"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_update_user(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.patch("https://api.hubapi.com/settings/v3/users/1").mock(
        return_value=httpx.Response(200, json={"id": "1"})
    )
    result = await hubspot_update_user(user_id="1", updates={"roleId": "user"}, client=c, portal_id="123")
    assert result["id"] == "1"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_deactivate_user(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.delete("https://api.hubapi.com/settings/v3/users/1").mock(
        return_value=httpx.Response(204)
    )
    result = await hubspot_deactivate_user(user_id="1", client=c, portal_id="123")
    assert "error" not in result
    await c.close()
