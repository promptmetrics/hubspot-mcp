"""One contract, both stores (Phase 2, Task 2).

Every test here runs twice: once against `FileStateStore` on a tmp directory,
once against `RedisStateStore` on fakeredis. That is the whole point — a remote
store is a drop-in only if it is held to the same behavioural contract as the
one the 671 other tests were written against, and the differences that matter
(server-stamped timestamps, audit redaction, action-id validation, the
confirm-count gate) are invisible in a per-implementation test.

Implementation-specific behaviour lives at the bottom, clearly marked.
"""
from __future__ import annotations

import pytest

from hubspot_mcp.state import FileStateStore
from hubspot_mcp.state.redis_store import (
    AUDIT_MAX_ENTRIES,
    KEY_ENV,
    PENDING_TTL_SECONDS,
    RedisStateStore,
    StateEncryptionUnavailable,
)

PORTAL = "99999999"
OTHER_PORTAL = "11111111"


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
    """The same contract, over local disk and over Redis."""
    if request.param == "file":
        from pathlib import Path

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("hubspot_mcp.persistence.CONFIG_DIR", tmp_path)
        yield FileStateStore()
    else:
        yield RedisStateStore(fake_redis, encryption_key=fernet_key)
        await fake_redis.aclose()


def _preview(**overrides):
    data = {
        "tool_name": "hubspot_update_object",
        "request_text": "set lifecycle stage",
        "intent": {"intent_type": "update", "target_object": "contacts"},
        "preview": {"original_values": {"1": {"jobtitle": "Analyst"}}},
        "required_confirmation": 0,
    }
    data.update(overrides)
    return data


# --------------------------------------------------------------------------- #
# Pending previews
# --------------------------------------------------------------------------- #


async def test_store_then_load_round_trips(store):
    await store.store_pending(PORTAL, "act-1", _preview())
    loaded = await store.load_pending(PORTAL, "act-1")
    assert loaded is not None
    assert loaded["tool_name"] == "hubspot_update_object"
    assert loaded["intent"]["target_object"] == "contacts"


async def test_missing_preview_reads_as_none(store):
    assert await store.load_pending(PORTAL, "never-stored") is None


async def test_store_stamps_a_server_side_timestamp(store):
    """The client's clock is not evidence of when we accepted the preview."""
    await store.store_pending(PORTAL, "act-1", _preview())
    loaded = await store.load_pending(PORTAL, "act-1")
    assert loaded is not None
    assert loaded["_stored_at"].startswith("20")


async def test_clear_removes_the_preview(store):
    await store.store_pending(PORTAL, "act-1", _preview())
    await store.clear_pending(PORTAL, "act-1")
    assert await store.load_pending(PORTAL, "act-1") is None
    assert await store.list_pending(PORTAL) == []


async def test_clearing_a_missing_preview_is_a_noop(store):
    await store.clear_pending(PORTAL, "never-stored")


async def test_portals_are_isolated(store):
    """One portal's pending write must never be visible to another."""
    await store.store_pending(PORTAL, "act-1", _preview())
    assert await store.load_pending(OTHER_PORTAL, "act-1") is None
    assert await store.list_pending(OTHER_PORTAL) == []


async def test_list_pending_returns_action_ids(store):
    await store.store_pending(PORTAL, "act-1", _preview())
    await store.store_pending(PORTAL, "act-2", _preview())
    listed = await store.list_pending(PORTAL)
    assert sorted(listed) == ["act-1", "act-2"]
    assert all(isinstance(entry, str) for entry in listed)


async def test_list_pending_is_empty_for_an_unused_portal(store):
    assert await store.list_pending(PORTAL) == []


# --------------------------------------------------------------------------- #
# The confirm-count gate
# --------------------------------------------------------------------------- #


async def test_confirm_accepts_the_matching_count(store):
    await store.store_pending(PORTAL, "act-1", _preview(required_confirmation=3))
    assert await store.confirm_pending(PORTAL, "act-1", 3) is True
    loaded = await store.load_pending(PORTAL, "act-1")
    assert loaded is not None and loaded["confirmed_count"] == 3


@pytest.mark.parametrize("count", [2, 4, 0])
async def test_confirm_rejects_a_wrong_count(store, count):
    await store.store_pending(PORTAL, "act-1", _preview(required_confirmation=3))
    assert await store.confirm_pending(PORTAL, "act-1", count) is False
    loaded = await store.load_pending(PORTAL, "act-1")
    assert loaded is not None and "confirmed_count" not in loaded


