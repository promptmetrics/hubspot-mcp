"""Connecting a HubSpot account to an authenticated user (Phase 3, hosted path).

The awkwardness this solves: authorising HubSpot is a *browser* journey, but the
person's identity is established by an *MCP* access token that a browser will
never carry. So the MCP side mints a one-time **ticket** bound to the caller's
subject and hands back a link; opening that link is what proves, to the browser
half, who is connecting.

    tool call ──► issue_ticket(subject) ──► /connect/hubspot?ticket=…
                                                  │ redeem, bind state
                                                  ▼
                                          HubSpot consent screen
                                                  │
                                     /connect/hubspot/callback?code&state
                                                  │ exchange, resolve hub id
                                                  ▼
                                          connection stored for subject

Both the ticket and the OAuth ``state`` are single-use, short-lived, and stored
under a digest of themselves so a dump of the backing store yields nothing
usable. Neither is ever logged.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from typing import Any

from hubspot_mcp.state.cache_store import CacheStore, get_cache_store
from hubspot_mcp.state.connection_store import (
    ConnectionStore,
    HubSpotConnection,
    get_connection_store,
)

PUBLIC_URL_ENV = "HUBSPOT_MCP_PUBLIC_URL"

# Long enough to walk through HubSpot's consent screen, short enough that an
# abandoned link is not a standing credential.
TICKET_TTL_SECONDS = 600
STATE_TTL_SECONDS = 600

# Our own minted values, so unlike an identity subject these can be validated
# strictly — anything else did not come from us.
_OPAQUE_RE = re.compile(r"[A-Za-z0-9_-]{16,128}")

_TICKET_CACHE = "connect-ticket"
_STATE_CACHE = "connect-state"


class ConnectError(Exception):
    """A connect attempt could not proceed. Message is safe to show a user."""


@dataclass(frozen=True)
class ConnectFlow:
    """The browser half of hosted HubSpot authorisation."""

    scopes: tuple[str, ...]
    public_url: str
    cache: CacheStore | None = None
    connections: ConnectionStore | None = None

    @classmethod
    def from_env(cls, scopes: list[str] | None = None) -> ConnectFlow:
        public_url = os.environ.get(PUBLIC_URL_ENV, "").strip().rstrip("/")
        if not public_url:
            raise ConnectError(
                f"{PUBLIC_URL_ENV} is not set, so the HubSpot redirect URI cannot be built. "
                "It must match the redirect URI registered on the HubSpot app exactly."
            )
        if scopes is None:
            from hubspot_mcp.scope_registry import authorize_scopes

            scopes = list(authorize_scopes())
        return cls(scopes=tuple(scopes), public_url=public_url)

    # --- plumbing ---------------------------------------------------------

    @property
    def _cache(self) -> CacheStore:
        return self.cache if self.cache is not None else get_cache_store()

    @property
    def _connections(self) -> ConnectionStore:
        return self.connections if self.connections is not None else get_connection_store()

    @property
    def redirect_uri(self) -> str:
        # Server-configured, never taken from the request: a caller-supplied
        # redirect_uri is how an OAuth flow becomes an open redirect.
        return f"{self.public_url}/connect/hubspot/callback"

    @staticmethod
    def _entry_name(prefix: str, value: str) -> str:
        # Store under a digest of the secret, not the secret: a dump of the
        # backing store then yields nothing that can be replayed.
        return f"{prefix}:{hashlib.sha256(value.encode()).hexdigest()}"

    async def _take(self, prefix: str, value: str) -> dict[str, Any] | None:
        """Read and consume a single-use entry."""
        if not _OPAQUE_RE.fullmatch(value or ""):
            return None
        name = self._entry_name(prefix, value)
        found = await self._cache.get(None, name)
        if found is not None:
            await self._cache.delete(None, name)
        return found

    # --- the flow ---------------------------------------------------------

    async def issue_ticket(self, subject: str) -> str:
        """Mint a one-time link that lets ``subject`` connect HubSpot in a browser."""
        if not subject:
            raise ConnectError("Cannot issue a connect link without an authenticated user.")
        ticket = secrets.token_urlsafe(32)
        await self._cache.set(
            None,
            self._entry_name(_TICKET_CACHE, ticket),
            {"subject": subject},
            ttl_seconds=TICKET_TTL_SECONDS,
        )
        return f"{self.public_url}/connect/hubspot?ticket={ticket}"

    async def begin(self, ticket: str) -> str:
        """Redeem a ticket and return the HubSpot authorize URL to redirect to."""
        entry = await self._take(_TICKET_CACHE, ticket)
        if entry is None:
            raise ConnectError(
                "This connect link has expired or was already used. "
                "Run the connect tool again for a fresh one."
            )

        from hubspot_mcp.app_credentials import get_client_id, get_oauth_endpoints
        from hubspot_mcp.oauth_flow import _build_code_challenge, _build_code_verifier

        client_id = get_client_id()
        if not client_id:
            raise ConnectError("The HubSpot app is not configured on this server.")

        state = secrets.token_urlsafe(32)
        verifier = _build_code_verifier()
        await self._cache.set(
            None,
            self._entry_name(_STATE_CACHE, state),
            {"subject": entry["subject"], "code_verifier": verifier},
            ttl_seconds=STATE_TTL_SECONDS,
        )

        import urllib.parse

        authorize_url, _ = get_oauth_endpoints()
        params = {
            "client_id": client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scopes),
            "response_type": "code",
            "state": state,
            "code_challenge": _build_code_challenge(verifier),
            "code_challenge_method": "S256",
        }
        return f"{authorize_url}?{urllib.parse.urlencode(params)}"

    async def complete(self, code: str, state: str) -> HubSpotConnection:
        """Exchange the authorization code and store the connection."""
        entry = await self._take(_STATE_CACHE, state)
        if entry is None:
            # Covers a forged state, a replayed one, and an expired one. They are
            # not distinguished: telling an attacker which they got is free help.
            raise ConnectError(
                "This authorisation could not be verified — it may have expired. "
                "Run the connect tool again to start over."
            )
        if not code:
            raise ConnectError("HubSpot did not return an authorization code.")

        import time

        from hubspot_mcp.oauth_flow import exchange_code_only, resolve_hub_id

        body = await exchange_code_only(code, self.redirect_uri, entry["code_verifier"])
        connection = HubSpotConnection(
            subject=entry["subject"],
            portal_id=await resolve_hub_id(body),
            refresh_token=body["refresh_token"],
            access_token=body["access_token"],
            expires_at=time.time() + body.get("expires_in", 21600),
            scopes_granted=tuple((body.get("scope") or "").split()),
        )
        await self._connections.put(connection)
        return connection
