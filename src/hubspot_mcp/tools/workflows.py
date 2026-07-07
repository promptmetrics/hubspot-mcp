from __future__ import annotations

from typing import Any
from urllib.parse import quote

from hubspot_mcp.blueprints.workflows import get_blueprint
from hubspot_mcp.blueprints.workflows.converter import blueprint_to_v4_payload

# Trigger blueprint self-registration
from hubspot_mcp.blueprints.workflows import (  # noqa: F401
    deal_stage_task,
    lead_scoring,
    re_anniversary_touch,
    re_buyer_appraisal_alert,
    re_buyer_criteria_match,
    re_buyer_financing_alert,
    re_buyer_inspection_alert,
    re_closing_day,
    re_engagement,
    re_hygiene_unassigned,
    re_offer_present_seller,
    re_open_house_followup,
    re_pre_listing_prep,
    re_showing_feedback,
    re_speed_to_lead,
    re_stale_buyer_deal,
    re_stale_listing,
    re_vendor_expiry,
    welcome_email,
)
from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(name="hubspot_get_workflow", description="Retrieve a HubSpot workflow by ID.")
async def hubspot_get_workflow(
    workflow_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/automation/v4/flows/{quote(workflow_id, safe='')}",
            portal_id=portal_id,
            expected_scopes=["automation"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_workflow"}


@tool(name="hubspot_list_workflows", description="List all HubSpot workflows.")
async def hubspot_list_workflows(
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            "/automation/v4/flows",
            portal_id=portal_id,
            expected_scopes=["automation"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_list_workflows"}


@tool(name="hubspot_create_workflow", description="Create a new HubSpot workflow.")
async def hubspot_create_workflow(
    name: str,
    workflow_type: str,
    actions: list[dict[str, Any]],
    enrollment: dict[str, Any],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            "/automation/v4/flows",
            portal_id=portal_id,
            body={"name": name, "type": workflow_type, "actions": actions, "enrollment": enrollment},
            expected_scopes=["automation"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_workflow"}


@tool(name="hubspot_update_workflow", description="Update an existing HubSpot workflow.")
async def hubspot_update_workflow(
    workflow_id: str,
    updates: dict[str, Any],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.patch(
            f"/automation/v4/flows/{quote(workflow_id, safe='')}",
            portal_id=portal_id,
            body=updates,
            expected_scopes=["automation"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_update_workflow"}


@tool(name="hubspot_enroll_workflow", description="Enroll records into a HubSpot workflow.")
async def hubspot_enroll_workflow(
    workflow_id: str,
    object_ids: list[str],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            f"/automation/v4/flows/{quote(workflow_id, safe='')}/enrollments",
            portal_id=portal_id,
            body={"objectIds": object_ids},
            expected_scopes=["automation"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_enroll_workflow"}


@tool(name="hubspot_toggle_workflow", description="Toggle a HubSpot workflow on or off.")
async def hubspot_toggle_workflow(
    workflow_id: str,
    enabled: bool,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            f"/automation/v4/flows/{quote(workflow_id, safe='')}/toggle",
            portal_id=portal_id,
            body={"enabled": enabled},
            expected_scopes=["automation"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_toggle_workflow"}


@tool(
    name="hubspot_create_workflow_from_blueprint",
    description="Create a new HubSpot workflow from a blueprint template.",
)
async def hubspot_create_workflow_from_blueprint(
    blueprint_name: str,
    client: HubSpotClient,
    portal_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        blueprint = get_blueprint(blueprint_name)
        if blueprint is None:
            available = [b.name for b in __import__(
                "hubspot_mcp.blueprints.workflows", fromlist=["list_blueprints"]
            ).list_blueprints()]
            return {
                "error": f"Blueprint '{blueprint_name}' not found.",
                "available_blueprints": available,
            }

        merged_params = params or {}
        # Merge parameter defaults from blueprint schema
        for key, info in blueprint.parameter_schema.items():
            if key not in merged_params:
                default = info.get("default")
                if default is not None:
                    merged_params[key] = default

        if blueprint.build is None:
            return {
                "error": "Blueprint build function is missing.",
                "tool": "hubspot_create_workflow_from_blueprint",
            }
        spec = blueprint.build(merged_params)
        if not spec.get("name"):
            spec["name"] = merged_params.get("name") or blueprint_name

        payload = blueprint_to_v4_payload(spec)

        resp = await client.post(
            "/automation/v4/flows",
            portal_id=portal_id,
            body=payload,
            expected_scopes=["automation"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_workflow_from_blueprint"}
    except ValueError as exc:
        return {
            "error": str(exc),
            "tool": "hubspot_create_workflow_from_blueprint",
            "hint": (
                "Some blueprints cannot be auto-created due to unsupported V4 API features: "
                "property-relative task due dates (e.g. '{{deadline - 5d}}'), "
                "unknown custom-object event triggers, placeholder team IDs, missing marketing email content_id, "
                "or missing custom properties/deal stages in the target portal. "
                "Build those workflows manually in the HubSpot UI."
            ),
        }
