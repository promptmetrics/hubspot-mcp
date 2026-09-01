"""Per-user HubSpot connections — the bridge between an identity and a portal.

Phase 3 has two OAuth relationships. Inbound, an MCP client authenticates to us
and we verify a token whose `subject` identifies the person. Outbound, we hold
the PromptMetrics HubSpot public app's credentials and act on *their* portal.
This store is what joins the two: `subject` in, `HubSpotConnection` out.

It lives beside `StateStore` and `CacheStore` rather than in `auth/` because it
is storage, and because the Redis implementation shares their cipher and key
namespace. Its failure semantics match `StateStore`, not `CacheStore`: a
connection cannot be reconstructed by refetching, so a backend failure must
surface rather than read as "not connected" — the latter would tell a user to
reconnect an account they already have.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import Any

from hubspot_mcp.config import PortalConfig

__all__ = [
    "HubSpotConnection",
    "ConnectionStore",
    "FileConnectionStore",
    "get_connection_store",
    "set_connection_store",
    "subject_key",
]


def subject_key(subject: str) -> str:
    """Return the storage key for an identity subject.

    Hashed rather than validated. Subjects are minted by an external identity
    provider and their format is not ours to constrain — `auth0|abc`,
    `user_01H...`, an email — so a validating regex risks locking out a real
    user, while passing the raw value into a Redis key or a file path invites
    injection and traversal. A digest is injection-proof by construction for
    any input, and keeps the subject itself out of the keyspace.
    """
    if not subject:
        raise ValueError("subject must not be empty")
    return hashlib.sha256(subject.encode()).hexdigest()


@dataclass(frozen=True)
class HubSpotConnection:
    """One person's authorised HubSpot portal.

    ``refresh_token`` is a long-lived credential to someone else's CRM, so
    :meth:`__repr__` is overridden — an unredacted dataclass repr puts it in
    every traceback and log line that touches this object.
    """

    subject: str
    portal_id: str
    refresh_token: str
    access_token: str | None = None
    expires_at: float | None = None
    scopes_granted: tuple[str, ...] = ()
    connected_at: float = field(default_factory=time.time)

    def __repr__(self) -> str:
        return (
            f"HubSpotConnection(subject={self.subject!r}, portal_id={self.portal_id!r}, "
            f"refresh_token=<redacted>, access_token={'<redacted>' if self.access_token else None}, "
            f"expires_at={self.expires_at!r}, scopes_granted={self.scopes_granted!r})"
        )

    def is_expired(self, *, leeway_seconds: float = 300.0) -> bool:
        """Whether the access token needs refreshing.

        Treats a missing token or a missing expiry as expired: the safe
        direction is one unnecessary refresh, not one 401 mid-write.
        """
        if not self.access_token or self.expires_at is None:
            return True
        return time.time() >= self.expires_at - leeway_seconds

    def to_portal_config(self) -> PortalConfig:
        """Adapt to the shape the client, handlers and tools already take."""
        return PortalConfig(
            portal_id=self.portal_id,
            token=self.access_token or "",
            scopes_granted=list(self.scopes_granted),
            auth_type="oauth",
            refresh_token=self.refresh_token,
            expires_at=self.expires_at,
        )

    def with_tokens(
        self, *, access_token: str, expires_at: float, refresh_token: str | None = None
    ) -> HubSpotConnection:
        """Return a copy carrying freshly refreshed tokens.

        ``refresh_token`` is optional because HubSpot does not always rotate it;
        omitting it keeps the existing one rather than blanking it.
        """
        return replace(
            self,
            access_token=access_token,
            expires_at=expires_at,
            refresh_token=refresh_token or self.refresh_token,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "portal_id": self.portal_id,
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
            "expires_at": self.expires_at,
            "scopes_granted": list(self.scopes_granted),
            "connected_at": self.connected_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HubSpotConnection:
        # `.get` on everything optional: a record written by an older version
        # must still load. Adding a field later needs no migration.
        return cls(
            subject=data["subject"],
            portal_id=data["portal_id"],
            refresh_token=data["refresh_token"],
            access_token=data.get("access_token"),
            expires_at=data.get("expires_at"),
            scopes_granted=tuple(data.get("scopes_granted") or ()),
            connected_at=data.get("connected_at") or time.time(),
        )


class ConnectionStore(ABC):
    """Storage for per-user HubSpot connections."""

    @abstractmethod
    async def get(self, subject: str) -> HubSpotConnection | None:
        """Return the subject's connection, or ``None`` if they have not connected."""

    @abstractmethod
    async def put(self, connection: HubSpotConnection) -> None:
        """Store (or replace) a subject's connection."""

    @abstractmethod
    async def delete(self, subject: str) -> None:
        """Forget a subject's connection. Disconnecting must actually disconnect."""


class FileConnectionStore(ConnectionStore):
    """Local-disk connections, for development and self-hosting.

    Written 0600 from birth via :func:`write_private_json`, matching how the
    local path already stores portal tokens. Not encrypted at rest — on the
    user's own machine the file mode is the boundary, exactly as for
    ``~/.claude/hubspot/<portal>.json``. The Redis store, which puts the same
    bytes in a third party's database, does encrypt.
    """

    def _path(self, subject: str):
        from hubspot_mcp.config import CONFIG_DIR

        return CONFIG_DIR / "connections" / f"{subject_key(subject)}.json"

    async def get(self, subject: str) -> HubSpotConnection | None:
        import asyncio

        return await asyncio.to_thread(self._get_sync, subject)

    def _get_sync(self, subject: str) -> HubSpotConnection | None:
        try:
            data = json.loads(self._path(subject).read_text())
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            # Unlike a cache, this must not read as "not connected" — that
            # would tell the user to reconnect an account they already have.
            raise ConnectionUnreadable(f"connection for this user could not be read: {exc}") from exc
        return HubSpotConnection.from_dict(data)

    async def put(self, connection: HubSpotConnection) -> None:
        import asyncio

        await asyncio.to_thread(self._put_sync, connection)

    def _put_sync(self, connection: HubSpotConnection) -> None:
        from hubspot_mcp.fileio import write_private_json

        write_private_json(self._path(connection.subject), connection.to_dict())

    async def delete(self, subject: str) -> None:
        import asyncio

        await asyncio.to_thread(self._delete_sync, subject)

    def _delete_sync(self, subject: str) -> None:
        self._path(subject).unlink(missing_ok=True)


class ConnectionUnreadable(RuntimeError):
    """Raised when a connection exists but cannot be read."""


_connection_store: ConnectionStore | None = None


def _build_default_connection_store() -> ConnectionStore:
    """Follow the same backend selection as the state and cache stores."""
    from hubspot_mcp.state import BACKEND_ENV

    backend = os.environ.get(BACKEND_ENV, "").strip().lower()
    if backend == "file":
        return FileConnectionStore()
    if backend == "redis" or (not backend and os.environ.get("REDIS_URL", "").strip()):
        from hubspot_mcp.state.redis_store import RedisConnectionStore

        return RedisConnectionStore.from_url()
    return FileConnectionStore()


def get_connection_store() -> ConnectionStore:
    global _connection_store
    if _connection_store is None:
        _connection_store = _build_default_connection_store()
    return _connection_store


def set_connection_store(store: ConnectionStore | None) -> None:
    """Install a connection store, or reset to the default with ``None``."""
    global _connection_store
    _connection_store = store
