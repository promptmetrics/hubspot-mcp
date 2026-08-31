import httpx
import pytest

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.tools.raw_api import hubspot_raw_api


@pytest.mark.asyncio
async def test_hubspot_raw_api_get(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "1"}]})
    )
    result = await hubspot_raw_api(method="GET", path="/crm/v3/objects/contacts", client=c, portal_id="123")
    assert len(result["results"]) == 1
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_raw_api_post(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts").mock(
        return_value=httpx.Response(201, json={"id": "2"})
    )
    result = await hubspot_raw_api(method="POST", path="/crm/v3/objects/contacts", body={"properties": {"email": "test@example.com"}}, client=c, portal_id="123")
    assert result["id"] == "2"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_raw_api_unsupported_method():
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    result = await hubspot_raw_api(method="PUT", path="/test", client=c, portal_id="123")
    assert "error" in result
    await c.close()
