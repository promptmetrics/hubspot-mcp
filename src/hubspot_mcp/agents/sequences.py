from __future__ import annotations

from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool

_TOOL_NAMES: list[str] = []

_DOMAIN = (
    "You manage HubSpot sequences. "
    "You retrieve sequence details and enroll contacts into sequences. "
    "Writes are limited to contact enrollment only."
)


def get_sequences_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Sequences Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("sequences")
async def _build_sequences_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    if intent.intent_type in ("search", "list", "get"):
        return PreviewResult(
            preview={"message": f"Sequence lookup for: {intent.description}"},
            impact_count=1,
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={},
        )

    if intent.intent_type == "create":
        return PreviewResult(
            preview={"message": f"Will enroll contact(s) into sequence: {intent.description}"},
            impact_count=intent.estimated_impact or 1,
            risk_level=intent.risk_level,
            proposed_payload={"sequence_id": None, "contact_ids": []},
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on sequences"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("sequences")
async def _execute_sequences(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    if intent.intent_type in ("search", "list", "get"):
        return {"status": "success", "message": f"Sequence lookup for: {request_text}"}

    if intent.intent_type == "create":
        return {"status": "success", "message": f"Contact enrollment into sequence for: {request_text}"}

    return {"status": "success", "message": f"Executed sequences for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("sequences")
async def _reconcile_sequences(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    return {"status": "unknown", "message": "Sequence reconciliation not yet implemented"}
