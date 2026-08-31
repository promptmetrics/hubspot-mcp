import httpx
import pytest

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.tools.reporting import (
    hubspot_create_dashboard,
    hubspot_create_report,
    hubspot_get_report,
    hubspot_schedule_email,
)


@pytest.mark.asyncio
async def test_hubspot_create_report(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/analytics/v2/reports").mock(
        return_value=httpx.Response(201, json={"id": "r-1", "name": "Deals Q1"})
    )
    result = await hubspot_create_report(
        name="Deals Q1",
        data_source="deals",
        metrics=["count", "amount"],
        client=c,
        portal_id="123",
    )
    assert result["id"] == "r-1"
    assert result["name"] == "Deals Q1"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_report_with_optional_fields(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/analytics/v2/reports").mock(
        return_value=httpx.Response(201, json={"id": "r-2"})
    )
    result = await hubspot_create_report(
        name="Filtered Deals",
        data_source="deals",
        metrics=["count"],
        client=c,
        portal_id="123",
        filters=[{"field": "dealstage", "operator": "EQ", "value": "closedwon"}],
        group_by=["dealstage"],
        visualization="bar_chart",
    )
    assert result["id"] == "r-2"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_get_report(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/analytics/v2/reports/r-1").mock(
        return_value=httpx.Response(200, json={"id": "r-1", "data": [{"count": 42}]})
    )
    result = await hubspot_get_report(report_id="r-1", client=c, portal_id="123")
    assert result["id"] == "r-1"
    assert result["data"][0]["count"] == 42
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_dashboard(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/analytics/v2/dashboards").mock(
        return_value=httpx.Response(201, json={"id": "d-1", "name": "Sales Overview"})
    )
    result = await hubspot_create_dashboard(
        name="Sales Overview",
        report_ids=["r-1", "r-2"],
        client=c,
        portal_id="123",
    )
    assert result["id"] == "d-1"
    assert result["name"] == "Sales Overview"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_create_dashboard_with_layout(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/analytics/v2/dashboards").mock(
        return_value=httpx.Response(201, json={"id": "d-2"})
    )
    result = await hubspot_create_dashboard(
        name="Layout Dashboard",
        report_ids=["r-1"],
        client=c,
        portal_id="123",
        layout={"columns": 2, "rows": 1},
    )
    assert result["id"] == "d-2"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_schedule_email(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/analytics/v2/scheduled-emails").mock(
        return_value=httpx.Response(201, json={"id": "se-1", "status": "active"})
    )
    result = await hubspot_schedule_email(
        name="Weekly Deals",
        resource_id="r-1",
        resource_type="report",
        recipients=["boss@example.com"],
        frequency="weekly",
        client=c,
        portal_id="123",
        day_of_week=1,
        hour=9,
    )
    assert result["id"] == "se-1"
    assert result["status"] == "active"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_schedule_email_minimal(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.post("https://api.hubapi.com/analytics/v2/scheduled-emails").mock(
        return_value=httpx.Response(201, json={"id": "se-2"})
    )
    result = await hubspot_schedule_email(
        name="Daily Summary",
        resource_id="d-1",
        resource_type="dashboard",
        recipients=["team@example.com"],
        frequency="daily",
        client=c,
        portal_id="123",
    )
    assert result["id"] == "se-2"
    await c.close()


@pytest.mark.asyncio
async def test_hubspot_get_report_error(respx_mock):
    c = HubSpotClient(PortalConfig(portal_id="123", token="t"))
    respx_mock.get("https://api.hubapi.com/analytics/v2/reports/bad-id").mock(
        return_value=httpx.Response(404, json={"message": "Not found"})
    )
    result = await hubspot_get_report(report_id="bad-id", client=c, portal_id="123")
    assert "error" in result
    assert result["tool"] == "hubspot_get_report"
    await c.close()
