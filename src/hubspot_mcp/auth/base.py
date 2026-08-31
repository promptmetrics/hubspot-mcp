"""Token-provider interface for the HubSpot MCP server.

A ``TokenProvider`` resolves the active portal's :class:`PortalConfig` (with a
usable access token) for the server lifespan. The provider does *not* run an
interactive auth flow — that lives in the ``hubspot-mcp auth`` subcommand. When
a portal has not yet been authenticated, ``resolve`` raises
:class:`NotAuthenticatedError` so the MCP server fails fast with actionable
guidance instead of blocking the stdio lifespan handshake.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from hubspot_mcp.config import PortalConfig


class NotAuthenticatedError(RuntimeError):
    """Raised when no usable token can be resolved for the requested portal."""


class TokenProvider(ABC):
    """Resolves a :class:`PortalConfig` for the active portal.

    Implementations are async because OAuth refresh (when needed inline) is a
    network call. Phase 1 ships two: :class:`~hubspot_mcp.auth.env_provider.EnvTokenProvider`
    (private-app token) and :class:`~hubspot_mcp.auth.oauth_provider.OAuthProvider`
    (bring-your-own-app OAuth, the default).
    """

    @property
    @abstractmethod
    def mode(self) -> str:
        """Auth mode label — ``"token"`` (PAT) or ``"oauth"``."""

    @abstractmethod
    async def resolve(self, portal_id: str) -> PortalConfig:
        """Return a :class:`PortalConfig` with a usable token, or raise."""