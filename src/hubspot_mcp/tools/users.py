from __future__ import annotations

from typing import Any
from urllib.parse import quote

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(name="hubspot_get_user", description="Retrieve a HubSpot user by ID.")
async def hubspot_get_user(
    user_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/settings/v3/users/{quote(user_id, safe='')}",
            portal_id=portal_id,
            expected_scopes=["settings.users.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_user"}


@tool(name="hubspot_list_users", description="List all HubSpot users.")
async def hubspot_list_users(
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            "/settings/v3/users",
            portal_id=portal_id,
            expected_scopes=["settings.users.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_list_users"}


@tool(name="hubspot_create_user", description="Create a new HubSpot user.")
async def hubspot_create_user(
    email: str,
    role_id: str,
    send_welcome_email: bool = True,
    client: HubSpotClient | None = None,
    portal_id: str = "",
) -> dict[str, Any]:
    if client is None:
        return {"error": "HubSpotClient not provided", "tool": "hubspot_create_user"}
    try:
        resp = await client.post(
            "/settings/v3/users",
            portal_id=portal_id,
            body={"email": email, "roleId": role_id, "sendWelcomeEmail": send_welcome_email},
            expected_scopes=["settings.users.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_user"}


@tool(name="hubspot_update_user", description="Update an existing HubSpot user.")
async def hubspot_update_user(
    user_id: str,
    updates: dict[str, Any],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.patch(
            f"/settings/v3/users/{quote(user_id, safe='')}",
            portal_id=portal_id,
            body=updates,
            expected_scopes=["settings.users.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_update_user"}


@tool(name="hubspot_deactivate_user", description="Deactivate a HubSpot user.")
async def hubspot_deactivate_user(
    user_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.delete(
            f"/settings/v3/users/{quote(user_id, safe='')}",
            portal_id=portal_id,
            expected_scopes=["settings.users.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_deactivate_user"}
