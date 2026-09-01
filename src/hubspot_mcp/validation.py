from __future__ import annotations

from difflib import get_close_matches
from typing import Any

from hubspot_mcp.cache import SchemaCache
from hubspot_mcp.scope_registry import get_required_scopes_for_agent


class ValidationError(Exception):
    def __init__(self, message: str, suggestions: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.suggestions = suggestions or []


def _extract_property_names(schema_data: dict[str, Any] | None) -> set[str]:
    if schema_data is None:
        return set()
    results = schema_data.get("results", [])
    if not isinstance(results, list):
        return set()
    return {str(n) for p in results if isinstance(p, dict) and (n := p.get("name")) is not None and n != ""}


def _extract_property_type(schema_data: dict[str, Any] | None, prop_name: str) -> str | None:
    if schema_data is None:
        return None
    results = schema_data.get("results", [])
    if not isinstance(results, list):
        return None
    for p in results:
        if isinstance(p, dict) and p.get("name") == prop_name:
            return p.get("type")
    return None


def _type_compatible(value: Any, prop_type: str | None) -> bool:
    if prop_type is None:
        return True
    if value is None:
        return True
    if prop_type == "string":
        return isinstance(value, str)
    if prop_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if prop_type == "bool":
        return isinstance(value, bool)
    if prop_type == "enumeration":
        return isinstance(value, str)
    if prop_type == "date":
        return isinstance(value, str)
    return True


def validate_object_type(
    object_type: str,
    portal_id: str,
    base_dir: Any = None,
) -> bool:
    """Return True if *object_type* is a known standard or cached custom type."""
    if object_type in {"contacts", "companies", "deals", "tickets"}:
        return True
    cache = SchemaCache(portal_id, base_dir=base_dir)
    return cache.get(object_type) is not None


def validate_properties(
    object_type: str,
    properties: dict[str, Any],
    portal_id: str,
    base_dir: Any = None,
) -> dict[str, Any]:
    """Validate property names and types against cached schema.

    Returns a dict with:
      - 'valid': bool
      - 'errors': list[dict] — each with 'property', 'reason', 'suggestions'
    """
    cache = SchemaCache(portal_id, base_dir=base_dir)
    schema_data = cache.get(object_type)

    # Graceful degradation: if no schema cached, allow the write
    if schema_data is None:
        return {"valid": True, "errors": [], "refreshed": False}

    known = _extract_property_names(schema_data)

    errors: list[dict[str, Any]] = []

    for prop_name, value in properties.items():
        if prop_name not in known:
            suggestions = get_close_matches(prop_name, known, n=3, cutoff=0.6)
            errors.append({
                "property": prop_name,
                "reason": "unknown_property",
                "suggestions": suggestions,
            })
            continue

        prop_type = _extract_property_type(schema_data, prop_name)
        if not _type_compatible(value, prop_type):
            errors.append({
                "property": prop_name,
                "reason": f"type_mismatch (expected {prop_type}, got {type(value).__name__})",
                "suggestions": [],
            })

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "refreshed": False,
    }



# Read-only system fields present on every object type.  Fallback for schemas
# cached without ``modificationMetadata`` (custom objects — see
# ``discover_custom_schemas``).  Deliberately NOT a blanket ``hs_*`` strip:
# fields like ``hs_lead_status`` are writable.
_READ_ONLY_SYSTEM_PROPERTIES = frozenset({
    "hs_object_id",
    "createdate",
    "lastmodifieddate",
    "hs_createdate",
    "hs_lastmodifieddate",
    "hs_created_by_user_id",
    "hs_updated_by_user_id",
})


def filter_writable_properties(
    object_type: str,
    properties: dict[str, Any],
    portal_id: str,
    base_dir: Any = None,
) -> tuple[dict[str, Any], list[str]]:
    """Split *properties* into (writable, stripped-read-only-names).

    HubSpot rejects a PATCH that sets a read-only property, so replaying a
    snapshot verbatim (which includes ``hs_lastmodifieddate`` etc.) fails the
    whole update.  Prefers ``modificationMetadata.readOnlyValue`` from the
    cached raw ``/crm/v3/properties/{domain}`` response (standard objects);
    falls back to :data:`_READ_ONLY_SYSTEM_PROPERTIES` for properties whose
    cached schema entry carries no metadata (custom objects, cold cache).
    """
    cache = SchemaCache(portal_id, base_dir=base_dir)
    schema_data = cache.get(object_type)
    metadata_by_name: dict[str, dict[str, Any]] = {}
    if schema_data is not None:
        for entry in schema_data.get("results", []) or []:
            if isinstance(entry, dict) and entry.get("name"):
                meta = entry.get("modificationMetadata")
                if isinstance(meta, dict):
                    metadata_by_name[str(entry["name"])] = meta

    writable: dict[str, Any] = {}
    stripped: list[str] = []
    for name, value in properties.items():
        meta = metadata_by_name.get(name)
        if meta is not None:
            read_only = bool(meta.get("readOnlyValue"))
        else:
            read_only = name in _READ_ONLY_SYSTEM_PROPERTIES
        if read_only:
            stripped.append(name)
        else:
            writable[name] = value
    return writable, stripped


def validate_scopes(
    agent_names: list[str],
    portal_scopes: list[str],
    target_object: str | None = None,
) -> dict[str, list[str]]:
    """Return missing HubSpot OAuth scopes per agent.

    Args:
        agent_names: Agents requested for dispatch.
        portal_scopes: Scopes granted to the current portal.
        target_object: Optional object type to narrow object-specific scopes.

    Returns:
        Mapping from agent name to sorted list of missing scopes. Empty dict
        means all required scopes are present.
    """
    granted = set(portal_scopes or [])
    blocked: dict[str, list[str]] = {}
    for agent in agent_names:
        required = get_required_scopes_for_agent(agent, target_object)
        missing = sorted(required - granted)
        if missing:
            blocked[agent] = missing
    return blocked


def format_scope_error(blocked: dict[str, list[str]]) -> str:
    """Format missing-scope errors for CLI display."""
    if not blocked:
        return ""
    lines: list[str] = ["Missing HubSpot OAuth scopes:"]
    for agent, scopes in sorted(blocked.items()):
        for scope in scopes:
            lines.append(f"- {agent}: {scope}")
    return "\n".join(lines)
