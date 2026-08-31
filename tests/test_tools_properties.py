import httpx
import pytest

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.tools.properties import (
    hubspot_get_property,
    hubspot_list_properties,
    hubspot_create_property,
    hubspot_update_property,
    hubspot_delete_property,
)


@pytest.mark.asyncio
async def test_hubspot_get_property(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/crm/v3/properties/contacts/email").mock(
        return_value=httpx.Response(200, json={"name": "email", "label": "Email"})
    )
    result = await hubspot_get_property(property_name="email", object_type="contacts", client=c, portal_id="123")
    assert result["name"] == "email"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_list_properties(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/crm/v3/properties/contacts").mock(
        return_value=httpx.Response(200, json={"results": [{"name": "email"}]})
    )
    result = await hubspot_list_properties(object_type="contacts", client=c, portal_id="123")
    assert len(result["results"]) == 1
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_property(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/properties/contacts").mock(
        return_value=httpx.Response(201, json={"name": "custom_field"})
    )
    result = await hubspot_create_property(
        object_type="contacts", name="custom_field", label="Custom Field",
        property_type="string", field_type="text", client=c, portal_id="123",
    )
    assert result["name"] == "custom_field"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_update_property(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.patch("https://api.hubapi.com/crm/v3/properties/contacts/email").mock(
        return_value=httpx.Response(200, json={"name": "email"})
    )
    result = await hubspot_update_property(property_name="email", object_type="contacts", updates={"label": "New"}, client=c, portal_id="123")
    assert result["name"] == "email"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_delete_property(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.delete("https://api.hubapi.com/crm/v3/properties/contacts/custom_field").mock(
        return_value=httpx.Response(204)
    )
    result = await hubspot_delete_property(property_name="custom_field", object_type="contacts", client=c, portal_id="123")
    assert "error" not in result
    await c.close()
