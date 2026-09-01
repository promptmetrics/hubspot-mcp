from __future__ import annotations

import hubspot_mcp.tools.analytics  # noqa: F401 — registers tools
import hubspot_mcp.tools.associations  # noqa: F401
import hubspot_mcp.tools.engagements  # noqa: F401
import hubspot_mcp.tools.hygiene  # noqa: F401
import hubspot_mcp.tools.lists  # noqa: F401
import hubspot_mcp.tools.objects  # noqa: F401
import hubspot_mcp.tools.pipelines  # noqa: F401
import hubspot_mcp.tools.properties  # noqa: F401
import hubspot_mcp.tools.users  # noqa: F401
import hubspot_mcp.tools.workflows  # noqa: F401
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.tools import get_tool

_READ_ONLY_TOOL_NAMES = [
    # Objects
    "hubspot_get_object",
    "hubspot_search_objects",
    # Properties
    "hubspot_get_property",
    "hubspot_list_properties",
    # Workflows
    "hubspot_get_workflow",
    "hubspot_list_workflows",
    # Lists
    "hubspot_get_list",
    "hubspot_list_lists",
    # Pipelines
    "hubspot_get_pipeline",
    "hubspot_list_pipelines",
    # Users
    "hubspot_get_user",
    "hubspot_list_users",
    # Engagements
    "hubspot_get_engagement",
    "hubspot_search_engagements",
    # Associations
    "hubspot_get_association_schema",
    # Analytics / hygiene (read-only)
    "hubspot_get_report",
    "hubspot_calculate_metrics",
    "hubspot_pipeline_velocity",
    "hubspot_find_duplicates",
]

_DOMAIN = (
    "You are the Verify Agent. You only read HubSpot state; you never write. "
    "You compare actual state against expected state and return a structured VerificationResult."
)


def get_verify_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _READ_ONLY_TOOL_NAMES if (t := get_tool(name)) is not None]
    instructions = (
        "\nInstructions:\n"
        "1. Use only the available read-only tools to inspect actual HubSpot state.\n"
        "2. Compare actual state to the expected state provided by the orchestrator.\n"
        "3. Return a JSON VerificationResult with:\n"
        "   - status: verified | mismatch | error | partial\n"
        "   - mismatches: list of {field, expected, actual} objects\n"
        "   - missing_fields: list of expected fields not found\n"
        "   - checked_count, verified_count\n"
        "   - message: human-readable summary\n"
        "4. If a read fails, return status error and include the error details in message.\n"
    )
    return build_agent_prompt(
        agent_name="Verify Agent",
        domain_description=_DOMAIN + instructions,
        available_tools=tools,
        portal_config=portal_config,
    )