async def test_confirm_on_a_missing_preview_is_false(store):
    assert await store.confirm_pending(PORTAL, "never-stored", 1) is False


async def test_confirm_is_false_when_no_confirmation_is_required(store):
    await store.store_pending(PORTAL, "act-1", _preview(required_confirmation=None))
    assert await store.confirm_pending(PORTAL, "act-1", 1) is False


# --------------------------------------------------------------------------- #
# Action-id validation — a path traversal on disk, a key injection in Redis
# --------------------------------------------------------------------------- #

TRAVERSAL_IDS = ["../escape", "a/b", "", "x" * 65, "act 1", "act:1", "*"]


@pytest.mark.parametrize("action_id", TRAVERSAL_IDS)
async def test_unsafe_action_ids_never_resolve(store, action_id):
    assert await store.load_pending(PORTAL, action_id) is None
    assert await store.load_undo_snapshot(PORTAL, action_id) is None
    assert await store.confirm_pending(PORTAL, action_id, 1) is False
    # Clear and delete must behave as not-found, not raise.
    await store.clear_pending(PORTAL, action_id)
    await store.delete_undo_snapshot(PORTAL, action_id)


@pytest.mark.parametrize("action_id", ["../escape", "a/b", "act:1"])
async def test_storing_an_unsafe_action_id_raises(store, action_id):
    """Ids are self-minted, so a bad one here is a bug, not user input."""
    with pytest.raises(ValueError, match="Invalid action_id"):
        await store.store_pending(PORTAL, action_id, _preview())


# --------------------------------------------------------------------------- #
# Undo snapshots
# --------------------------------------------------------------------------- #


async def test_snapshot_round_trips(store):
    await store.save_undo_snapshot(
        PORTAL, "act-1", {"1": {"jobtitle": "Analyst"}}, {"intent_type": "update", "undoable": True}
    )
    snap = await store.load_undo_snapshot(PORTAL, "act-1")
    assert snap is not None
    assert snap["original_values"] == {"1": {"jobtitle": "Analyst"}}
    assert snap["metadata"]["undoable"] is True


async def test_snapshot_for_action_derives_undoability(store):
    await store.save_undo_snapshot_for_action(PORTAL, "act-1", _preview())
    snap = await store.load_undo_snapshot(PORTAL, "act-1")
    assert snap is not None
    assert snap["metadata"] == {
        "intent_type": "update",
        "target_object": "contacts",
        "undoable": True,
    }


async def test_an_update_with_no_captured_originals_is_not_undoable(store):
    """The false-undo-promise bug: never claim undoability we cannot deliver."""
    await store.save_undo_snapshot_for_action(
        PORTAL, "act-1", _preview(preview={"original_values": {}})
    )
    snap = await store.load_undo_snapshot(PORTAL, "act-1")
    assert snap is not None
    assert snap["metadata"]["undoable"] is False


async def test_a_delete_is_never_undoable(store):
    await store.save_undo_snapshot_for_action(
        PORTAL,
        "act-1",
        _preview(intent={"intent_type": "delete", "target_object": "contacts"}),
    )
    snap = await store.load_undo_snapshot(PORTAL, "act-1")
    assert snap is not None
    assert snap["metadata"]["undoable"] is False


async def test_update_snapshot_merges_metadata(store):
    await store.save_undo_snapshot(PORTAL, "act-1", {}, {"intent_type": "create"})
    await store.update_undo_snapshot(PORTAL, "act-1", metadata={"created_ids": ["501"]})
    snap = await store.load_undo_snapshot(PORTAL, "act-1")
    assert snap is not None
    assert snap["metadata"] == {"intent_type": "create", "created_ids": ["501"]}


async def test_updating_a_missing_snapshot_is_a_noop(store):
    await store.update_undo_snapshot(PORTAL, "never-stored", metadata={"created_ids": ["1"]})
    assert await store.load_undo_snapshot(PORTAL, "never-stored") is None


async def test_delete_removes_the_snapshot(store):
    await store.save_undo_snapshot(PORTAL, "act-1", {"1": {}}, None)
    await store.delete_undo_snapshot(PORTAL, "act-1")
    assert await store.load_undo_snapshot(PORTAL, "act-1") is None


async def test_snapshots_are_portal_scoped(store):
    await store.save_undo_snapshot(PORTAL, "act-1", {"1": {}}, None)
    assert await store.load_undo_snapshot(OTHER_PORTAL, "act-1") is None


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #


async def test_audit_round_trips(store):
    await store.log_write(PORTAL, action="approve:act-1", agent="tool", result_summary={"status": "success"})
    audits = await store.get_recent_audits(PORTAL)
    assert len(audits) == 1
    assert audits[0]["action"] == "approve:act-1"
    assert audits[0]["agent"] == "tool"
    assert audits[0]["timestamp"].startswith("20")


