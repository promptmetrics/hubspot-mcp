"""Turn a verified access token into the caller's HubSpot session (Phase 3).

This is the resolver behind :func:`hubspot_mcp.server.set_session_resolver`, and
it is what makes all 86 tools act on the portal the *caller* authorised rather
than one the process was configured with.

    verified token ──► subject ──► their connection ──► PortalConfig ──► client

Two things here are less obvious than the flow:

**Clients are pooled per subject, not built per request.**
:class:`~hubspot_mcp.client.HubSpotClient` opens an ``httpx.AsyncClient`` in its
constructor and must be closed; building one per request and dropping it leaks a
connection pool every time, which looks fine in testing and degrades a live
deployment over hours. Pooling by *subject* rather than by portal matters
because two people may authorise the same portal — a shared client would refresh
one person's grant into the other's record.

**A missing connection is not an error.** Someone who has authenticated but not
yet connected HubSpot gets a link, through the same ``auth_error`` channel the
stdio path uses for an unauthenticated portal.
"""
from __future__ import annotations

import asyncio
import sys
import time
from collections import OrderedDict
from typing import Any

from hubspot_mcp.auth.hosted import HostedOAuthProvider, NotConnectedError
from hubspot_mcp.cache import SchemaCache
from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig

# Bounded so a busy deployment cannot accumulate connection pools without limit.
# Eviction closes the client, so the cost of overflow is a reconnect, not a leak.
MAX_POOLED_CLIENTS = 32


class ClientPool:
    """Least-recently-used pool of per-subject HubSpot clients."""

    def __init__(self, max_clients: int = MAX_POOLED_CLIENTS) -> None:
        self._clients: OrderedDict[str, HubSpotClient] = OrderedDict()
        self._max = max_clients
        self._lock = asyncio.Lock()

    async def acquire(self, subject: str, portal: PortalConfig) -> HubSpotClient:
        """Return this subject's client, carrying their current credentials."""
        async with self._lock:
            existing = self._clients.get(subject)
            if existing is not None:
                self._clients.move_to_end(subject)
                # The provider has already resolved (and possibly refreshed)
                # this token; hand it over without discarding the connections.
                existing.update_credentials(portal)
                return existing

            client = HubSpotClient(portal, token_refresher=_refresher_for(subject))
            self._clients[subject] = client
            evicted = []
            while len(self._clients) > self._max:
                _, victim = self._clients.popitem(last=False)
                evicted.append(victim)

        # Closing outside the lock: an eviction must not stall other callers.
        for victim in evicted:
            await _close_quietly(victim)
        return client

    async def close_all(self) -> None:
        async with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            await _close_quietly(client)

    def __len__(self) -> int:
        return len(self._clients)


async def _close_quietly(client: HubSpotClient) -> None:
    try:
        await client.close()
    except Exception as exc:  # noqa: BLE001 — teardown must never mask the caller's result
        print(f"hubspot_mcp: closing a pooled client failed: {exc}", file=sys.stderr)


def _refresher_for(subject: str):
    """Persist a client-initiated refresh to the *connection store*.

    Rarely reached — the provider refreshes on resolve, so a pooled client is
    handed a token with the full leeway remaining. It has to be right anyway:
    the default refresher writes a local portal file, which on a hosted
    deployment stores this user's grant somewhere nobody reads.
    """

    async def refresh(portal: PortalConfig) -> dict[str, Any]:
        provider = HostedOAuthProvider()
        connection = await provider.store.get(subject)
        if connection is None:
            raise NotConnectedError("No HubSpot account is connected for this user.")
        updated = await provider.refresh(connection)
        return {
            "access_token": updated.access_token,
            "refresh_token": updated.refresh_token,
            "expires_in": int((updated.expires_at or 0) - time.time()),
        }

    return refresh


def _unresolved(reason: str) -> dict[str, Any]:
    """A session that answers `tools/list` but cannot act.

    The same shape the stdio path yields when a portal is unauthenticated, so
    tool bodies need no new branch — under 2026-07-28 there is no handshake in
    which to fail instead.
    """
    return {
        "client": None,
        "cache": None,
        "portal_config": None,
        "portal_id": None,
        "auth_error": reason,
        "capabilities": None,
    }


def build_session_resolver(pool: ClientPool | None = None):
    """Return the resolver to install with ``server.set_session_resolver``."""
    clients = pool if pool is not None else ClientPool()

    async def resolve(ctx: Any) -> dict[str, Any]:
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
        if token is None or not token.subject:
            # The transport should already have rejected this; belt and braces
            # so a misconfiguration cannot silently serve an anonymous caller.
            return _unresolved("Not authenticated.")

        subject = token.subject
        try:
            portal_config = await HostedOAuthProvider().resolve(subject)
        except NotConnectedError as exc:
            return _unresolved(await _connect_guidance(subject, exc))

        client = await clients.acquire(subject, portal_config)
        capabilities = await _capabilities(portal_config)
        return {
            "client": client,
            # Not `warm_standard_schemas`: that makes several HubSpot calls, and
            # per request it would be a tax on every tool call. The cache warms
            # on demand via `ensure_custom_schema_cached`.
            "cache": SchemaCache(portal_config.portal_id),
            "portal_config": portal_config,
            "portal_id": portal_config.portal_id,
            "auth_error": None,
            "capabilities": capabilities,
        }

    resolve.pool = clients  # type: ignore[attr-defined]
    return resolve


async def _capabilities(portal_config: PortalConfig):
    """Entitlements for this portal, from the shared cache where possible."""
    from hubspot_mcp.capabilities import probe_portal

    try:
        return await probe_portal(portal_config)
    except Exception as exc:  # noqa: BLE001 — entitlements must never fail a call
        print(f"hubspot_mcp: capability probe failed: {exc}", file=sys.stderr)
        return None


async def _connect_guidance(subject: str, exc: NotConnectedError) -> str:
    """Explain what to do, with a link where one will actually help."""
    if not exc.reconnect_required:
        # Transient: a connect link would have them re-authorise for nothing.
        return str(exc)
    try:
        from hubspot_mcp.auth.connect import ConnectFlow

        link = await ConnectFlow.from_env().issue_ticket(subject)
    except Exception as link_exc:  # noqa: BLE001 — guidance must not become a 500
        print(f"hubspot_mcp: could not mint a connect link: {link_exc}", file=sys.stderr)
        return f"{exc} Ask an administrator for a HubSpot connect link."
    return f"{exc} Connect your HubSpot account here: {link}"
