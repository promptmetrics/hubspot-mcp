import httpx
import pytest

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.tools.pipelines import (
    hubspot_get_pipeline,
    hubspot_list_pipelines,
    hubspot_create_pipeline,
    hubspot_update_pipeline,
    hubspot_reorder_stages,
)


@pytest.mark.asyncio
async def test_hubspot_get_pipeline(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/crm/v3/pipelines/deals/default").mock(
        return_value=httpx.Response(200, json={"id": "default", "label": "Sales"})
    )
    result = await hubspot_get_pipeline(object_type="deals", pipeline_id="default", client=c, portal_id="123")
    assert result["id"] == "default"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_list_pipelines(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/crm/v3/pipelines/deals").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "default"}]})
    )
    result = await hubspot_list_pipelines(object_type="deals", client=c, portal_id="123")
    assert len(result["results"]) == 1
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_pipeline(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/pipelines/deals").mock(
        return_value=httpx.Response(201, json={"id": "new"})
    )
    result = await hubspot_create_pipeline(object_type="deals", label="New", display_order=1, stages=[], client=c, portal_id="123")
    assert result["id"] == "new"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_update_pipeline(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.patch("https://api.hubapi.com/crm/v3/pipelines/deals/default").mock(
        return_value=httpx.Response(200, json={"id": "default"})
    )
    result = await hubspot_update_pipeline(object_type="deals", pipeline_id="default", updates={"label": "Updated"}, client=c, portal_id="123")
    assert result["id"] == "default"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_reorder_stages(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.patch("https://api.hubapi.com/crm/v3/pipelines/deals/default/stages").mock(
        return_value=httpx.Response(200)
    )
    result = await hubspot_reorder_stages(object_type="deals", pipeline_id="default", stages=[], client=c, portal_id="123")
    assert "error" not in result
    await c.close()