async def test_audit_is_empty_for_an_unused_portal(store):
    assert await store.get_recent_audits(PORTAL) == []


async def test_audit_returns_the_most_recent_entries_chronologically(store):
    for i in range(5):
        await store.log_write(PORTAL, action=f"action_{i}", agent="tool", result_summary={})
    audits = await store.get_recent_audits(PORTAL, limit=3)
    assert [a["action"] for a in audits] == ["action_2", "action_3", "action_4"]


async def test_audit_carries_informing_sources(store):
    sources = [{"source": "official", "trust_tier": "official", "url": "https://developers.hubspot.com/docs"}]
    await store.log_write(
        PORTAL, action="a", agent="tool", result_summary={}, informing_sources=sources
    )
    audits = await store.get_recent_audits(PORTAL)
    assert audits[0]["informing_sources"] == sources


async def test_audit_is_redacted_before_storage(store):
    """Moving the audit log off local disk must not quietly un-redact it.

    `redact_dict_for_disk` at the "pii" level masks emails, phone numbers and
    name-like strings. It does NOT redact by key name, so a credential placed
    in `result_summary` would survive — no caller does that today (the summary
    is request text plus a status), but it is worth knowing the boundary.
    """
    await store.log_write(
        PORTAL,
        action="approve:act-1",
        agent="tool",
        result_summary={"request": "update ada@example.com", "status": "success"},
    )
    audits = await store.get_recent_audits(PORTAL)
    assert "ada@example.com" not in str(audits[0])
    assert "<email:" in audits[0]["result_summary"]["request"]
    assert audits[0]["result_summary"]["status"] == "success"


async def test_audits_are_portal_scoped(store):
    await store.log_write(PORTAL, action="a", agent="tool", result_summary={})
    assert await store.get_recent_audits(OTHER_PORTAL) == []


# --------------------------------------------------------------------------- #
# Redis-specific: encryption, TTLs, index hygiene
# --------------------------------------------------------------------------- #


async def test_redis_values_are_encrypted_at_rest(fake_redis, fernet_key):
    """Previews carry contact names and emails into a third party's database."""
    store = RedisStateStore(fake_redis, encryption_key=fernet_key)
    await store.store_pending(PORTAL, "act-1", _preview(request_text="email ada@example.com"))
    await store.save_undo_snapshot(PORTAL, "act-1", {"1": {"email": "ada@example.com"}}, None)
    await store.log_write(PORTAL, action="a", agent="tool", result_summary={"email": "ada@example.com"})

    for key in await fake_redis.keys("*"):
        raw = await fake_redis.dump(key)
        assert b"ada@example.com" not in raw, f"{key!r} holds plaintext"
        assert b"hubspot_update_object" not in raw
    await fake_redis.aclose()


