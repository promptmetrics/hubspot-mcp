import httpx
import pytest

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.tools.service import (
    hubspot_get_knowledge_base_article,
    hubspot_list_kb_articles,
    hubspot_get_ticket_pipeline,
    hubspot_create_ticket_pipeline,
    hubspot_list_service_automation,
    hubspot_get_feedback_survey,
)


@pytest.mark.asyncio
async def test_hubspot_get_knowledge_base_article(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/knowledge-base/v3/articles/1").mock(
        return_value=httpx.Response(200, json={"id": "1", "title": "Test Article"})
    )
    result = await hubspot_get_knowledge_base_article(article_id="1", client=c, portal_id="123")
    assert result["id"] == "1"
    assert result["title"] == "Test Article"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_list_kb_articles(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/knowledge-base/v3/articles?limit=10&category=general").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "1", "title": "Article 1"}]})
    )
    result = await hubspot_list_kb_articles(client=c, portal_id="123", category="general")
    assert len(result["results"]) == 1
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_get_ticket_pipeline(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/crm/v3/pipelines/tickets/support").mock(
        return_value=httpx.Response(200, json={"id": "support", "label": "Support", "stages": []})
    )
    result = await hubspot_get_ticket_pipeline(pipeline_id="support", client=c, portal_id="123")
    assert result["id"] == "support"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_ticket_pipeline(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/crm/v3/pipelines/tickets").mock(
        return_value=httpx.Response(201, json={"id": "new-pipe", "label": "New Support"})
    )
    result = await hubspot_create_ticket_pipeline(
        label="New Support", display_order=1, stages=[{"label": "Open"}], client=c, portal_id="123"
    )
    assert result["id"] == "new-pipe"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_list_service_automation(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/automation/v4/workflows?limit=5").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "1", "name": "Ticket Auto-Route"}]})
    )
    result = await hubspot_list_service_automation(client=c, portal_id="123", limit=5)
    assert len(result["results"]) == 1
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_get_feedback_survey(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/feedback/v1/surveys/abc").mock(
        return_value=httpx.Response(200, json={"id": "abc", "name": "NPS Survey"})
    )
    result = await hubspot_get_feedback_survey(survey_id="abc", client=c, portal_id="123")
    assert result["id"] == "abc"
    await c.close()
