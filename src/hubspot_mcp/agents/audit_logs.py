from __future__ import annotations

from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_preview
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool

_TOOL_NAMES: list[str] = []

_DOMAIN = (
    "You provide HubSpot audit logs. "
    "You retrieve activity, login, and security audit events. "
    "This agent is read-only and requires an Enterprise subscription."
)


def get_audit_logs_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Audit Logs Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("audit_logs")
async def _build_audit_logs_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    return PreviewResult(
        preview={"message": f"Audit logs query for portal {portal_id}: {intent.description}"},
        impact_count=0,
        risk_level=intent.risk_level,
        proposed_payload={},
        original_values={},
    )
