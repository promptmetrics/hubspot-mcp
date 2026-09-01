import json
import time

import httpx
import pytest

from hubspot_mcp.capabilities import (
    CapabilityCache,
    CapabilityMatrix,
    capability_explanation,
    has_capability,
    probe_portal,
    validate_capabilities,
)
from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig


def test_capability_matrix_defaults():
    m = CapabilityMatrix()
    assert m.contacts is True
    assert m.companies is True
    assert m.deals is True
    assert m.tickets is True
    assert m.workflows is False
    assert m.lists is True
    assert m.pipelines is True
    assert m.users is False
    assert m.custom_objects is False
    assert m.calculated_properties is False


def test_capability_matrix_override():
    m = CapabilityMatrix(workflows=True, users=True)
    assert m.workflows is True
    assert m.users is True


def test_has_capability():
    m = CapabilityMatrix(workflows=True)
    assert has_capability(m, "workflows") is True
    assert has_capability(m, "users") is False
    assert has_capability(m, "nonexistent") is False


def test_validate_capabilities_empty():
    m = CapabilityMatrix()
    assert validate_capabilities([], m) == {}


def test_validate_capabilities_all_available():
    m = CapabilityMatrix(workflows=True, users=True)
    assert validate_capabilities(["workflows", "users"], m) == {}


def test_validate_capabilities_missing():
    m = CapabilityMatrix(workflows=False, users=False)
    result = validate_capabilities(["workflows", "users"], m)
    assert result == {"workflows": ["workflows"], "users": ["users"]}


def test_validate_capabilities_mixed():
    m = CapabilityMatrix(workflows=True, users=False)
    result = validate_capabilities(["workflows", "users"], m)
    assert result == {"users": ["users"]}


def test_validate_capabilities_unmapped_agent():
    m = CapabilityMatrix()
    assert validate_capabilities(["objects", "properties"], m) == {}


def test_capability_explanation_known():
    assert "Professional or Enterprise" in capability_explanation("workflows")
    assert "Professional or Enterprise" in capability_explanation("users")
    assert "Enterprise" in capability_explanation("custom_objects")
    assert "Enterprise" in capability_explanation("calculated_properties")


def test_capability_explanation_unknown():
    assert capability_explanation("unknown_feature") == "unknown_feature is not available on this portal."


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


def test_cache_get_set(tmp_path):
    cache = CapabilityCache("123", base_dir=tmp_path)
    m = CapabilityMatrix(workflows=True)
    cache.set(m)
    retrieved = cache.get()
    assert retrieved is not None
    assert retrieved.workflows is True


def test_cache_miss(tmp_path):
    cache = CapabilityCache("123", base_dir=tmp_path)
    assert cache.get() is None


def test_cache_ttl_expiration(tmp_path, monkeypatch):
    cache = CapabilityCache("123", base_dir=tmp_path)
    cache.set(CapabilityMatrix(workflows=True))
    fixed_time = time.time() + 90000
    monkeypatch.setattr(time, "time", lambda: fixed_time)
    assert cache.get() is None


def test_cache_invalidate(tmp_path):
    cache = CapabilityCache("123", base_dir=tmp_path)
    cache.set(CapabilityMatrix(workflows=True))
    cache.invalidate()
    assert cache.get() is None
    assert not cache.cache_file.exists()


def test_cache_persists_to_disk(tmp_path):
    cache = CapabilityCache("123", base_dir=tmp_path)
    cache.set(CapabilityMatrix(workflows=True, users=True))

    cache2 = CapabilityCache("123", base_dir=tmp_path)
    retrieved = cache2.get()
    assert retrieved is not None
    assert retrieved.workflows is True
    assert retrieved.users is True


def test_cache_file_structure(tmp_path):
    cache = CapabilityCache("123", base_dir=tmp_path)
    cache.set(CapabilityMatrix(workflows=True))
    data = json.loads(cache.cache_file.read_text())
    assert "matrix" in data
    assert "_timestamp" in data["matrix"]
    assert "data" in data["matrix"]
    assert data["matrix"]["data"]["workflows"] is True


