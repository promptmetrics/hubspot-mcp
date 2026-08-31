"""Bearer-token middleware — Phase 2 stub.

Phase 1 authenticates per-portal via :class:`TokenProvider` (PAT or
bring-your-own-app OAuth) and the ``HubSpotClient`` injects the bearer token
per request. This module is the extension point for Phase 2's managed-host
deployment, where a single bearer token (issued by the host) is attached to
every outbound HubSpot call regardless of portal — a reverse-proxy-style auth
layer rather than per-portal credential files.

Intentionally not implemented in Phase 1; left as a documented placeholder so
the Phase 2 wiring has a known home.

**Phase 2 must verify per request, not per connection.** Protocol revision
2026-07-28 removes the ``initialize``/``notifications/initialized`` handshake
and the ``Mcp-Session-Id`` header entirely (SEP-2575), so there is no
connection-establishment phase to authenticate in and no session identity to
carry a prior decision forward. Anything that authenticates once and then
trusts the connection would authorise every later request on that connection
for free. Verify the inbound token on each request instead — either as ASGI
middleware in front of ``MCPServer.streamable_http_app()``, or via the SDK's
``token_verifier``/``auth`` constructor parameters, which run per request by
construction and are the preferred route.
"""
from __future__ import annotations


class BearerMiddleware:
    """Phase 2 placeholder — attaches a host-issued bearer token to requests."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: D401, ANN002, ANN003
        raise NotImplementedError(
            "BearerMiddleware is a Phase 2 stub. Phase 1 uses TokenProvider "
            "(EnvTokenProvider / OAuthProvider) + HubSpotClient per-portal auth."
        )
