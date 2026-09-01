"""Redis-backed :class:`StateStore` — the hosted deployment's store.

On a serverless host any request can land on a different instance than the one
before it, so the write-safety state machine cannot live on local disk: a
preview minted while serving one request would be invisible to the approve that
follows it. This store moves pending previews, undo snapshots and the audit log
to Redis, which is what makes the server-minted-handle pattern (SEP-2567)
survive real infrastructure.

Provider-agnostic on purpose. It speaks the Redis protocol via ``redis-py`` and
reads a single ``REDIS_URL``, which is what every Vercel Marketplace Redis
integration injects — there is no first-party Vercel Redis, and picking a vendor
should not be a code change.

**Everything is encrypted before it leaves the process.** Pending previews and
undo snapshots carry HubSpot record properties — names, emails, deal
amounts — and they are about to sit in a third party's database. The audit log
is redacted by :func:`redact_dict_for_disk` first, exactly as the file store
does, and then encrypted like everything else.
"""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from redis.asyncio import Redis
from redis.exceptions import WatchError

from hubspot_mcp.persistence import is_valid_action_id
from hubspot_mcp.redaction import redact_dict_for_disk
from hubspot_mcp.snapshot import build_undo_snapshot
from hubspot_mcp.state.base import StateStore

# `redis` and `cryptography` are the `[redis]` extra, not core dependencies.
# Nothing imports this module unless the Redis backend is selected, so the
# stdio plugin path never pays for them — see `hubspot_mcp.state.get_store`.

URL_ENV = "REDIS_URL"
KEY_ENV = "HUBSPOT_MCP_STATE_KEY"  # noqa: S105 — the env var name, not a secret

# Matches the file store's `reap_expired` default, so a preview does not outlive
# its local-path equivalent just because it moved to Redis.
PENDING_TTL_SECONDS = 24 * 60 * 60
# Undo has to outlive the approve that created it by enough to be useful the
# next morning, but a snapshot holds pre-change record values and should not
# accumulate indefinitely.
SNAPSHOT_TTL_SECONDS = 7 * 24 * 60 * 60
# The file store's audit log grows without bound; a Redis list must not.
AUDIT_MAX_ENTRIES = 1000

_NAMESPACE = "hubspot-mcp"


class StateEncryptionUnavailable(RuntimeError):
    """Raised when the Redis store has no usable encryption key."""