# ---------------------------------------------------------------------------
# Probe tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_portal_uses_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hubspot_mcp.capabilities.CapabilityCache",
        lambda portal_id, base_dir=None: CapabilityCache(portal_id, base_dir=tmp_path),
    )
    cache = CapabilityCache("123", base_dir=tmp_path)
    cache.set(CapabilityMatrix(workflows=True))

    portal = PortalConfig(portal_id="123", token="test-token")
    result = await probe_portal(portal)
    assert result.workflows is True


@pytest.mark.asyncio
async def test_probe_portal_detects_tier(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hubspot_mcp.capabilities.CapabilityCache",
        lambda portal_id, base_dir=None: CapabilityCache(portal_id, base_dir=tmp_path),
    )
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    respx_mock.get("https://api.hubapi.com/account-info/v3/details").mock(
        return_value=httpx.Response(200, json={"tier": "Enterprise"})
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/schemas").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )
    respx_mock.get("https://api.hubapi.com/automation/v4/flows?limit=1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get("https://api.hubapi.com/settings/v3/users?limit=1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/properties/contacts").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    portal = PortalConfig(portal_id="123", token="test-token")
    result = await probe_portal(portal)
    assert result.workflows is True
    assert result.custom_objects is False
    await client.close()


@pytest.mark.asyncio
async def test_probe_portal_detects_custom_objects(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hubspot_mcp.capabilities.CapabilityCache",
        lambda portal_id, base_dir=None: CapabilityCache(portal_id, base_dir=tmp_path),
    )
    respx_mock.get("https://api.hubapi.com/account-info/v3/details").mock(
        return_value=httpx.Response(200, json={})
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/schemas").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get("https://api.hubapi.com/automation/v4/flows?limit=1").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )
    respx_mock.get("https://api.hubapi.com/settings/v3/users?limit=1").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/properties/contacts").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    portal = PortalConfig(portal_id="123", token="test-token")
    result = await probe_portal(portal)
    assert result.custom_objects is True
    assert result.workflows is False
    assert result.users is False


@pytest.mark.asyncio
async def test_probe_portal_detects_calculated_properties(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hubspot_mcp.capabilities.CapabilityCache",
        lambda portal_id, base_dir=None: CapabilityCache(portal_id, base_dir=tmp_path),
    )
    respx_mock.get("https://api.hubapi.com/account-info/v3/details").mock(
        return_value=httpx.Response(200, json={})
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/schemas").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )
    respx_mock.get("https://api.hubapi.com/automation/v4/flows?limit=1").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )
    respx_mock.get("https://api.hubapi.com/settings/v3/users?limit=1").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/properties/contacts").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"name": "email", "type": "string"},
                    {"name": "lifetime_value", "type": "calculation"},
                ]
            },
        )
    )

    portal = PortalConfig(portal_id="123", token="test-token")
    result = await probe_portal(portal)
    assert result.calculated_properties is True


@pytest.mark.asyncio
async def test_probe_portal_caches_result(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hubspot_mcp.capabilities.CapabilityCache",
        lambda portal_id, base_dir=None: CapabilityCache(portal_id, base_dir=tmp_path),
    )
    respx_mock.get("https://api.hubapi.com/account-info/v3/details").mock(
        return_value=httpx.Response(200, json={"tier": "Professional"})
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/schemas").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )
    respx_mock.get("https://api.hubapi.com/automation/v4/flows?limit=1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get("https://api.hubapi.com/settings/v3/users?limit=1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/properties/contacts").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    # Every probe must resolve definitively (entitled or 401/403/404) for the
    # matrix to be cached — a transient failure skips the cache write (M10).
    respx_mock.get("https://api.hubapi.com/crm/v3/properties/companies").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get("https://api.hubapi.com/marketing/v3/emails?limit=1").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )
    respx_mock.get("https://api.hubapi.com/cms/v3/pages/site-pages?limit=1").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )

    portal = PortalConfig(portal_id="123", token="test-token")
    result = await probe_portal(portal)
    assert result.workflows is True

    cache = CapabilityCache("123", base_dir=tmp_path)
    cached = cache.get()
    assert cached is not None
    assert cached.workflows is True
    assert cached.users is True


