"""Expiring caches, and why they are a separate interface (Phase 2, Task 4).

`StateStore` and `CacheStore` need opposite failure behaviour. Losing a pending
preview breaks an approve, so a `StateStore` backend failure must surface.
Losing the capability matrix or the docs index costs a refetch, so a
`CacheStore` backend failure must read as a **miss** — otherwise a Redis blip
fails tool calls that could simply have gone to HubSpot instead.

Everything above the `# Redis-specific` divider runs against both backends.
"""
from __future__ import annotations

import json
import time

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from hubspot_mcp.state.cache_store import CacheStore, FileCacheStore, get_cache_store, set_cache_store
from hubspot_mcp.state.redis_store import RedisCacheStore

PORTAL = "99999999"


@pytest.fixture
def fernet_key():
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


@pytest.fixture
def fake_redis():
    import fakeredis.aioredis

    return fakeredis.aioredis.FakeRedis(decode_responses=False)


@pytest.fixture(params=["file", "redis"])
async def cache(request, tmp_path, monkeypatch, fake_redis, fernet_key):
    if request.param == "file":
        monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
        yield FileCacheStore()
    else:
        yield RedisCacheStore(fake_redis, encryption_key=fernet_key)
        await fake_redis.aclose()


# --------------------------------------------------------------------------- #
# The contract, on both backends
# --------------------------------------------------------------------------- #


async def test_round_trips(cache):
    await cache.set(PORTAL, "capabilities", {"workflows": True}, ttl_seconds=60)
    assert await cache.get(PORTAL, "capabilities") == {"workflows": True}


async def test_a_missing_entry_is_none(cache):
    assert await cache.get(PORTAL, "never-written") is None


async def test_expiry_is_a_miss(cache, monkeypatch):
    await cache.set(PORTAL, "capabilities", {"workflows": True}, ttl_seconds=1)
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 3600)
    assert await cache.get(PORTAL, "capabilities") is None


async def test_delete_removes_the_entry(cache):
    await cache.set(PORTAL, "capabilities", {"workflows": True}, ttl_seconds=60)
    await cache.delete(PORTAL, "capabilities")
    assert await cache.get(PORTAL, "capabilities") is None


async def test_deleting_a_missing_entry_is_a_noop(cache):
    await cache.delete(PORTAL, "never-written")


async def test_scopes_are_isolated(cache):
    """One portal's entitlements must never answer for another's."""
    await cache.set(PORTAL, "capabilities", {"workflows": True}, ttl_seconds=60)
    assert await cache.get("11111111", "capabilities") is None


async def test_a_global_entry_is_not_a_portal_entry(cache):
    """The docs index is global; a portal-scoped read must not find it."""
    await cache.set(None, "docs_index", {"entries": []}, ttl_seconds=60)
    assert await cache.get(None, "docs_index") == {"entries": []}
    assert await cache.get(PORTAL, "docs_index") is None


async def test_overwrite_replaces_the_value(cache):
    await cache.set(PORTAL, "capabilities", {"workflows": True}, ttl_seconds=60)
    await cache.set(PORTAL, "capabilities", {"workflows": False}, ttl_seconds=60)
    assert await cache.get(PORTAL, "capabilities") == {"workflows": False}


# --------------------------------------------------------------------------- #
# The whole reason this is not StateStore: a backend failure is a miss
# --------------------------------------------------------------------------- #


class _BrokenRedis:
    """Every operation fails, the way an unreachable Redis does."""

    async def get(self, *a, **kw):
        raise RedisConnectionError("connection refused")

    async def set(self, *a, **kw):
        raise RedisConnectionError("connection refused")

    async def delete(self, *a, **kw):
        raise RedisConnectionError("connection refused")


async def test_an_unreachable_backend_reads_as_a_miss(fernet_key, capsys):
    """A Redis blip must send the caller to HubSpot, not fail the tool call."""
    broken = RedisCacheStore(_BrokenRedis(), encryption_key=fernet_key)
    assert await broken.get(PORTAL, "capabilities") is None
    assert "cache read failed" in capsys.readouterr().err


async def test_an_unreachable_backend_drops_writes_loudly(fernet_key, capsys):
    broken = RedisCacheStore(_BrokenRedis(), encryption_key=fernet_key)
    await broken.set(PORTAL, "capabilities", {"workflows": True}, ttl_seconds=60)
    await broken.delete(PORTAL, "capabilities")
    err = capsys.readouterr().err
    assert "cache write failed" in err
    assert "cache delete failed" in err


