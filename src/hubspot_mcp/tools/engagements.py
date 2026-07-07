from __future__ import annotations

from typing import Any
from urllib.parse import quote

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(name="hubspot_get_engagement", description="Retrieve a HubSpot engagement by ID.")
async def hubspot_get_engagement(
    engagement_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/crm/v3/objects/engagements/{quote(engagement_id, safe='')}",
            portal_id=portal_id,
            expected_scopes=[
                "crm.objects.notes.read",
                "crm.objects.calls.read",
                "crm.objects.appointments.read",
                "crm.objects.tasks.read",
                "crm.objects.emails.read",
                "sales-email-read",
            ],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_engagement"}


@tool(name="hubspot_search_engagements", description="Search HubSpot engagements using filter groups.")
async def hubspot_search_engagements(
    query: dict[str, Any],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            "/crm/v3/objects/engagements/search",
            portal_id=portal_id,
            body=query,
            expected_scopes=[
                "crm.objects.notes.read",
                "crm.objects.calls.read",
                "crm.objects.appointments.read",
                "crm.objects.tasks.read",
                "crm.objects.emails.read",
                "sales-email-read",
            ],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_search_engagements"}


@tool(name="hubspot_create_note", description="Create a note engagement in HubSpot.")
async def hubspot_create_note(
    body: str,
    associations: list[dict[str, Any]] | None = None,
    client: HubSpotClient | None = None,
    portal_id: str = "",
) -> dict[str, Any]:
    if client is None:
        return {"error": "HubSpotClient not provided", "tool": "hubspot_create_note"}
    try:
        properties = {"hs_engagement_type": "NOTE", "hs_note_body": body}
        payload: dict[str, Any] = {"properties": properties}
        if associations:
            payload["associations"] = associations
        resp = await client.post(
            "/crm/v3/objects/engagements",
            portal_id=portal_id,
            body=payload,
            expected_scopes=["crm.objects.notes.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_note"}


@tool(name="hubspot_create_task", description="Create a task engagement in HubSpot.")
async def hubspot_create_task(
    subject: str,
    status: str,
    timestamp: str,
    associations: list[dict[str, Any]] | None = None,
    client: HubSpotClient | None = None,
    portal_id: str = "",
) -> dict[str, Any]:
    if client is None:
        return {"error": "HubSpotClient not provided", "tool": "hubspot_create_task"}
    try:
        properties = {
            "hs_engagement_type": "TASK",
            "hs_task_subject": subject,
            "hs_task_status": status,
            "hs_timestamp": timestamp,
        }
        payload: dict[str, Any] = {"properties": properties}
        if associations:
            payload["associations"] = associations
        resp = await client.post(
            "/crm/v3/objects/engagements",
            portal_id=portal_id,
            body=payload,
            expected_scopes=["crm.objects.tasks.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_task"}


@tool(name="hubspot_create_email", description="Create an email engagement in HubSpot.")
async def hubspot_create_email(
    subject: str,
    body: str,
    associations: list[dict[str, Any]] | None = None,
    client: HubSpotClient | None = None,
    portal_id: str = "",
) -> dict[str, Any]:
    if client is None:
        return {"error": "HubSpotClient not provided", "tool": "hubspot_create_email"}
    try:
        properties = {"hs_engagement_type": "EMAIL", "hs_email_subject": subject, "hs_email_body": body}
        payload: dict[str, Any] = {"properties": properties}
        if associations:
            payload["associations"] = associations
        resp = await client.post(
            "/crm/v3/objects/engagements",
            portal_id=portal_id,
            body=payload,
            expected_scopes=["crm.objects.emails.write", "sales-email-read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_email"}


@tool(name="hubspot_create_meeting", description="Create a meeting engagement in HubSpot.")
async def hubspot_create_meeting(
    title: str,
    start_time: str,
    associations: list[dict[str, Any]] | None = None,
    client: HubSpotClient | None = None,
    portal_id: str = "",
) -> dict[str, Any]:
    if client is None:
        return {"error": "HubSpotClient not provided", "tool": "hubspot_create_meeting"}
    try:
        properties = {"hs_engagement_type": "MEETING", "hs_meeting_title": title, "hs_meeting_start_time": start_time}
        payload: dict[str, Any] = {"properties": properties}
        if associations:
            payload["associations"] = associations
        resp = await client.post(
            "/crm/v3/objects/engagements",
            portal_id=portal_id,
            body=payload,
            expected_scopes=["crm.objects.appointments.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_meeting"}


@tool(name="hubspot_create_call", description="Create a call engagement in HubSpot.")
async def hubspot_create_call(
    title: str,
    duration_ms: int,
    associations: list[dict[str, Any]] | None = None,
    client: HubSpotClient | None = None,
    portal_id: str = "",
) -> dict[str, Any]:
    if client is None:
        return {"error": "HubSpotClient not provided", "tool": "hubspot_create_call"}
    try:
        properties = {"hs_engagement_type": "CALL", "hs_call_title": title, "hs_call_duration": duration_ms}
        payload: dict[str, Any] = {"properties": properties}
        if associations:
            payload["associations"] = associations
        resp = await client.post(
            "/crm/v3/objects/engagements",
            portal_id=portal_id,
            body=payload,
            expected_scopes=["crm.objects.calls.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_call"}
