"""Token providers for the HubSpot MCP server.

`TokenProvider` (base) resolves a `PortalConfig` for the active portal. Two
Phase 1 implementations: `EnvTokenProvider` (private-app token fallback) and
`OAuthProvider` (bring-your-own-app OAuth, default). Both feed the same
`HubSpotClient`; tool bodies never read `os.environ` directly.
"""
from __future__ import annotations

from hubspot_mcp.auth.base import TokenProvider
from hubspot_mcp.auth.env_provider import EnvTokenProvider
from hubspot_mcp.auth.oauth_provider import OAuthProvider

__all__ = ["TokenProvider", "EnvTokenProvider", "OAuthProvider"]