"""Per-user HubSpot connections (Phase 3, stage 1).

This store joins the two halves of Phase 3's auth: an identity subject minted by
the identity provider, and the HubSpot portal that person authorised us to act
on. It holds refresh tokens — long-lived credentials to someone else's CRM — so
the tests below care as much about how it fails and what it leaks as about the
round trip.

Everything above the `# Backend-specific` divider runs against both backends.
"""
from __future__ import annotations

import json
import time

import pytest

from hubspot_mcp.state.connection_store import (
    ConnectionUnreadable,
    FileConnectionStore,
    HubSpotConnection,
    get_connection_store,
    set_connection_store,
    subject_key,
)
from hubspot_mcp.state.redis_store import KEY_ENV, RedisConnectionStore

SUBJECT = "user_01HQXZ8P3Q"
OTHER_SUBJECT = "auth0|9f3c2b"
PORTAL = "99999999"
REFRESH = "hubspot-refresh-token-value"


@pytest.fixture
def fernet_key():
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


@pytest.fixture
def fake_redis():
    import fakeredis.aioredis

    return fakeredis.aioredis.FakeRedis(decode_responses=False)


@pytest.fixture(params=["file", "redis"])
async def store(request, tmp_path, monkeypatch, fake_redis, fernet_key):
    if request.param == "file":
        monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
        yield FileConnectionStore()
    else:
        yield RedisConnectionStore(fake_redis, encryption_key=fernet_key)
        await fake_redis.aclose()


def _connection(**overrides) -> HubSpotConnection:
    data = {
        "subject": SUBJECT,
        "portal_id": PORTAL,
        "refresh_token": REFRESH,
        "access_token": "hubspot-access-token",
        "expires_at": time.time() + 1800,
        "scopes_granted": ("crm.objects.contacts.read",),
    }
    data.update(overrides)
    return HubSpotConnection(**data)


# --------------------------------------------------------------------------- #
# The contract, on both backends
# --------------------------------------------------------------------------- #


async def test_round_trips(store):
    await store.put(_connection())
    found = await store.get(SUBJECT)
    assert found is not None
    assert found.portal_id == PORTAL
    assert found.refresh_token == REFRESH
    assert found.scopes_granted == ("crm.objects.contacts.read",)


async def test_an_unconnected_subject_is_none(store):
    assert await store.get(SUBJECT) is None


async def test_subjects_are_isolated(store):
    """One user's portal must never answer for another's."""
    await store.put(_connection())
    assert await store.get(OTHER_SUBJECT) is None


async def test_put_replaces(store):
    await store.put(_connection())
    await store.put(_connection(portal_id="12345678", refresh_token="second"))
    found = await store.get(SUBJECT)
    assert found is not None
    assert found.portal_id == "12345678"
    assert found.refresh_token == "second"


async def test_delete_actually_disconnects(store):
    await store.put(_connection())
    await store.delete(SUBJECT)
    assert await store.get(SUBJECT) is None


async def test_deleting_an_unconnected_subject_is_a_noop(store):
    await store.delete(SUBJECT)


async def test_awkward_subject_formats_round_trip(store):
    """Identity providers mint subjects in formats we do not control."""
    for subject in ["auth0|abc", "google-oauth2|10932", "ada@example.com", "a/b", "x" * 300, "*"]:
        await store.put(_connection(subject=subject))
        found = await store.get(subject)
        assert found is not None and found.subject == subject


# --------------------------------------------------------------------------- #
# Failure behaviour: unreadable is not the same as unconnected
# --------------------------------------------------------------------------- #


async def test_a_corrupt_record_raises_rather_than_reading_as_unconnected(tmp_path, monkeypatch):
    """Telling a user to reconnect an account they already have is the wrong answer."""
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    store = FileConnectionStore()
    await store.put(_connection())
    (tmp_path / "connections" / f"{subject_key(SUBJECT)}.json").write_text("{ not json")

    with pytest.raises(ConnectionUnreadable):
        await store.get(SUBJECT)


async def test_a_rotated_key_raises_rather_than_reading_as_unconnected(fake_redis, fernet_key):
    from cryptography.fernet import Fernet

    writer = RedisConnectionStore(fake_redis, encryption_key=fernet_key)
    await writer.put(_connection())

    reader = RedisConnectionStore(fake_redis, encryption_key=Fernet.generate_key().decode())
    with pytest.raises(ConnectionUnreadable, match=KEY_ENV):
        await reader.get(SUBJECT)
    await fake_redis.aclose()


# --------------------------------------------------------------------------- #
# Leakage
# --------------------------------------------------------------------------- #


