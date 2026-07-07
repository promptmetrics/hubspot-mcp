"""Private-app (PAT) token provider — the Phase 1 fallback auth mode.

Reads a portal token from the same sources the plugin used
(``~/.claude/hubspot/<portal>.json`` → ``HUBSPOT_TOKEN_<portal>`` env →
``<portal>.token`` file) via :func:`load_portal_config`. No refresh, no
client_secret — a private-app token is long-lived. Use this when the operator
cannot create a HubSpot app for OAuth; OAuth (bring-your-own-app) is preferred.
"""
from __future__ import annotations

from hubspot_mcp.auth.base import NotAuthenticatedError, TokenProvider
from hubspot_mcp.config import PortalConfig, load_portal_config


class EnvTokenProvider(TokenProvider):
    """PAT provider: surface whatever ``load_portal_config`` finds."""

    @property
    def mode(self) -> str:
        return "token"

    async def resolve(self, portal_id: str) -> PortalConfig:
        portal = load_portal_config(portal_id)
        if portal is None or not portal.token:
            raise NotAuthenticatedError(
                f"No private-app token found for portal {portal_id}. "
                f"Set HUBSPOT_TOKEN_{portal_id} or create "
                f"~/.claude/hubspot/{portal_id}.json (run `hubspot-mcp auth login "
                f"--mode token --portal {portal_id}`)."
            )
        return portal