def _fernet(key: str | None = None) -> Fernet:
    """Build the Fernet cipher, failing closed when no key is configured."""
    raw = (key if key is not None else os.environ.get(KEY_ENV, "")).strip()
    if not raw:
        raise StateEncryptionUnavailable(
            f"{KEY_ENV} is not set. Pending previews and undo snapshots carry HubSpot "
            "record properties and must not be written to a third-party store in the "
            "clear. Generate one with:\n"
            "  python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(raw.encode())
    except Exception as exc:
        raise StateEncryptionUnavailable(
            f"{KEY_ENV} is not a valid Fernet key (expected 32 url-safe base64-encoded bytes)."
        ) from exc


class RedisStateStore(StateStore):
    """Portal-keyed state store over any Redis-protocol server."""

    def __init__(self, client: Redis, *, encryption_key: str | None = None) -> None:
        self._redis = client
        self._cipher = _fernet(encryption_key)

    @classmethod
    def from_url(cls, url: str | None = None, **kwargs: Any) -> RedisStateStore:
        resolved = url or os.environ.get(URL_ENV, "")
        if not resolved:
            raise RuntimeError(f"{URL_ENV} is not set; cannot build a RedisStateStore.")
        # decode_responses stays False: every value is a Fernet token, i.e. bytes.
        return cls(Redis.from_url(resolved, decode_responses=False), **kwargs)

    # --- keys and codec ---------------------------------------------------

    def _pending_key(self, portal_id: str, action_id: str) -> str:
        return f"{_NAMESPACE}:{portal_id}:pending:{action_id}"

    def _pending_index(self, portal_id: str) -> str:
        return f"{_NAMESPACE}:{portal_id}:pending-index"

    def _snapshot_key(self, portal_id: str, action_id: str) -> str:
        return f"{_NAMESPACE}:{portal_id}:undo:{action_id}"

    def _audit_key(self, portal_id: str) -> str:
        return f"{_NAMESPACE}:{portal_id}:audit"

    def _encode(self, payload: dict[str, Any]) -> bytes:
        return self._cipher.encrypt(json.dumps(payload).encode())

    def _decode(self, raw: bytes | str | None) -> dict[str, Any] | None:
        # redis-py types its return as `bytes | str` because `decode_responses`
        # is a runtime setting; we build the client with it off, so this is
        # always bytes in practice.
        if raw is None:
            return None
        try:
            return json.loads(self._cipher.decrypt(raw))
        except (InvalidToken, ValueError):
            # A rotated key or a corrupt value must read as "not found", never
            # as a crash on the approve path — the write simply cannot be
            # approved, which is the safe direction.
            return None

    # --- pending previews -------------------------------------------------

    async def store_pending(self, portal_id: str, action_id: str, data: dict[str, Any]) -> None:
        if not is_valid_action_id(action_id):
            raise ValueError(f"Invalid action_id: {action_id!r}")
        # The file store stamps this server-side; the client's clock is not
        # evidence of when we accepted the preview.
        data["_stored_at"] = _utc_now_iso()
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(self._pending_key(portal_id, action_id), self._encode(data), ex=PENDING_TTL_SECONDS)
            pipe.zadd(self._pending_index(portal_id), {action_id: time.time()})
            await pipe.execute()

    async def load_pending(self, portal_id: str, action_id: str) -> dict[str, Any] | None:
        if not is_valid_action_id(action_id):
            return None
        return self._decode(await self._redis.get(self._pending_key(portal_id, action_id)))

    async def confirm_pending(self, portal_id: str, action_id: str, count: int) -> bool:
        if not is_valid_action_id(action_id):
            return False
        key = self._pending_key(portal_id, action_id)

        # Read-modify-write under WATCH. This is the destructive-action count
        # gate, so two concurrent approves must not both observe an
        # unconfirmed preview and both write a confirmation.
        async with self._redis.pipeline(transaction=True) as pipe:
            while True:
                try:
                    await pipe.watch(key)
                    data = self._decode(await pipe.get(key))
                    if data is None:
                        return False
                    required = data.get("required_confirmation")
                    if required is None or int(required) != int(count):
                        return False
                    data["confirmed_count"] = count
                    ttl = await pipe.ttl(key)
                    pipe.multi()
                    pipe.set(key, self._encode(data), ex=ttl if ttl and ttl > 0 else PENDING_TTL_SECONDS)
                    await pipe.execute()
                    return True
                except WatchError:
                    continue

    async def clear_pending(self, portal_id: str, action_id: str) -> None:
        if not is_valid_action_id(action_id):
            return
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(self._pending_key(portal_id, action_id))
            pipe.zrem(self._pending_index(portal_id), action_id)
            await pipe.execute()

    async def list_pending(self, portal_id: str) -> list[str]:
        index = self._pending_index(portal_id)
        members = await self._redis.zrevrange(index, 0, -1)
        if not members:
            return []
        ids = [m.decode() if isinstance(m, bytes) else str(m) for m in members]

        async with self._redis.pipeline(transaction=False) as pipe:
            for action_id in ids:
                pipe.exists(self._pending_key(portal_id, action_id))
            present = await pipe.execute()

        live = [aid for aid, exists in zip(ids, present, strict=True) if exists]
        # Entries whose value hit its TTL leave the index behind; prune them so
        # the index cannot grow without bound on a busy portal.
        if stale := [aid for aid, exists in zip(ids, present, strict=True) if not exists]:
            await self._redis.zrem(index, *stale)
        return live

    # --- undo snapshots ---------------------------------------------------

    async def save_undo_snapshot_for_action(
        self, portal_id: str, action_id: str, preview_data: dict[str, Any]
    ) -> None:
        original_values, metadata = build_undo_snapshot(preview_data)
        await self.save_undo_snapshot(portal_id, action_id, original_values, metadata)

    async def save_undo_snapshot(
        self,
        portal_id: str,
        action_id: str,
        original_values: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not is_valid_action_id(action_id):
            raise ValueError(f"Invalid action_id: {action_id!r}")
        payload: dict[str, Any] = {"action_id": action_id, "original_values": original_values}
        if metadata:
            payload["metadata"] = metadata
        await self._redis.set(
            self._snapshot_key(portal_id, action_id), self._encode(payload), ex=SNAPSHOT_TTL_SECONDS
        )

    async def load_undo_snapshot(self, portal_id: str, action_id: str) -> dict[str, Any] | None:
        if not is_valid_action_id(action_id):
            return None
        return self._decode(await self._redis.get(self._snapshot_key(portal_id, action_id)))

    async def update_undo_snapshot(
        self, portal_id: str, action_id: str, *, metadata: dict[str, Any] | None = None
    ) -> None:
        if not is_valid_action_id(action_id):
            return
        key = self._snapshot_key(portal_id, action_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            while True:
                try:
                    await pipe.watch(key)
                    payload = self._decode(await pipe.get(key))
                    if payload is None:
                        # Matches the file store: updating a snapshot that is
                        # not there is a no-op, not an error.
                        return
                    if metadata is not None:
                        payload.setdefault("metadata", {}).update(metadata)
                    ttl = await pipe.ttl(key)
                    pipe.multi()
                    pipe.set(key, self._encode(payload), ex=ttl if ttl and ttl > 0 else SNAPSHOT_TTL_SECONDS)
                    await pipe.execute()
                    return
                except WatchError:
                    continue

    async def delete_undo_snapshot(self, portal_id: str, action_id: str) -> None:
        if not is_valid_action_id(action_id):
            return
        await self._redis.delete(self._snapshot_key(portal_id, action_id))

    # --- audit log --------------------------------------------------------

    async def log_write(
        self,
        portal_id: str,
        *,
        action: str,
        agent: str,
        result_summary: dict[str, Any],
        informing_sources: list[dict[str, Any]] | None = None,
    ) -> None:
        entry = {
            "timestamp": _utc_now_iso(),
            "action": action,
            "agent": agent,
            "result_summary": result_summary,
            "informing_sources": informing_sources or [],
        }
        # Redaction first, exactly as the file store does it. Moving the audit
        # log to Redis must not quietly un-redact it.
        key = self._audit_key(portal_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.lpush(key, self._encode(redact_dict_for_disk(entry)))
            pipe.ltrim(key, 0, AUDIT_MAX_ENTRIES - 1)
            await pipe.execute()

    async def get_recent_audits(self, portal_id: str, limit: int = 50) -> list[dict[str, Any]]:
        raw = await self._redis.lrange(self._audit_key(portal_id), 0, max(limit, 0) - 1)
        # LPUSH puts newest at the head; the file store returns the most recent
        # `limit` in chronological order, so reverse to match.
        decoded = [self._decode(item) for item in raw]
        return [entry for entry in reversed(decoded) if entry is not None]


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