def test_repr_does_not_leak_the_refresh_token():
    """An unredacted dataclass repr puts a CRM credential in every traceback."""
    rendered = repr(_connection())
    assert REFRESH not in rendered
    assert "hubspot-access-token" not in rendered
    assert "<redacted>" in rendered
    # Still useful for debugging.
    assert PORTAL in rendered and SUBJECT in rendered


def test_the_subject_is_not_used_as_a_storage_key():
    """A raw subject in a Redis key or file path is an injection surface."""
    key = subject_key("../../etc/passwd")
    assert key.isalnum() and len(key) == 64
    assert "/" not in key and ".." not in key


def test_subject_keys_are_stable_and_distinct():
    assert subject_key(SUBJECT) == subject_key(SUBJECT)
    assert subject_key(SUBJECT) != subject_key(OTHER_SUBJECT)


def test_an_empty_subject_is_refused():
    with pytest.raises(ValueError, match="must not be empty"):
        subject_key("")


async def test_redis_values_are_encrypted_at_rest(fake_redis, fernet_key):
    """A refresh token is a credential to someone else's CRM, in a third party's database."""
    store = RedisConnectionStore(fake_redis, encryption_key=fernet_key)
    await store.put(_connection())
    for key in await fake_redis.keys("*"):
        raw = await fake_redis.dump(key)
        assert REFRESH.encode() not in raw
        assert SUBJECT.encode() not in raw
    await fake_redis.aclose()


async def test_the_file_backend_writes_privately(tmp_path, monkeypatch):
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    await FileConnectionStore().put(_connection())
    path = tmp_path / "connections" / f"{subject_key(SUBJECT)}.json"
    assert path.stat().st_mode & 0o077 == 0
    assert json.loads(path.read_text())["portal_id"] == PORTAL


# --------------------------------------------------------------------------- #
# Token lifecycle
# --------------------------------------------------------------------------- #


def test_a_fresh_token_is_not_expired():
    assert _connection(expires_at=time.time() + 3600).is_expired() is False


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"expires_at": time.time() - 1}, "past expiry"),
        ({"expires_at": time.time() + 60}, "inside the refresh leeway"),
        ({"access_token": None}, "no access token yet"),
        ({"expires_at": None}, "unknown expiry"),
    ],
)
def test_needs_refresh(overrides, reason):
    """One unnecessary refresh beats one 401 in the middle of a write."""
    assert _connection(**overrides).is_expired() is True, reason


def test_with_tokens_keeps_the_refresh_token_when_hubspot_does_not_rotate_it():
    updated = _connection().with_tokens(access_token="new", expires_at=time.time() + 1800)
    assert updated.refresh_token == REFRESH
    assert updated.access_token == "new"


def test_with_tokens_takes_a_rotated_refresh_token_when_given_one():
    updated = _connection().with_tokens(
        access_token="new", expires_at=time.time() + 1800, refresh_token="rotated"
    )
    assert updated.refresh_token == "rotated"


def test_to_portal_config_matches_what_the_tool_layer_expects():
    config = _connection().to_portal_config()
    assert config.portal_id == PORTAL
    assert config.auth_type == "oauth"
    assert config.refresh_token == REFRESH
    assert config.scopes_granted == ["crm.objects.contacts.read"]


def test_a_record_written_without_optional_fields_still_loads():
    """Adding a field later must not need a migration."""
    loaded = HubSpotConnection.from_dict(
        {"subject": SUBJECT, "portal_id": PORTAL, "refresh_token": REFRESH}
    )
    assert loaded.access_token is None
    assert loaded.scopes_granted == ()
    assert loaded.is_expired() is True


# --------------------------------------------------------------------------- #
# Backend selection
# --------------------------------------------------------------------------- #


def test_redis_url_selects_the_redis_backend(monkeypatch, fernet_key):
    from hubspot_mcp.state import BACKEND_ENV
    from hubspot_mcp.state.connection_store import _build_default_connection_store

    monkeypatch.delenv(BACKEND_ENV, raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv(KEY_ENV, fernet_key)
    assert isinstance(_build_default_connection_store(), RedisConnectionStore)


def test_no_redis_url_selects_the_file_backend(monkeypatch):
    from hubspot_mcp.state import BACKEND_ENV
    from hubspot_mcp.state.connection_store import _build_default_connection_store

    monkeypatch.delenv(BACKEND_ENV, raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert isinstance(_build_default_connection_store(), FileConnectionStore)


def test_set_connection_store_round_trips():
    set_connection_store(None)
    replacement = FileConnectionStore()
    set_connection_store(replacement)
    assert get_connection_store() is replacement
    set_connection_store(None)
