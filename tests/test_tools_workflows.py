import json

import httpx
import pytest

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.tools.workflows import (
    hubspot_create_workflow,
    hubspot_enroll_workflow,
    hubspot_get_workflow,
    hubspot_list_workflows,
    hubspot_toggle_workflow,
    hubspot_update_workflow,
)


@pytest.mark.asyncio
async def test_hubspot_get_workflow(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/automation/v4/flows/1").mock(
        return_value=httpx.Response(200, json={"id": "1", "name": "Test"})
    )
    result = await hubspot_get_workflow(workflow_id="1", client=c, portal_id="123")
    assert result["id"] == "1"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_list_workflows(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/automation/v4/flows").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "1", "name": "Test"}]})
    )
    result = await hubspot_list_workflows(client=c, portal_id="123")
    assert len(result["results"]) == 1
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_workflow(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    route = respx_mock.post("https://api.hubapi.com/automation/v4/flows").mock(
        return_value=httpx.Response(201, json={"id": "2"})
    )
    result = await hubspot_create_workflow(
        name="New",
        object_type="Contact-based",
        enrollment={"type": "LIST_BASED"},
        actions=[],
        client=c,
        portal_id="123",
    )
    assert result["id"] == "2"
    sent = json.loads(route.calls.last.request.content)
    assert sent["flowType"] == "WORKFLOW"
    assert sent["objectTypeId"] == "0-1"
    assert sent["type"] == "CONTACT_FLOW"
    assert sent["enrollmentCriteria"]["type"] == "LIST_BASED"
    assert sent["isEnabled"] is False
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_update_workflow(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    route = respx_mock.put("https://api.hubapi.com/automation/v4/flows/1").mock(
        return_value=httpx.Response(200, json={"id": "1"})
    )
    result = await hubspot_update_workflow(
        workflow_id="1",
        revision_id="r9",
        body={"name": "Updated", "createdAt": "2026-01-01", "id": "1"},
        client=c,
        portal_id="123",
    )
    assert result["id"] == "1"
    sent = json.loads(route.calls.last.request.content)
    assert sent["revisionId"] == "r9"
    assert sent["name"] == "Updated"
    # server-managed fields stripped before PUT
    assert "createdAt" not in sent
    assert "id" not in sent
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_enroll_workflow(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/automation/v4/flows/1/enrollments").mock(
        return_value=httpx.Response(200)
    )
    result = await hubspot_enroll_workflow(workflow_id="1", object_ids=["101"], client=c, portal_id="123")
    assert "error" not in result
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_toggle_workflow(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/automation/v4/flows/1/toggle").mock(
        return_value=httpx.Response(200)
    )
    result = await hubspot_toggle_workflow(workflow_id="1", enabled=False, client=c, portal_id="123")
    assert "error" not in result
    await c.close()
