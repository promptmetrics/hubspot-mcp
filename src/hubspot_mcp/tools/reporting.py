from __future__ import annotations

from typing import Any

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(name="hubspot_create_report", description="Create a custom report in HubSpot.")
async def hubspot_create_report(
    name: str,
    data_source: str,
    metrics: list[str],
    client: HubSpotClient,
    portal_id: str,
    filters: list[dict[str, Any]] | None = None,
    group_by: list[str] | None = None,
    visualization: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "dataSource": data_source,
        "metrics": metrics,
    }
    if filters is not None:
        body["filters"] = filters
    if group_by is not None:
        body["groupBy"] = group_by
    if visualization is not None:
        body["visualization"] = visualization
    try:
        resp = await client.post(
            "/analytics/v2/reports",
            portal_id=portal_id,
            body=body,
            expected_scopes=["reports.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_report"}


@tool(name="hubspot_get_report", description="Retrieve report data from HubSpot by report ID.")
async def hubspot_get_report(
    report_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/analytics/v2/reports/{report_id}",
            portal_id=portal_id,
            expected_scopes=["reports.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_report"}


@tool(name="hubspot_create_dashboard", description="Assemble multiple reports into a HubSpot dashboard.")
async def hubspot_create_dashboard(
    name: str,
    report_ids: list[str],
    client: HubSpotClient,
    portal_id: str,
    layout: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "reportIds": report_ids,
    }
    if layout is not None:
        body["layout"] = layout
    try:
        resp = await client.post(
            "/analytics/v2/dashboards",
            portal_id=portal_id,
            body=body,
            expected_scopes=["reports.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_dashboard"}


@tool(name="hubspot_schedule_email", description="Schedule email delivery of a report or dashboard.")
async def hubspot_schedule_email(
    name: str,
    resource_id: str,
    resource_type: str,
    recipients: list[str],
    frequency: str,
    client: HubSpotClient,
    portal_id: str,
    day_of_week: int | None = None,
    hour: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "resourceId": resource_id,
        "resourceType": resource_type,
        "recipients": recipients,
        "frequency": frequency,
    }
    if day_of_week is not None:
        body["dayOfWeek"] = day_of_week
    if hour is not None:
        body["hour"] = hour
    try:
        resp = await client.post(
            "/analytics/v2/scheduled-emails",
            portal_id=portal_id,
            body=body,
            expected_scopes=["reports.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_schedule_email"}
