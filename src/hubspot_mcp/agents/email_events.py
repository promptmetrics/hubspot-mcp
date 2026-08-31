from __future__ import annotations

from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_preview
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool

_TOOL_NAMES: list[str] = []

_DOMAIN = (
    "You provide HubSpot email event analytics. "
    "You retrieve email events and campaign performance data. "
    "This agent is read-only and uses legacy email events APIs."
)


def get_email_events_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Email Events Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("email_events")
async def _build_email_events_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    return PreviewResult(
        preview={"message": f"Email events query for portal {portal_id}: {intent.description}"},
        impact_count=0,
        risk_level=intent.risk_level,
        proposed_payload={},
        original_values={},
    )
