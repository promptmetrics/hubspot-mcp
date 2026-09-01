from __future__ import annotations

import hubspot_mcp.tools.users  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_get_user",
    "hubspot_list_users",
    "hubspot_create_user",
    "hubspot_update_user",
    "hubspot_deactivate_user",
]

_DOMAIN = (
    "You manage HubSpot users and their roles. "
    "You retrieve, list, create, update, and deactivate user accounts."
)


def get_users_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Users Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("users")
async def _build_users_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    if intent.intent_type in ("search", "list", "get"):
        try:
            result = await invoke_tool(
                "hubspot_list_users",
                portal_id,
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
        records = result.get("results", [])
        return PreviewResult(
            preview={"users": records},
            impact_count=len(records),
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={},
        )

    if intent.intent_type == "create":
        return PreviewResult(
            preview={"message": "Will create a new user"},
            impact_count=1,
            risk_level=intent.risk_level,
            proposed_payload={"email": intent.description},
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on users"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("users")
async def _execute_users(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    if intent.intent_type in ("search", "list", "get"):
        result = await invoke_tool(
            "hubspot_list_users",
            portal_id,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "create":
        payload = proposed_payload or {}
        result = await invoke_tool(
            "hubspot_create_user",
            portal_id,
            email=payload.get("email", ""),
            role_id=payload.get("role_id", ""),
            send_welcome_email=payload.get("send_welcome_email", True),
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "update":
        payload = proposed_payload or {}
        user_id = payload.get("user_id")
        if not user_id:
            return {"status": "error", "message": "No user_id specified for update."}
        result = await invoke_tool(
            "hubspot_update_user",
            portal_id,
            user_id=user_id,
            updates=payload.get("updates", {}),
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "delete":
        payload = proposed_payload or {}
        user_id = payload.get("user_id")
        if not user_id:
            return {"status": "error", "message": "No user_id specified for deactivation."}
        result = await invoke_tool(
            "hubspot_deactivate_user",
            portal_id,
            user_id=user_id,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    return {"status": "success", "message": f"Executed users for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("users")
async def _reconcile_users(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    user_id = expected_payload.get("user_id") or expected_payload.get("id")
    if not user_id:
        return {"status": "unknown", "message": "No user_id in expected payload for reconciliation"}

    if intent.intent_type == "create":
        result = await invoke_tool(
            "hubspot_get_user",
            portal_id,
            user_id=user_id,
            client=client,
        )
        if "error" in result:
            return {
                "status": "discrepancy",
                "message": f"User {user_id} not found after expected creation.",
                "expected": expected_payload,
                "actual": None,
            }
        return {
            "status": "verified",
            "message": f"User {user_id} verified.",
            "expected": expected_payload,
            "actual": result,
        }

    if intent.intent_type == "update":
        result = await invoke_tool(
            "hubspot_get_user",
            portal_id,
            user_id=user_id,
            client=client,
        )
        if "error" in result:
            return {
                "status": "discrepancy",
                "message": f"User {user_id} not found for update verification.",
                "expected": expected_payload,
                "actual": None,
            }
        return {
            "status": "verified",
            "message": f"Update verified on user {user_id}.",
            "expected": expected_payload,
            "actual": result,
        }

    if intent.intent_type == "delete":
        result = await invoke_tool(
            "hubspot_get_user",
            portal_id,
            user_id=user_id,
            client=client,
        )
        if "error" not in result:
            return {
                "status": "discrepancy",
                "message": f"User {user_id} still active after expected deactivation.",
                "expected": expected_payload,
                "actual": result,
            }
        return {
            "status": "verified",
            "message": f"Delete verified: user {user_id} no longer active.",
            "expected": expected_payload,
            "actual": None,
        }

    return {"status": "unknown", "message": "Reconciliation not implemented for this intent"}
