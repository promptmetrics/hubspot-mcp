import httpx
import pytest

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.tools.associations import (
    hubspot_get_association_schema,
    hubspot_create_association_schema,
    hubspot_associate_records,
    hubspot_disassociate_records,
)


@pytest.mark.asyncio
async def test_hubspot_get_association_schema(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/crm/v4/associations/contacts/companies/labels").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "1"}]})
    )
    result = await hubspot_get_association_schema(from_object_type="contacts", to_object_type="companies", client=c, portal_id="123")
    assert len(result["results"]) == 1
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_association_schema(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v4/associations/contacts/companies/labels").mock(
        return_value=httpx.Response(201, json={"id": "2"})
    )
    result = await hubspot_create_association_schema(from_object_type="contacts", to_object_type="companies", name="primary", label="Primary", client=c, portal_id="123")
    assert result["id"] == "2"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_associate_records(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.put("https://api.hubapi.com/crm/v4/objects/contacts/1/associations/companies/10").mock(
        return_value=httpx.Response(200)
    )
    result = await hubspot_associate_records(
        from_object_type="contacts", from_object_id="1",
        to_object_type="companies", to_object_id="10",
        association_type_id="1", client=c, portal_id="123",
    )
    assert "error" not in result
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_disassociate_records(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.delete("https://api.hubapi.com/crm/v4/objects/contacts/1/associations/companies/10/1").mock(
        return_value=httpx.Response(204)
    )
    result = await hubspot_disassociate_records(
        from_object_type="contacts", from_object_id="1",
        to_object_type="companies", to_object_id="10",
        association_type_id="1", client=c, portal_id="123",
    )
    assert "error" not in result
    await c.close()
