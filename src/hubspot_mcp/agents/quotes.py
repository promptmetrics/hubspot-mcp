from __future__ import annotations

import hubspot_mcp.tools.objects  # noqa: F401 — registers tools
from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.dispatch import register_execute, register_preview, register_reconcile
from hubspot_mcp.models import PreviewResult, TaskIntent
from hubspot_mcp.tools import get_tool, invoke_tool

_TOOL_NAMES = [
    "hubspot_get_object",
    "hubspot_search_objects",
    "hubspot_create_object",
    "hubspot_update_object",
    "hubspot_delete_object",
    "hubspot_batch_upsert_objects",
]

_DOMAIN = (
    "You manage HubSpot commerce quotes. "
    "You retrieve, search, create, update, and delete quote records. "
    "Quotes can be linked to deals, fees, discounts, and taxes."
)

_DEFAULT_OBJECT_TYPE = "quotes"

_STOP_WORDS = {
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "with", "and", "or",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "must", "shall", "can", "need", "dare", "ought", "used",
    "find", "search", "get", "show", "list", "create", "add", "new", "update",
    "change", "edit", "modify", "delete", "remove", "quote", "quotes",
}


def _extract_search_term(intent: TaskIntent) -> str:
    words = [
        w.strip(".,;:!?")
        for w in intent.description.lower().split()
        if w.strip(".,;:!?") not in _STOP_WORDS and len(w.strip(".,;:!?")) > 2
    ]
    return " ".join(words[:3]) if words else "*"


