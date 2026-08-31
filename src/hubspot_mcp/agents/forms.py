from __future__ import annotations

import hubspot_mcp.tools.forms  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_list_forms",
    "hubspot_get_form",
    "hubspot_create_form",
]

_DOMAIN = (
    "You manage HubSpot marketing forms. "
    "You retrieve, list, and create forms. Updates are not supported via the v4 API."
)


def get_forms_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    prompt = build_agent_prompt(
        agent_name="Forms Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )
    return prompt


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("forms")
async def _build_forms_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    if intent.intent_type in ("search", "list", "get"):
        try:
            result = await invoke_tool(
                "hubspot_list_forms",
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
            preview={"forms": records},
            impact_count=len(records),
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={},
        )

    if intent.intent_type == "create":
        return PreviewResult(
            preview={"message": "Will create a new form"},
            impact_count=1,
            risk_level=intent.risk_level,
            proposed_payload={"name": intent.description, "fields": []},
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on forms"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("forms")
async def _execute_forms(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    if intent.intent_type in ("search", "list", "get"):
        result = await invoke_tool(
            "hubspot_list_forms",
            portal_id,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "create":
        payload = proposed_payload or {}
        result = await invoke_tool(
            "hubspot_create_form",
            portal_id,
            name=payload.get("name", "New Form"),
            form_type=payload.get("form_type", "HUBSPOT"),
            fields=payload.get("fields", []),
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "update":
        return {
            "status": "error",
            "message": "Form updates are not supported via the v4 API.",
        }

    if intent.intent_type == "delete":
        return {"status": "success", "message": f"Executed forms delete for: {request_text}"}

    return {"status": "success", "message": f"Executed forms for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("forms")
async def _reconcile_forms(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    if intent.intent_type == "create":
        name = expected_payload.get("name")
        if not name:
            return {"status": "unknown", "message": "No form name in expected payload for reconciliation"}

        result = await invoke_tool(
            "hubspot_list_forms",
            portal_id,
            client=client,
        )
        if "error" in result:
            return {
                "status": "discrepancy",
                "message": f"Unable to list forms for reconciliation: {result['error']}",
                "expected": expected_payload,
                "actual": None,
            }

        records = result.get("results", [])
        match = next((r for r in records if r.get("name") == name), None)
        if not match:
            return {
                "status": "discrepancy",
                "message": f"Form '{name}' not found after expected creation.",
                "expected": expected_payload,
                "actual": None,
            }
        return {
            "status": "verified",
            "message": f"Form '{name}' verified after creation.",
            "expected": expected_payload,
            "actual": match,
        }

    if intent.intent_type == "delete":
        form_id = expected_payload.get("form_id") or expected_payload.get("id")
        if not form_id:
            return {"status": "unknown", "message": "No form_id in expected payload for reconciliation"}

        result = await invoke_tool(
            "hubspot_get_form",
            portal_id,
            form_id=form_id,
            client=client,
        )
        if "error" not in result:
            return {
                "status": "discrepancy",
                "message": f"Form {form_id} still exists after expected delete.",
                "expected": expected_payload,
                "actual": result,
            }
        return {
            "status": "verified",
            "message": f"Delete verified: form {form_id} no longer exists.",
            "expected": expected_payload,
            "actual": None,
        }

    return {"status": "unknown", "message": "Reconciliation not implemented for this intent"}
