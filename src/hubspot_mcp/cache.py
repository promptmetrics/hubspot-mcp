from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig

WARM_DOMAINS = ["contacts", "companies", "deals", "tickets"]

# Canonical set of built-in HubSpot object types that never need custom-schema
# discovery.  ``tools/objects.py`` aliases this as ``_VALID_OBJECT_TYPES`` so the
# tool path and the cache layer share one source of truth (avoids an import
# cycle: ``tools.objects`` imports ``cache``, not the reverse).
STANDARD_OBJECT_TYPES = frozenset({
    "contacts",
    "companies",
    "deals",
    "tickets",
    "carts",
    "order",
    "quotes",
    "subscriptions",
    "invoices",
    "discount",
    "fees",
    "taxes",
    "goal_targets",
})


async def ensure_custom_schema_cached(
    portal_config: PortalConfig, object_type: str | None
) -> None:
    """Discover custom object schemas if ``object_type`` is non-standard and absent.

    The tool path validates custom object types against the on-disk
    :class:`SchemaCache`; custom schemas only land there via
    :func:`discover_custom_schemas`, which the agent path runs in
    ``initialize_session`` but the tool path historically did not.  Calling this
    before validation lets ``hubspot tool ... '{"object_type":"<custom>"}'``
    succeed on a cold cache.  Idempotent: it skips the HubSpot ``/schemas`` fetch
    when the type is built-in or already cached (within TTL).
    """
    if not object_type or object_type in STANDARD_OBJECT_TYPES:
        return
    if SchemaCache(portal_config.portal_id).get(object_type) is not None:
        return
    await discover_custom_schemas(portal_config)


async def discover_custom_schemas(portal_config: PortalConfig) -> list[str]:
    """Fetch custom object schemas from HubSpot and cache them.

    Returns a list of custom object type names.  If the portal has no
    custom objects, returns an empty list.
    """
    client = HubSpotClient(portal_config)
    try:
        resp = await client.get(
            "/crm/v3/schemas",
            portal_id=portal_config.portal_id,
            expected_scopes=["crm.schemas.read"],
        )
        schemas = resp.body.get("results", [])
        cache = SchemaCache(portal_config.portal_id)
        names: list[str] = []
        for schema in schemas:
            if not isinstance(schema, dict):
                continue
            name = schema.get("name")
            if not name or name in WARM_DOMAINS:
                continue
            names.append(name)
            properties = schema.get("properties", [])
            formatted = {
                "results": [
                    {"name": p.get("name"), "type": p.get("type")}
                    for p in properties
                    if isinstance(p, dict) and p.get("name")
                ]
            }
            cache.set(name, formatted)
        return names
    except Exception:
        return []
    finally:
        await client.close()


class SchemaCache:
    """Per-portal object/property schema, cached on local disk.

    **Deliberately not moved to Redis in Phase 2**, unlike the capability matrix
    and the docs index. Every writer here is async, but the readers are not:
    ``validation``, ``tools/objects``, ``agent_routing`` and the agent prompt
    builders all call ``get`` / ``list_custom_object_names`` from synchronous
    code, and the prompt builders are invoked synchronously from
    ``prompts/list``. Making this async means rewriting the validation and
    prompt layers — which the Phase 2 plan names as the signal that a seam is in
    the wrong place.

    The cost of leaving it per-instance is small and bounded: a cold instance
    re-warms the standard schemas in ``app_lifespan``, which is the same work a
    fresh stdio session already does today. Contrast the capability matrix,
    where a per-instance copy makes two instances advertise *different tool
    lists* for one portal, and the docs index, where a cold build is ~40
    outbound fetches.

    If cold-start cost ever does matter: hydrate this from a shared cache in the
    lifespan and push back on write, keeping the synchronous reader interface.
    """

    TTL_SECONDS = 3600  # 1 hour

    def __init__(self, portal_id: str, base_dir: Path | None = None) -> None:
        self.portal_id = portal_id
        self.base_dir = base_dir or (Path.home() / ".claude" / "hubspot" / portal_id)
        self.cache_file = self.base_dir / "schema_cache.json"
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

    def get(self, domain: str) -> dict[str, Any] | None:
        entry = self._data.get(domain)
        if entry is None:
            return None
        ts = entry.get("_timestamp", 0)
        if time.time() - ts > self.TTL_SECONDS:
            return None
        return entry.get("data")

    def set(self, domain: str, data: dict[str, Any]) -> None:
        self._data[domain] = {"_timestamp": time.time(), "data": data}
        self._save()

    def invalidate(self, domain: str) -> None:
        if domain in self._data:
            del self._data[domain]
            self._save()

    def refresh_all(self) -> None:
        self._data = {}
        if self.cache_file.exists():
            self.cache_file.unlink()

    def refresh_domain(self, domain: str) -> None:
        self.invalidate(domain)

    def list_custom_object_names(self) -> list[str]:
        """Return cached custom object type names (non-standard, non-expired)."""
        standard = set(WARM_DOMAINS)
        names: list[str] = []
        for domain, entry in self._data.items():
            if domain.startswith("_") or domain in standard:
                continue
            ts = entry.get("_timestamp", 0)
            if time.time() - ts > self.TTL_SECONDS:
                continue
            names.append(domain)
        return names


async def warm_standard_schemas(portal_config: PortalConfig) -> SchemaCache:
    cache = SchemaCache(portal_config.portal_id)
    client = HubSpotClient(portal_config)
    try:

        async def _fetch(domain: str) -> tuple[str, dict[str, Any] | None]:
            try:
                resp = await client.get(
                    f"/crm/v3/properties/{domain}",
                    portal_id=portal_config.portal_id,
                )
                return domain, resp.body
            except Exception:
                return domain, None

        results = await asyncio.gather(*(_fetch(d) for d in WARM_DOMAINS))
        for domain, data in results:
            if data is not None:
                cache.set(domain, data)
    finally:
        await client.close()
    return cache
