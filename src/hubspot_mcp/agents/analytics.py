from __future__ import annotations

import hubspot_mcp.tools.analytics  # noqa: F401 — registers tools
import hubspot_mcp.tools.reporting  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_preview
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_get_analytics_report",
    "hubspot_calculate_metrics",
    "hubspot_pipeline_velocity",
    "hubspot_create_report",
    "hubspot_create_dashboard",
    "hubspot_schedule_email",
]

_DOMAIN = (
    "You provide analytics and reporting for HubSpot. "
    "You retrieve reports, calculate conversion and win rates, measure pipeline velocity, "
    "create custom reports, assemble dashboards, and schedule email delivery."
)


def get_analytics_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Analytics Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("analytics")
async def _build_analytics_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    if intent.intent_type in ("search", "list", "get"):
        try:
            result = await invoke_tool(
                "hubspot_get_analytics_report",
                portal_id,
                report_id=intent.description,
                client=client,
            )
        except Exception as exc:
            return PreviewResult(
                preview={"error": str(exc)},
                impact_count=0,
                risk_level=intent.risk_level,
            )
        if "error" in result:
            return PreviewResult(
                preview={"error": result["error"]},
                impact_count=0,
                risk_level=intent.risk_level,
            )
        return PreviewResult(
            preview={"report": result},
            impact_count=1,
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={},
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on analytics"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )
