import time

import httpx
import pytest

from hubspot_mcp.cache import WARM_DOMAINS, SchemaCache, warm_standard_schemas
from hubspot_mcp.config import PortalConfig


def test_cache_get_set(tmp_path):
    cache = SchemaCache("123", base_dir=tmp_path)
    cache.set("objects", {"contacts": ["email"]})
    assert cache.get("objects") == {"contacts": ["email"]}


def test_cache_miss(tmp_path):
    cache = SchemaCache("123", base_dir=tmp_path)
    assert cache.get("nonexistent") is None


def test_cache_ttl_expiration(tmp_path, monkeypatch):
    cache = SchemaCache("123", base_dir=tmp_path)
    cache.set("objects", {"contacts": ["email"]})
    fixed_time = time.time() + 4000
    monkeypatch.setattr(time, "time", lambda: fixed_time)
    assert cache.get("objects") is None


def test_cache_invalidate(tmp_path):
    cache = SchemaCache("123", base_dir=tmp_path)
    cache.set("objects", {"contacts": ["email"]})
    cache.invalidate("objects")
    assert cache.get("objects") is None


def test_cache_refresh_all(tmp_path):
    cache = SchemaCache("123", base_dir=tmp_path)
    cache.set("objects", {"contacts": ["email"]})
    cache.refresh_all()
    assert cache.get("objects") is None


def test_cache_refresh_domain(tmp_path):
    cache = SchemaCache("123", base_dir=tmp_path)
    cache.set("objects", {"contacts": ["email"]})
    cache.set("pipelines", {"stages": []})
    cache.refresh_domain("objects")
    assert cache.get("objects") is None
    assert cache.get("pipelines") == {"stages": []}


# ---------------------------------------------------------------------------
# Warm tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warm_standard_schemas_caches_all(respx_mock, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "hubspot_mcp.cache.Path.home", lambda: tmp_path
    )
    for domain in WARM_DOMAINS:
        respx_mock.get(f"https://api.hubapi.com/crm/v3/properties/{domain}").mock(
            return_value=httpx.Response(200, json={"results": [{"name": domain}]})
        )

    portal = PortalConfig(portal_id="123", token="test-token")
    cache = await warm_standard_schemas(portal)

    for domain in WARM_DOMAINS:
        data = cache.get(domain)
        assert data is not None
        assert data["results"][0]["name"] == domain


@pytest.mark.asyncio
async def test_warm_standard_schemas_handles_errors(respx_mock, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "hubspot_mcp.cache.Path.home", lambda: tmp_path
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/properties/contacts").mock(
        return_value=httpx.Response(200, json={"results": [{"name": "email"}]})
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/properties/companies").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/properties/deals").mock(
        return_value=httpx.Response(200, json={"results": [{"name": "amount"}]})
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/properties/tickets").mock(
        return_value=httpx.Response(500, text="Error")
    )

    portal = PortalConfig(portal_id="123", token="test-token")
    cache = await warm_standard_schemas(portal)

    assert cache.get("contacts") is not None
    assert cache.get("companies") is None
    assert cache.get("deals") is not None
    assert cache.get("tickets") is None


def test_cache_persists_across_instances(tmp_path):
    cache1 = SchemaCache("123", base_dir=tmp_path)
    cache1.set("objects", {"contacts": ["email"]})
    cache2 = SchemaCache("123", base_dir=tmp_path)
    assert cache2.get("objects") == {"contacts": ["email"]}


def test_cache_loads_corrupt_json_as_empty(tmp_path):
    cache_file = tmp_path / "schema_cache.json"
    cache_file.write_text("not-json")
    cache = SchemaCache("123", base_dir=tmp_path)
    assert cache.get("objects") is None


@pytest.mark.asyncio
async def test_warm_standard_schemas_total_failure(respx_mock, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "hubspot_mcp.cache.Path.home", lambda: tmp_path
    )
    for domain in WARM_DOMAINS:
        respx_mock.get(f"https://api.hubapi.com/crm/v3/properties/{domain}").mock(
            return_value=httpx.Response(401, json={"message": "Unauthorized"})
        )

    portal = PortalConfig(portal_id="123", token="test-token")
    cache = await warm_standard_schemas(portal)

    for domain in WARM_DOMAINS:
        assert cache.get(domain) is None