def get_quotes_agent_prompt(portal_config: PortalConfig | None = None) -> AgentPrompt:
    tools = [t for name in _TOOL_NAMES if (t := get_tool(name)) is not None]
    return build_agent_prompt(
        agent_name="Quotes Agent",
        domain_description=_DOMAIN,
        available_tools=tools,
        portal_config=portal_config,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@register_preview("quotes")
async def _build_quotes_preview(
    agent_name: str,
    intent: TaskIntent,
    client,
    portal_id: str,
) -> PreviewResult:
    object_type = intent.target_object or _DEFAULT_OBJECT_TYPE

    if not intent.target_object:
        return PreviewResult(
            preview={"message": f"{intent.intent_type} operation on {object_type}"},
            impact_count=intent.estimated_impact or 1,
            risk_level=intent.risk_level,
        )

    if intent.intent_type in ("search", "update", "delete"):
        search_term = _extract_search_term(intent)
        try:
            result = await invoke_tool(
                "hubspot_search_objects",
                portal_id,
                object_type=object_type,
                query={"query": search_term, "limit": 10},
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
            preview={"records": records},
            impact_count=len(records),
            risk_level=intent.risk_level,
            proposed_payload={},
            original_values={r.get("id"): r.get("properties", {}) for r in records},
        )

    if intent.intent_type == "create":
        return PreviewResult(
            preview={"message": f"Will create a new {object_type} record"},
            impact_count=1,
            risk_level=intent.risk_level,
            proposed_payload={"object_type": object_type, "properties": {}},
        )

    return PreviewResult(
        preview={"message": f"{intent.intent_type} operation on {object_type}"},
        impact_count=intent.estimated_impact or 1,
        risk_level=intent.risk_level,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@register_execute("quotes")
async def _execute_quotes(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    proposed_payload: dict | None,
) -> dict:
    object_type = intent.target_object or _DEFAULT_OBJECT_TYPE

    if not intent.target_object:
        return {"status": "success", "message": f"Executed {object_type} for: {request_text}"}

    if intent.intent_type == "search":
        search_term = _extract_search_term(intent)
        result = await invoke_tool(
            "hubspot_search_objects",
            portal_id,
            object_type=object_type,
            query={"query": search_term, "limit": 10},
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "create":
        props = proposed_payload.get("properties", {}) if proposed_payload else {}
        result = await invoke_tool(
            "hubspot_create_object",
            portal_id,
            object_type=object_type,
            properties=props,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "update":
        search_term = _extract_search_term(intent)
        search_result = await invoke_tool(
            "hubspot_search_objects",
            portal_id,
            object_type=object_type,
            query={"query": search_term, "limit": 10},
            client=client,
        )
        records = search_result.get("results", [])
        if not records:
            return {"status": "error", "message": "No matching records found to update."}
        object_id = records[0].get("id")
        props = proposed_payload.get("properties", {}) if proposed_payload else {}
        result = await invoke_tool(
            "hubspot_update_object",
            portal_id,
            object_id=object_id,
            object_type=object_type,
            properties=props,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    if intent.intent_type == "delete":
        search_term = _extract_search_term(intent)
        search_result = await invoke_tool(
            "hubspot_search_objects",
            portal_id,
            object_type=object_type,
            query={"query": search_term, "limit": 10},
            client=client,
        )
        records = search_result.get("results", [])
        if not records:
            return {"status": "error", "message": "No matching records found to delete."}
        object_id = records[0].get("id")
        result = await invoke_tool(
            "hubspot_delete_object",
            portal_id,
            object_id=object_id,
            object_type=object_type,
            client=client,
        )
        return {"status": "success", "data": {"result": result}}

    return {"status": "success", "message": f"Executed {object_type} for: {request_text}"}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@register_reconcile("quotes")
async def _reconcile_quotes(
    agent_name: str,
    intent: TaskIntent,
    request_text: str,
    client,
    portal_id: str,
    expected_payload: dict,
) -> dict:
    object_type = intent.target_object or _DEFAULT_OBJECT_TYPE

    if not intent.target_object:
        return {"status": "unknown", "message": "Reconciliation not implemented for this intent"}

    search_term = _extract_search_term(intent)

    if intent.intent_type == "create":
        result = await invoke_tool(
            "hubspot_search_objects",
            portal_id,
            object_type=object_type,
            query={"query": search_term, "limit": 10},
            client=client,
        )
        records = result.get("results", [])
        if not records:
            return {
                "status": "discrepancy",
                "message": f"No {object_type} record found matching expected creation.",
                "expected": expected_payload,
                "actual": None,
            }
        return {
            "status": "verified",
            "message": f"Found {len(records)} potential matches for created {object_type}.",
            "expected": expected_payload,
            "actual": records[:3],
        }

    if intent.intent_type == "update":
        result = await invoke_tool(
            "hubspot_search_objects",
            portal_id,
            object_type=object_type,
            query={"query": search_term, "limit": 10},
            client=client,
        )
        records = result.get("results", [])
        if not records:
            return {
                "status": "discrepancy",
                "message": f"No {object_type} record found for update verification.",
                "expected": expected_payload,
                "actual": None,
            }
        expected_props = expected_payload.get("properties", {})
        mismatches = []
        for record in records[:3]:
            props = record.get("properties", {})
            for key, expected_val in expected_props.items():
                actual_val = props.get(key)
                if actual_val != expected_val:
                    mismatches.append({
                        "record_id": record.get("id"),
                        "property": key,
                        "expected": expected_val,
                        "actual": actual_val,
                    })
        if mismatches:
            return {
                "status": "discrepancy",
                "message": f"Property mismatches found on {len(mismatches)} record(s).",
                "expected": expected_payload,
                "actual": records[:3],
                "mismatches": mismatches,
            }
        return {
            "status": "verified",
            "message": f"Update verified on {len(records)} matching {object_type} record(s).",
            "expected": expected_payload,
            "actual": records[:3],
        }

    if intent.intent_type == "delete":
        result = await invoke_tool(
            "hubspot_search_objects",
            portal_id,
            object_type=object_type,
            query={"query": search_term, "limit": 10},
            client=client,
        )
        records = result.get("results", [])
        if records:
            return {
                "status": "discrepancy",
                "message": f"{len(records)} {object_type} record(s) still exist after expected delete.",
                "expected": expected_payload,
                "actual": records[:3],
            }
        return {
            "status": "verified",
            "message": f"Delete verified: no matching {object_type} records remain.",
            "expected": expected_payload,
            "actual": None,
        }

    return {"status": "unknown", "message": "Reconciliation not implemented for this intent"}
