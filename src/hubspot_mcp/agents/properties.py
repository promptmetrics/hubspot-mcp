from __future__ import annotations

import hubspot_mcp.tools.properties  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_get_property",
    "hubspot_list_properties",
    "hubspot_create_property",
    "hubspot_update_property",
    "hubspot_delete_property",
]

_DOMAIN = (
    "You manage custom property definitions for HubSpot object types (contacts, companies, deals, tickets). "
    "You retrieve, list, create, update, and delete properties and their field types."
)


def get_properties_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Properties Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("properties")
async def _build_properties_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    object_type = intent.target_object
    if not object_type:
        return PreviewResult(
            preview={"message": f"{intent.intent_type} operation on properties"},
            impact_count=intent.estimated_impact or 1,
            risk_level=intent.risk_level,
        )

    if intent.intent_type in ("search", "list", "get"):
        try:
            result = await invoke_tool(
                "hubspot_list_properties",
                portal_id,
                object_type=object_type,
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
            preview={"properties": records},
            impact_count=len(records),
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={},
        )

    if intent.intent_type == "create":
        return PreviewResult(
            preview={"message": f"Will create a new property on {object_type}"},
            impact_count=1,
            risk_level=intent.risk_level,
            proposed_payload={"object_type": object_type, "properties": {}},
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on {object_type} properties"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("properties")
async def _execute_properties(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    object_type = intent.target_object
    if not object_type:
        return {"status": "success", "message": f"Executed properties for: {request_text}"}

    if intent.intent_type in ("search", "list", "get"):
        result = await invoke_tool(
            "hubspot_list_properties",
            portal_id,
            object_type=object_type,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "create":
        props = proposed_payload.get("properties", {}) if proposed_payload else {}
        result = await invoke_tool(
            "hubspot_create_property",
            portal_id,
            object_type=object_type,
            name=props.get("name", "new_property"),
            label=props.get("label", "New Property"),
            property_type=props.get("type", "string"),
            field_type=props.get("fieldType", "text"),
            group_name=props.get("groupName", "contactinformation"),
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "update":
        props = proposed_payload.get("properties", {}) if proposed_payload else {}
        property_name = props.get("name") or (proposed_payload.get("property_name") if proposed_payload else None)
        if not property_name:
            return {"status": "error", "message": "No property name specified for update."}
        result = await invoke_tool(
            "hubspot_update_property",
            portal_id,
            property_name=property_name,
            object_type=object_type,
            updates=props,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "delete":
        property_name = proposed_payload.get("property_name") if proposed_payload else None
        if not property_name:
            return {"status": "error", "message": "No property name specified for delete."}
        result = await invoke_tool(
            "hubspot_delete_property",
            portal_id,
            property_name=property_name,
            object_type=object_type,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    return {"status": "success", "message": f"Executed properties for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("properties")
async def _reconcile_properties(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    object_type = intent.target_object
    if not object_type:
        return {"status": "unknown", "message": "Reconciliation not implemented for this intent"}

    property_name = expected_payload.get("name") or expected_payload.get("property_name")
    if not property_name:
        return {"status": "unknown", "message": "No property name in expected payload for reconciliation"}

    if intent.intent_type == "create":
        result = await invoke_tool(
            "hubspot_get_property",
            portal_id,
            property_name=property_name,
            object_type=object_type,
            client=client,
        )
        if "error" in result:
            return {
                "status": "discrepancy",
                "message": f"Property {property_name} not found after expected creation.",
                "expected": expected_payload,
                "actual": None,
            }
        return {
            "status": "verified",
            "message": f"Property {property_name} verified on {object_type}.",
            "expected": expected_payload,
            "actual": result,
        }

    if intent.intent_type == "update":
        result = await invoke_tool(
            "hubspot_get_property",
            portal_id,
            property_name=property_name,
            object_type=object_type,
            client=client,
        )
        if "error" in result:
            return {
                "status": "discrepancy",
                "message": f"Property {property_name} not found for update verification.",
                "expected": expected_payload,
                "actual": None,
            }
        expected_props = expected_payload.get("properties", {})
        actual_props = result.get("properties", result)
        mismatches = []
        for key, expected_val in expected_props.items():
            actual_val = actual_props.get(key)
            if actual_val != expected_val:
                mismatches.append({
                    "property": key,
                    "expected": expected_val,
                    "actual": actual_val,
                })
        if mismatches:
            return {
                "status": "discrepancy",
                "message": f"Property mismatches found on {property_name}.",
                "expected": expected_payload,
                "actual": result,
                "mismatches": mismatches,
            }
        return {
            "status": "verified",
            "message": f"Update verified on property {property_name}.",
            "expected": expected_payload,
            "actual": result,
        }

    if intent.intent_type == "delete":
        result = await invoke_tool(
            "hubspot_get_property",
            portal_id,
            property_name=property_name,
            object_type=object_type,
            client=client,
        )
        if "error" not in result:
            return {
                "status": "discrepancy",
                "message": f"Property {property_name} still exists after expected delete.",
                "expected": expected_payload,
                "actual": result,
            }
        return {
            "status": "verified",
            "message": f"Delete verified: property {property_name} no longer exists.",
            "expected": expected_payload,
            "actual": None,
        }

    return {"status": "unknown", "message": "Reconciliation not implemented for this intent"}