async def test_the_state_store_does_not_swallow_backend_failures(fernet_key):
    """The contrast that justifies two interfaces: state failures must surface."""
    from hubspot_mcp.state.redis_store import RedisStateStore

    class _BrokenStateRedis(_BrokenRedis):
        pass

    store = RedisStateStore(_BrokenStateRedis(), encryption_key=fernet_key)
    with pytest.raises(RedisConnectionError):
        await store.load_pending(PORTAL, "act-1")


# --------------------------------------------------------------------------- #
# Redis-specific
# --------------------------------------------------------------------------- #


async def test_redis_cache_values_are_encrypted_at_rest(fake_redis, fernet_key):
    """Custom property names describe the portal's business configuration."""
    cache = RedisCacheStore(fake_redis, encryption_key=fernet_key)
    await cache.set(PORTAL, "capabilities", {"custom_object": "p_secret_deal_type"}, ttl_seconds=60)
    for key in await fake_redis.keys("*"):
        assert b"p_secret_deal_type" not in await fake_redis.dump(key)
    await fake_redis.aclose()


async def test_redis_cache_sets_a_ttl(fake_redis, fernet_key):
    cache = RedisCacheStore(fake_redis, encryption_key=fernet_key)
    await cache.set(PORTAL, "capabilities", {"workflows": True}, ttl_seconds=60)
    assert 0 < await fake_redis.ttl(cache._key(PORTAL, "capabilities")) <= 60
    await fake_redis.aclose()


async def test_a_value_under_a_rotated_key_is_a_miss(fake_redis, fernet_key):
    from cryptography.fernet import Fernet

    writer = RedisCacheStore(fake_redis, encryption_key=fernet_key)
    await writer.set(PORTAL, "capabilities", {"workflows": True}, ttl_seconds=60)
    reader = RedisCacheStore(fake_redis, encryption_key=Fernet.generate_key().decode())
    assert await reader.get(PORTAL, "capabilities") is None
    await fake_redis.aclose()


# --------------------------------------------------------------------------- #
# File backend keeps the paths it had before the seam existed
# --------------------------------------------------------------------------- #


async def test_file_backend_writes_the_pre_existing_paths(tmp_path, monkeypatch):
    """A layout change would orphan every local cache on upgrade."""
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    cache = FileCacheStore()
    await cache.set(PORTAL, "capabilities", {"workflows": True}, ttl_seconds=60)
    await cache.set(None, "docs_index", {"entries": []}, ttl_seconds=60)

    assert (tmp_path / PORTAL / "capabilities.json").exists()
    assert (tmp_path / "docs_index.json").exists()


async def test_file_backend_writes_privately(tmp_path, monkeypatch):
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    await FileCacheStore().set(PORTAL, "capabilities", {"workflows": True}, ttl_seconds=60)
    assert (tmp_path / PORTAL / "capabilities.json").stat().st_mode & 0o077 == 0


async def test_corrupt_cache_file_is_a_miss(tmp_path, monkeypatch):
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    (tmp_path / PORTAL).mkdir(parents=True)
    (tmp_path / PORTAL / "capabilities.json").write_text("{ not json")
    assert await FileCacheStore().get(PORTAL, "capabilities") is None


async def test_file_backend_stamps_an_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    await FileCacheStore().set(PORTAL, "capabilities", {"workflows": True}, ttl_seconds=60)
    payload = json.loads((tmp_path / PORTAL / "capabilities.json").read_text())
    assert time.time() < payload["_expires_at"] <= time.time() + 60


# --------------------------------------------------------------------------- #
# Backend selection tracks the state store
# --------------------------------------------------------------------------- #


def test_redis_url_selects_the_redis_cache(monkeypatch, fernet_key):
    from hubspot_mcp.state import BACKEND_ENV
    from hubspot_mcp.state.cache_store import _build_default_cache_store

    monkeypatch.delenv(BACKEND_ENV, raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("HUBSPOT_MCP_STATE_KEY", fernet_key)
    assert isinstance(_build_default_cache_store(), RedisCacheStore)


def test_no_redis_url_selects_the_file_cache(monkeypatch):
    from hubspot_mcp.state import BACKEND_ENV
    from hubspot_mcp.state.cache_store import _build_default_cache_store

    monkeypatch.delenv(BACKEND_ENV, raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert isinstance(_build_default_cache_store(), FileCacheStore)


def test_set_cache_store_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    set_cache_store(None)
    assert isinstance(get_cache_store(), FileCacheStore)
    replacement = FileCacheStore()
    set_cache_store(replacement)
    assert get_cache_store() is replacement
    set_cache_store(None)


def test_every_cache_interface_method_is_a_coroutine():
    import inspect

    sync = [
        name
        for name, member in vars(CacheStore).items()
        if getattr(member, "__isabstractmethod__", False) and not inspect.iscoroutinefunction(member)
    ]
    assert sync == []
