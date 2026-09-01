from __future__ import annotations

from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig

_TOOL_NAMES: list[str] = []

_DOMAIN = (
    "You are the Triage Agent. You do not execute HubSpot API calls yourself. "
    "You understand the user's request, clarify ambiguous goals, and emit a structured "
    "execution plan (LoopPlan) that specialist agents will execute sequentially."
)


def _capability_matrix_text(portal_config: PortalConfig | None) -> str:
    if portal_config is None:
        return ""
    return (
        f"\nCapability matrix for portal {portal_config.portal_id}:\n"
        f"- Objects: yes\n"
        f"- Properties: yes\n"
        f"- Workflows: {'yes' if portal_config.tier in {'Professional', 'Enterprise'} else 'no'}\n"
        f"- Lists: yes\n"
        f"- Pipelines: yes\n"
        f"- Users: yes\n"
        f"- Hygiene: yes\n"
        f"- Analytics: yes\n"
        f"- Associations: yes\n"
        f"- Engagements: yes\n"
        f"- RawAPI: yes\n"
    )


def _triage_instructions() -> str:
    return (
        "\nInstructions:\n"
        "1. Parse the user's request into a single, concrete, measurable goal.\n"
        "2. If the request is ambiguous or missing required context, ask clarifying questions "
        "and do not emit a plan.\n"
        "3. Otherwise, return a JSON LoopPlan with:\n"
        "   - goal: one-sentence summary\n"
        "   - success_criteria: list of verifiable outcomes\n"
        "   - steps: ordered PlanStep objects with step_number, agent, action, description, "
        "expected_artifact_keys, prerequisites, risk_level\n"
        "   - overall_risk: low | medium | high | destructive\n"
        "   - max_iterations: default 3\n"
        "   - artifact_schema: map of expected artifact key to type/description\n"
        "4. Respect the capability matrix: do not plan workflow steps if the portal lacks workflows.\n"
        "5. Mark dependencies explicitly via prerequisites (e.g. ['1'] means step 2 depends on step 1).\n"
        "6. Prefer sequential, dependency-respecting plans over parallel execution.\n"
        "7. Return only the JSON plan (or clarifying questions), with no extra commentary.\n"
    )


def get_triage_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    return build_agent_prompt(
        agent_name="Triage Agent",
        domain_description=_DOMAIN + _capability_matrix_text(portal_config) + _triage_instructions(),
        available_tools=[],
        portal_config=portal_config,
    )
