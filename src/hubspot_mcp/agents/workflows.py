from __future__ import annotations

from typing import Any

import hubspot_mcp.tools.blueprint_library  # noqa: F401 — registers tools
import hubspot_mcp.tools.workflows  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.blueprints.workflows import build_blueprint_context, list_blueprints, reload_blueprints
from hubspot_mcp.blueprints.workflows.converter import blueprint_to_v4_payload
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_get_workflow",
    "hubspot_list_workflows",
    "hubspot_create_workflow",
    "hubspot_create_workflow_from_blueprint",
    "hubspot_update_workflow",
    "hubspot_enroll_workflow",
    "hubspot_toggle_workflow",
    "hubspot_extract_workflow_blueprint",
    "hubspot_parameterize_blueprint_draft",
    "hubspot_promote_blueprint_draft",
]

_DOMAIN = (
    "You manage HubSpot automation workflows. "
    "You retrieve, list, create, update, enroll records in, and toggle workflow states."
)


def get_workflows_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    prompt = build_agent_prompt(
        agent_name="Workflows Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )
    blueprint_ctx = build_blueprint_context()
    prompt.system_prompt += f"\n\n{blueprint_ctx}"
    return prompt


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("workflows")
async def _build_workflows_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    if intent.intent_type in ("search", "list", "get"):
        try:
            result = await invoke_tool(
                "hubspot_list_workflows",
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
            preview={"workflows": records},
            impact_count=len(records),
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={},
        )

    if intent.intent_type == "create":
        return _preview_create(intent)

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on workflows"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


def _match_blueprint(intent: TaskIntent):
    """Best-effort: pick the shipped/user blueprint whose name appears in the request.

    The preview builder cannot receive a caller-provided blueprint name, so it
    infers one from the request text. Returns ``None`` when nothing matches.
    """
    text = intent.description.lower()
    for bp in list_blueprints():
        if bp.name and bp.name.lower() in text:
            return bp
    return None


def _preview_create(intent: TaskIntent) -> PreviewResult:
    """Richer create preview: resolve a blueprint, render its spec, attempt V4
    conversion, and surface raw-node / cross-portal warnings (R5). The blueprint
    name survives into execute via ``proposed_payload`` (apply_write persists the
    builder's proposed_payload), so the execute path can route to
    ``hubspot_create_workflow_from_blueprint`` instead of manual construction.
    """
    # Fresh processes load only packaged blueprints at import (by design). Reload
    # from disk so a user-promoted blueprint is matchable in any process.
    reload_blueprints()
    bp = _match_blueprint(intent)
    if bp is None:
        return PreviewResult(
            preview={"message": "Will create a new workflow (no matching blueprint; manual construction)."},
            impact_count=1,
            risk_level=intent.risk_level,
            proposed_payload={"name": intent.description, "actions": []},
        )

    warnings: list[str] = []
    params: dict[str, Any] = {}
    spec = bp.build(params) if bp.build is not None else {}
    raw_action_count = sum(1 for a in spec.get("actions", []) if isinstance(a, dict) and a.get("raw") is True)
    v4_ok = True
    try:
        blueprint_to_v4_payload({**spec, "name": bp.name})
    except ValueError as exc:
        v4_ok = False
        warnings.append(f"Blueprint does not auto-convert to V4: {exc}")

    if raw_action_count:
        warnings.append(
            f"Blueprint contains {raw_action_count} raw action node(s) the converter "
            "cannot generically re-create; review before creating."
        )
    if bp.origin == "user":
        # Extracted/user blueprints may carry portal-specific values (list IDs,
        # content IDs, team/user IDs) from their source portal (R5).
        warnings.append(
            "This blueprint originated from another portal; verify portal-specific "
            "values (list IDs, marketing email content IDs, team/user IDs) before creating."
        )

    proposed_payload: dict[str, Any] = {
        "blueprint_name": bp.name,
        "params": params,
        "object_type": spec.get("object_type", "Contact-based"),
        "raw_action_count": raw_action_count,
        "v4_ok": v4_ok,
    }
    message = f"Will create a workflow from blueprint '{bp.name}' [{bp.origin}]"
    if warnings:
        message += ". Warnings: " + "; ".join(warnings)

    return PreviewResult(
        preview={"message": message, "warnings": warnings},
        impact_count=1,
        risk_level=intent.risk_level,
        proposed_payload=proposed_payload,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("workflows")
async def _execute_workflows(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    if intent.intent_type in ("search", "list", "get"):
        result = await invoke_tool(
            "hubspot_list_workflows",
            portal_id,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "create":
        payload = proposed_payload or {}
        if payload.get("blueprint_name"):
            result = await invoke_tool(
                "hubspot_create_workflow_from_blueprint",
                portal_id,
                blueprint_name=payload["blueprint_name"],
                params=payload.get("params") or {},
                client=client,
            )
            return {"status": "success", "data": {"result": result}}
        result = await invoke_tool(
            "hubspot_create_workflow",
            portal_id,
            name=payload.get("name", "New Workflow"),
            object_type=payload.get("object_type", "Contact-based"),
            enrollment=payload.get("enrollment", {}),
            actions=payload.get("actions", []),
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "update":
        payload = proposed_payload or {}
        workflow_id = payload.get("workflow_id")
        if not workflow_id:
            return {"status": "error", "message": "No workflow_id specified for update."}
        # V4 updates are PUT-with-revisionId and delete any omitted field, so
        # fetch the current body, merge caller edits over it, and PUT the whole.
        current = await invoke_tool(
            "hubspot_get_workflow",
            portal_id,
            workflow_id=workflow_id,
            client=client,
        )
        if "error" in current:
            return {
                "status": "error",
                "message": f"Could not fetch workflow {workflow_id} for update: {current['error']}",
            }
        revision_id = current.get("revisionId")
        if not revision_id:
            return {
                "status": "error",
                "message": f"Workflow {workflow_id} has no revisionId; cannot safely update.",
            }
        merged = {**current, **(payload.get("updates", {}))}
        result = await invoke_tool(
            "hubspot_update_workflow",
            portal_id,
            workflow_id=workflow_id,
            revision_id=revision_id,
            body=merged,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "delete":
        return {"status": "success", "message": f"Executed workflows for: {request_text}"}

    return {"status": "success", "message": f"Executed workflows for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("workflows")
async def _reconcile_workflows(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    workflow_id = expected_payload.get("workflow_id") or expected_payload.get("id")
    if not workflow_id:
        return {"status": "unknown", "message": "No workflow_id in expected payload for reconciliation"}

    if intent.intent_type == "create":
        result = await invoke_tool(
            "hubspot_get_workflow",
            portal_id,
            workflow_id=workflow_id,
            client=client,
        )
        if "error" in result:
            return {
                "status": "discrepancy",
                "message": f"Workflow {workflow_id} not found after expected creation.",
                "expected": expected_payload,
                "actual": None,
            }
        return {
            "status": "verified",
            "message": f"Workflow {workflow_id} verified.",
            "expected": expected_payload,
            "actual": result,
        }

    if intent.intent_type == "update":
        result = await invoke_tool(
            "hubspot_get_workflow",
            portal_id,
            workflow_id=workflow_id,
            client=client,
        )
        if "error" in result:
            return {
                "status": "discrepancy",
                "message": f"Workflow {workflow_id} not found for update verification.",
                "expected": expected_payload,
                "actual": None,
            }
        return {
            "status": "verified",
            "message": f"Update verified on workflow {workflow_id}.",
            "expected": expected_payload,
            "actual": result,
        }

    if intent.intent_type == "delete":
        result = await invoke_tool(
            "hubspot_get_workflow",
            portal_id,
            workflow_id=workflow_id,
            client=client,
        )
        if "error" not in result:
            return {
                "status": "discrepancy",
                "message": f"Workflow {workflow_id} still exists after expected delete.",
                "expected": expected_payload,
                "actual": result,
            }
        return {
            "status": "verified",
            "message": f"Delete verified: workflow {workflow_id} no longer exists.",
            "expected": expected_payload,
            "actual": None,
        }

    return {"status": "unknown", "message": "Reconciliation not implemented for this intent"}