async def test_redis_refuses_to_start_without_an_encryption_key(fake_redis, monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(StateEncryptionUnavailable, match=KEY_ENV):
        RedisStateStore(fake_redis)
    await fake_redis.aclose()


async def test_redis_rejects_a_malformed_encryption_key(fake_redis):
    with pytest.raises(StateEncryptionUnavailable, match="not a valid Fernet key"):
        RedisStateStore(fake_redis, encryption_key="not-a-real-key")
    await fake_redis.aclose()


async def test_a_value_written_under_another_key_reads_as_not_found(fake_redis, fernet_key):
    """A rotated key must not crash the approve path — it must fail closed."""
    from cryptography.fernet import Fernet

    writer = RedisStateStore(fake_redis, encryption_key=fernet_key)
    await writer.store_pending(PORTAL, "act-1", _preview())

    reader = RedisStateStore(fake_redis, encryption_key=Fernet.generate_key().decode())
    assert await reader.load_pending(PORTAL, "act-1") is None
    await fake_redis.aclose()


async def test_pending_previews_expire(fake_redis, fernet_key):
    store = RedisStateStore(fake_redis, encryption_key=fernet_key)
    await store.store_pending(PORTAL, "act-1", _preview())
    ttl = await fake_redis.ttl(store._pending_key(PORTAL, "act-1"))
    assert 0 < ttl <= PENDING_TTL_SECONDS
    await fake_redis.aclose()


async def test_an_expired_preview_is_pruned_from_the_index(fake_redis, fernet_key):
    """TTL removes the value but not the index entry; list_pending self-heals."""
    store = RedisStateStore(fake_redis, encryption_key=fernet_key)
    await store.store_pending(PORTAL, "act-1", _preview())
    await store.store_pending(PORTAL, "act-2", _preview())
    await fake_redis.delete(store._pending_key(PORTAL, "act-1"))

    assert await store.list_pending(PORTAL) == ["act-2"]
    assert await fake_redis.zcard(store._pending_index(PORTAL)) == 1
    await fake_redis.aclose()


async def test_the_audit_list_is_capped(fake_redis, fernet_key):
    store = RedisStateStore(fake_redis, encryption_key=fernet_key)
    for i in range(AUDIT_MAX_ENTRIES + 10):
        await store.log_write(PORTAL, action=f"a{i}", agent="tool", result_summary={})
    assert await fake_redis.llen(store._audit_key(PORTAL)) == AUDIT_MAX_ENTRIES
    await fake_redis.aclose()


# --------------------------------------------------------------------------- #
# Statelessness: the property Phase 2 exists to preserve
# --------------------------------------------------------------------------- #


async def test_a_preview_minted_by_one_instance_is_approvable_by_another(fake_redis, fernet_key):
    """The decisive property for a multi-instance host.

    2026-07-28 has no session (SEP-2575), so `action_id` is a server-minted
    handle passed as an ordinary tool argument. On Vercel the approve almost
    certainly lands on a different instance than the preview did — two store
    objects, no shared memory, only Redis between them.
    """
    minting_instance = RedisStateStore(fake_redis, encryption_key=fernet_key)
    await minting_instance.store_pending(PORTAL, "act-1", _preview(required_confirmation=2))

    approving_instance = RedisStateStore(fake_redis, encryption_key=fernet_key)
    loaded = await approving_instance.load_pending(PORTAL, "act-1")
    assert loaded is not None
    assert await approving_instance.confirm_pending(PORTAL, "act-1", 2) is True

    await approving_instance.save_undo_snapshot_for_action(PORTAL, "act-1", loaded)
    await approving_instance.clear_pending(PORTAL, "act-1")
    await approving_instance.log_write(
        PORTAL, action="approve:act-1", agent="tool", result_summary={"status": "success"}
    )

    # A third instance can still undo and read the audit trail.
    undoing_instance = RedisStateStore(fake_redis, encryption_key=fernet_key)
    assert await undoing_instance.load_undo_snapshot(PORTAL, "act-1") is not None
    assert (await undoing_instance.get_recent_audits(PORTAL))[0]["action"] == "approve:act-1"
    await fake_redis.aclose()


# --------------------------------------------------------------------------- #
# Backend selection
# --------------------------------------------------------------------------- #


def test_redis_url_alone_selects_the_redis_backend(monkeypatch, fernet_key):
    """Forgetting a second variable would silently serve from local disk."""
    from hubspot_mcp.state import BACKEND_ENV, _build_default_store

    monkeypatch.delenv(BACKEND_ENV, raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv(KEY_ENV, fernet_key)
    assert isinstance(_build_default_store(), RedisStateStore)


def test_no_redis_url_selects_the_file_backend(monkeypatch):
    from hubspot_mcp.state import BACKEND_ENV, FileStateStore, _build_default_store

    monkeypatch.delenv(BACKEND_ENV, raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert isinstance(_build_default_store(), FileStateStore)


def test_the_backend_can_be_forced_to_file(monkeypatch, fernet_key):
    from hubspot_mcp.state import BACKEND_ENV, FileStateStore, _build_default_store

    monkeypatch.setenv(BACKEND_ENV, "file")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv(KEY_ENV, fernet_key)
    assert isinstance(_build_default_store(), FileStateStore)


def test_an_unknown_backend_is_refused(monkeypatch):
    from hubspot_mcp.state import BACKEND_ENV, _build_default_store

    monkeypatch.setenv(BACKEND_ENV, "postgres")
    with pytest.raises(ValueError, match="not a known backend"):
        _build_default_store()


def test_redis_backend_without_a_url_is_refused(monkeypatch):
    from hubspot_mcp.state import BACKEND_ENV, _build_default_store

    monkeypatch.setenv(BACKEND_ENV, "redis")
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        _build_default_store()


def test_the_stdio_path_never_imports_redis():
    """Selecting the file backend must not import redis, even though it is installed."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import hubspot_mcp.server; "
            "print(any(m == 'redis' or m.startswith('redis.') for m in sys.modules))",
        ],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "HUBSPOT_PORTAL": "99999999"},
        check=True,
    )
    assert result.stdout.strip() == "False", "importing the server pulls in the redis extra"
