from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.errors import HubSpotError


class CapabilityMatrix(BaseModel):
    tier: str = "unknown"
    contacts: bool = True
    companies: bool = True
    deals: bool = True
    tickets: bool = True
    workflows: bool = False
    lists: bool = True
    pipelines: bool = True
    users: bool = False
    custom_objects: bool = False
    calculated_properties: bool = False
    service_automation: bool = False
    marketing: bool = False
    cms: bool = False


class CapabilityCache:
    TTL_SECONDS = 86400  # 24 hours

    def __init__(self, portal_id: str, base_dir: Path | None = None) -> None:
        self.portal_id = portal_id
        self.base_dir = base_dir or (Path.home() / ".claude" / "hubspot" / portal_id)
        self.cache_file = self.base_dir / "capabilities.json"
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self.cache_file.exists():
            try:
                self._data = json.loads(self.cache_file.read_text())
            except json.JSONDecodeError:
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(json.dumps(self._data, indent=2))

    def get(self) -> CapabilityMatrix | None:
        entry = self._data.get("matrix")
        if entry is None:
            return None
        ts = entry.get("_timestamp", 0)
        if time.time() - ts > self.TTL_SECONDS:
            return None
        return CapabilityMatrix.model_validate(entry.get("data", {}))

    def set(self, matrix: CapabilityMatrix) -> None:
        self._data["matrix"] = {"_timestamp": time.time(), "data": matrix.model_dump()}
        self._save()

    def invalidate(self) -> None:
        self._data = {}
        if self.cache_file.exists():
            self.cache_file.unlink()


_AGENT_CAPABILITY_REQUIREMENTS: dict[str, list[str]] = {
    "workflows": ["workflows"],
    "users": ["users"],
    "service": ["service_automation"],
    "marketing": ["marketing"],
    "cms": ["cms"],
}


_NOT_ENTITLED_STATUSES = (401, 403, 404)


async def _probe_bool(client: HubSpotClient, path: str, portal_id: str) -> bool | None:
    """Probe one capability endpoint.

    Returns True (entitled), False (definitively not entitled — 401/403/404),
    or None for a transient failure (5xx/429/network), which must NOT be
    cached: one blip would otherwise disable the gated agents for a full TTL.
    """
    try:
        await client.get(path, portal_id=portal_id)
        return True
    except HubSpotError as exc:
        if exc.status_code in _NOT_ENTITLED_STATUSES:
            return False
        return None
    except Exception:
        return None


async def probe_portal(portal_config: PortalConfig) -> CapabilityMatrix:
    cache = CapabilityCache(portal_config.portal_id)
    cached = cache.get()
    if cached is not None:
        return cached

    client = HubSpotClient(portal_config)
    matrix = CapabilityMatrix()
    portal_id = portal_config.portal_id
    cacheable = True

    try:
        try:
            resp = await client.get("/account-info/v3/details", portal_id=portal_id)
            matrix.tier = resp.body.get("tier", "unknown")
        except HubSpotError as exc:
            if exc.status_code not in _NOT_ENTITLED_STATUSES:
                cacheable = False
        except Exception:
            cacheable = False

        bool_probes = (
            ("custom_objects", "/crm/v3/schemas"),
            # The workflow tools call /automation/v4/flows; probing the
            # non-existent /automation/v4/workflows 404'd on live portals and
            # cached workflows=False for 24h.
            ("workflows", "/automation/v4/flows?limit=1"),
            ("users", "/settings/v3/users?limit=1"),
            ("marketing", "/marketing/v3/emails?limit=1"),
            ("cms", "/cms/v3/pages/site-pages?limit=1"),
        )
        for field, path in bool_probes:
            result = await _probe_bool(client, path, portal_id)
            if result is None:
                cacheable = False
            else:
                setattr(matrix, field, result)

        try:
            resp = await client.get("/crm/v3/properties/contacts", portal_id=portal_id)
            results = resp.body.get("results", [])
            has_calc = any(p.get("type") == "calculation" for p in results)
            if not has_calc:
                resp2 = await client.get("/crm/v3/properties/companies", portal_id=portal_id)
                results2 = resp2.body.get("results", [])
                has_calc = any(p.get("type") == "calculation" for p in results2)
            matrix.calculated_properties = has_calc
        except HubSpotError as exc:
            matrix.calculated_properties = False
            if exc.status_code not in _NOT_ENTITLED_STATUSES:
                cacheable = False
        except Exception:
            matrix.calculated_properties = False
            cacheable = False
    finally:
        await client.close()

    if cacheable:
        cache.set(matrix)
    return matrix


def has_capability(matrix: CapabilityMatrix, feature: str) -> bool:
    return getattr(matrix, feature, False)


def validate_capabilities(
    agent_names: list[str],
    matrix: CapabilityMatrix,
) -> dict[str, list[str]]:
    blocked: dict[str, list[str]] = {}
    for name in agent_names:
        required = _AGENT_CAPABILITY_REQUIREMENTS.get(name, [])
        missing = [f for f in required if not has_capability(matrix, f)]
        if missing:
            blocked[name] = missing
    return blocked


def capability_explanation(feature: str) -> str:
    explanations: dict[str, str] = {
        "workflows": "Workflow automation requires a Professional or Enterprise HubSpot subscription.",
        "users": "User management requires a Professional or Enterprise HubSpot subscription.",
        "custom_objects": "Custom objects require an Enterprise HubSpot subscription.",
        "calculated_properties": "Calculated properties require an Enterprise HubSpot subscription.",
        "service_automation": "Service automation requires a Professional or Enterprise HubSpot subscription.",
        "marketing": "Marketing emails and campaigns require a Marketing Hub Professional or Enterprise subscription.",
        "cms": "CMS content management requires a CMS Hub or Content Hub subscription.",
    }
    return explanations.get(feature, f"{feature} is not available on this portal.")


# --- Tool-level gating (tools-only MCP server) -------------------------------
#
# Upstream maps AGENTS to capabilities; this server exposes tools directly, so
# the tool-level map is derived from the agent map plus scope_registry's
# agent->tools index rather than hand-maintained, which would drift.


def tool_capability_requirements() -> dict[str, list[str]]:
    """Map each tool to the portal capabilities its agent requires."""
    from hubspot_mcp.scope_registry import _AGENT_TOOLS

    out: dict[str, list[str]] = {}
    for agent, features in _AGENT_CAPABILITY_REQUIREMENTS.items():
        for tool_name in _AGENT_TOOLS.get(agent, []):
            out.setdefault(tool_name, []).extend(features)
    return {k: sorted(set(v)) for k, v in out.items()}


def missing_capabilities_for_tool(tool_name: str, matrix: CapabilityMatrix) -> list[str]:
    """Capabilities ``tool_name`` needs that ``matrix`` says the portal lacks."""
    required = tool_capability_requirements().get(tool_name, [])
    return [f for f in required if not has_capability(matrix, f)]


def probe_was_conclusive(portal_id: str) -> bool:
    """Whether the last probe was definitive enough to persist.

    ``probe_portal`` writes the cache only when every probe returned a
    definitive answer; a transient 5xx/429/network failure leaves the matrix at
    its **defaults** (``workflows``, ``users``, ``marketing``, ``cms`` and
    ``custom_objects`` all default to False) and skips the write. Treating that
    as ground truth would silently unadvertise a dozen tools after one blip,
    with no cache entry to explain it -- so callers that hide tools must gate on
    this, and callers that merely explain a refusal need not.
    """
    return CapabilityCache(portal_id).get() is not None