@pytest.mark.asyncio
async def test_probe_portal_handles_all_failures(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hubspot_mcp.capabilities.CapabilityCache",
        lambda portal_id, base_dir=None: CapabilityCache(portal_id, base_dir=tmp_path),
    )
    respx_mock.get("https://api.hubapi.com/account-info/v3/details").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/schemas").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    respx_mock.get("https://api.hubapi.com/automation/v4/flows?limit=1").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    respx_mock.get("https://api.hubapi.com/settings/v3/users?limit=1").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/properties/contacts").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )

    portal = PortalConfig(portal_id="123", token="test-token")
    result = await probe_portal(portal)
    assert result.contacts is True
    assert result.workflows is False
    assert result.users is False
    assert result.custom_objects is False
    assert result.calculated_properties is False


# ---------------------------------------------------------------------------
# M10: probe hits /automation/v4/flows (what the workflow tools call), caches
# a definitive 404 as not-entitled, and never caches a transient failure.
# ---------------------------------------------------------------------------


def _mock_all_probes_ok(respx_mock, *, flows=None):
    respx_mock.get("https://api.hubapi.com/account-info/v3/details").mock(
        return_value=httpx.Response(200, json={"tier": "Professional"})
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/schemas").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get("https://api.hubapi.com/automation/v4/flows?limit=1").mock(
        return_value=flows or httpx.Response(200, json={"results": []})
    )
    respx_mock.get("https://api.hubapi.com/settings/v3/users?limit=1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/properties/contacts").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get("https://api.hubapi.com/crm/v3/properties/companies").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get("https://api.hubapi.com/marketing/v3/emails?limit=1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get("https://api.hubapi.com/cms/v3/pages/site-pages?limit=1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )


@pytest.mark.asyncio
async def test_workflows_probe_hits_flows_endpoint(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hubspot_mcp.capabilities.CapabilityCache",
        lambda portal_id, base_dir=None: CapabilityCache(portal_id, base_dir=tmp_path),
    )
    _mock_all_probes_ok(respx_mock)
    result = await probe_portal(PortalConfig(portal_id="123", token="test-token"))
    assert result.workflows is True
    flows_calls = [c for c in respx_mock.calls if "automation/v4/flows" in str(c.request.url)]
    assert len(flows_calls) == 1


@pytest.mark.asyncio
async def test_probe_404_caches_false(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hubspot_mcp.capabilities.CapabilityCache",
        lambda portal_id, base_dir=None: CapabilityCache(portal_id, base_dir=tmp_path),
    )
    _mock_all_probes_ok(respx_mock, flows=httpx.Response(404, json={"message": "not found"}))
    result = await probe_portal(PortalConfig(portal_id="123", token="test-token"))
    assert result.workflows is False
    cached = CapabilityCache("123", base_dir=tmp_path).get()
    assert cached is not None and cached.workflows is False


@pytest.mark.asyncio
async def test_probe_5xx_does_not_cache_and_reprobes(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hubspot_mcp.capabilities.CapabilityCache",
        lambda portal_id, base_dir=None: CapabilityCache(portal_id, base_dir=tmp_path),
    )
    _mock_all_probes_ok(respx_mock, flows=httpx.Response(503, text="Service Unavailable"))
    portal = PortalConfig(portal_id="123", token="test-token")

    result = await probe_portal(portal)
    assert result.workflows is False  # safe default, but...
    assert CapabilityCache("123", base_dir=tmp_path).get() is None  # ...not cached

    # A second call re-probes instead of serving a poisoned matrix.
    await probe_portal(portal)
    flows_calls = [c for c in respx_mock.calls if "automation/v4/flows" in str(c.request.url)]
    assert len(flows_calls) == 2
