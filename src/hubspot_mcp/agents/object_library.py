from __future__ import annotations

from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_preview
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool

_TOOL_NAMES: list[str] = []

_DOMAIN = (
    "You provide information about the HubSpot object library. "
    "You describe which standard and custom objects are available in the portal. "
    "This agent is read-only — it cannot enable or disable objects."
)


def get_object_library_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Object Library Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("object_library")
async def _build_object_library_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    return PreviewResult(
        preview={"message": f"Object library info for: {intent.description}"},
        impact_count=0,
        risk_level=intent.risk_level,
        proposed_payload={},
        original_values={},
    )
