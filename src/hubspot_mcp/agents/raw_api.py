from __future__ import annotations

import hubspot_mcp.tools.raw_api  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = ["hubspot_raw_api"]

_DOMAIN = (
    "You are the power-user escape hatch for HubSpot. "
    "You make direct API calls to any uncovered HubSpot endpoint using the raw API tool. "
    "Use this only when no other specialist agent covers the required endpoint."
)


def get_raw_api_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Raw API Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("raw_api")
async def _build_raw_api_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation via raw API"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("raw_api")
async def _execute_raw_api(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    payload = proposed_payload or {}
    method = payload.get("method", "GET")
    path = payload.get("path", "")
    body = payload.get("body")
    expected_scopes = payload.get("expected_scopes")

    if not path:
        return {"status": "error", "message": "No API path specified for raw API call."}

    result = await invoke_tool(
        "hubspot_raw_api",
        portal_id,
        method=method,
        path=path,
        body=body,
        expected_scopes=expected_scopes,
        client=client,
    )
    return {"status": "success", "data": {"result": result}}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("raw_api")
async def _reconcile_raw_api(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    return {"status": "unknown", "message": "Reconciliation not implemented for raw_api"}
