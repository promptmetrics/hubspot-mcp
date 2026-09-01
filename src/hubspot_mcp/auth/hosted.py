"""Resolve a HubSpot session for an authenticated user (Phase 3, hosted path).

Phase 1's providers answer "which portal is this *process* configured for?".
This one answers "which portal did *this caller* authorise?", by looking the
subject up in the connection store and refreshing their access token when it is
close to expiry.

It is deliberately not a :class:`~hubspot_mcp.auth.base.TokenProvider`. That
interface resolves by ``portal_id``, which on a hosted deployment is an output
of authentication, not an input to it — accepting a portal id from the caller
would let anyone name someone else's portal.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict

import httpx

from hubspot_mcp.config import PortalConfig
from hubspot_mcp.state.connection_store import (
    ConnectionStore,
    HubSpotConnection,
    get_connection_store,
)

# Same buffer the local path uses, so a token is never handed out with only
# seconds left on it.
REFRESH_LEEWAY_SECONDS = 300.0


class NotConnectedError(Exception):
    """The caller has no usable HubSpot connection.

    ``reconnect_required`` distinguishes "HubSpot has definitively rejected this
    grant, the user must authorise again" from "we could not reach HubSpot just
    now". Telling someone to redo an OAuth flow because of a 503 wastes their
    time and, if they do it, silently rotates a working credential.
    """

    def __init__(self, message: str, *, reconnect_required: bool = True) -> None:
        super().__init__(message)
        self.reconnect_required = reconnect_required


class HostedOAuthProvider:
    """Resolve the caller's :class:`PortalConfig` from their stored connection."""

    def __init__(self, store: ConnectionStore | None = None) -> None:
        self._store = store
        # One refresh per subject at a time. Two concurrent tool calls whose
        # token has just aged out would otherwise both refresh, and if HubSpot
        # rotates the refresh token the loser writes back a dead one.
        #
        # In-process only: it does not coordinate across instances. That race
        # stays possible on a multi-instance deployment and is accepted for now
        # — the window is the few hundred milliseconds of a refresh, against a
        # 300s leeway. Revisit with a Redis lock if it ever shows up.
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @property
    def store(self) -> ConnectionStore:
        return self._store if self._store is not None else get_connection_store()

    async def resolve(self, subject: str) -> PortalConfig:
        """Return a ready-to-use ``PortalConfig`` for ``subject``.

        Raises :class:`NotConnectedError` if they have not connected HubSpot, or
        if their grant is no longer usable.
        """
        connection = await self.store.get(subject)
        if connection is None:
            raise NotConnectedError("No HubSpot account is connected for this user.")

        if not connection.is_expired(leeway_seconds=REFRESH_LEEWAY_SECONDS):
            return connection.to_portal_config()

        async with self._locks[subject]:
            # Re-read under the lock: another coroutine may have refreshed while
            # we waited, and refreshing again would rotate a token needlessly.
            current = await self.store.get(subject)
            if current is None:
                raise NotConnectedError("No HubSpot account is connected for this user.")
            if not current.is_expired(leeway_seconds=REFRESH_LEEWAY_SECONDS):
                return current.to_portal_config()
            refreshed = await self._refresh(current)
        return refreshed.to_portal_config()

    async def _refresh(self, connection: HubSpotConnection) -> HubSpotConnection:
        from hubspot_mcp.oauth_flow import refresh_tokens_only

        try:
            body = await refresh_tokens_only(connection.refresh_token)
        except httpx.HTTPStatusError as exc:
            raise self._refusal(exc) from exc
        except httpx.HTTPError as exc:
            # Network-level: never definitive.
            raise NotConnectedError(
                f"Could not reach HubSpot to refresh this connection: {exc}",
                reconnect_required=False,
            ) from exc

        # `scopes_granted` is deliberately untouched: a refresh response may omit
        # `scope`, and overwriting with an empty set would make the scope-gap
        # report lie.
        updated = connection.with_tokens(
            access_token=body["access_token"],
            expires_at=time.time() + body.get("expires_in", 21600),
            refresh_token=body.get("refresh_token"),
        )
        await self.store.put(updated)
        return updated

    @staticmethod
    def _refusal(exc: httpx.HTTPStatusError) -> NotConnectedError:
        """Classify a refresh failure, conclusive vs transient.

        The same distinction the capability prober draws: 4xx is HubSpot telling
        us the grant is gone, 5xx and 429 are HubSpot having a bad minute. Only
        the first should send a user back through authorisation.
        """
        status = exc.response.status_code
        if status in (400, 401, 403) or status == 404:
            return NotConnectedError(
                "This HubSpot connection is no longer valid — it may have been revoked "
                "in HubSpot, or the app uninstalled. Reconnect to continue.",
                reconnect_required=True,
            )
        return NotConnectedError(
            f"HubSpot could not refresh this connection right now (HTTP {status}). "
            "This is usually temporary; try again shortly.",
            reconnect_required=False,
        )
