"""Bring-your-own-app OAuth provider — Phase 1 default auth mode.

``OAuthProvider.resolve`` returns the persisted OAuth portal config; the
``HubSpotClient`` refreshes the access token in place when it nears expiry
(via :func:`refresh_access_token`). The interactive authorize flow (localhost
callback server + browser) is run by :func:`run_oauth_login`, invoked from the
``hubspot-mcp auth login`` subcommand — not from the MCP lifespan — so a missing
portal fails fast with guidance instead of blocking the stdio handshake.

Scope handling is *exact-set*: the operator lists the precise scopes (via
``--scopes`` or ``HUBSPOT_SCOPES``) and HubSpot is asked for exactly that set;
no implicit scope expansion.
"""
from __future__ import annotations

import asyncio
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from hubspot_mcp.auth.base import NotAuthenticatedError, TokenProvider
from hubspot_mcp.config import PortalConfig, load_portal_config
from hubspot_mcp.oauth_flow import exchange_code_for_token, get_authorization_url

_DEFAULT_REDIRECT_PORT = 3000
_CALLBACK_TIMEOUT_SECONDS = 300.0


class OAuthProvider(TokenProvider):
    """Resolve-side OAuth provider (refresh is handled by ``HubSpotClient``)."""

    @property
    def mode(self) -> str:
        return "oauth"

    async def resolve(self, portal_id: str) -> PortalConfig:
        portal = load_portal_config(portal_id)
        if portal is None or portal.auth_type != "oauth" or not portal.token:
            raise NotAuthenticatedError(
                f"Portal {portal_id} is not OAuth-authenticated. "
                f"Run `hubspot-mcp auth login --mode oauth --portal {portal_id}` "
                f"to start the OAuth flow."
            )
        return portal


def _resolve_scopes(scopes: list[str] | None) -> list[str]:
    """Exact-scope-set resolution: explicit arg wins, then HUBSPOT_SCOPES env."""
    if scopes:
        return scopes
    env = os.getenv("HUBSPOT_SCOPES", "").strip()
    if env:
        return [s.strip() for s in env.split(",") if s.strip()]
    raise ValueError(
        "No OAuth scopes provided. Pass --scopes or set the HUBSPOT_SCOPES env "
        "var with the exact comma-separated scope set."
    )


class _CallbackCapture:
    """Minimal HTTP server capturing the OAuth redirect's ``code``/``state``."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.code: str | None = None
        self.state: str | None = None
        self.error: str | None = None
        self._done = threading.Event()
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        capture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence stderr noise
                return

            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/oauth/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                params = urllib.parse.parse_qs(parsed.query)
                if params.get("error"):
                    capture.error = params["error"][0]
                else:
                    capture.code = params.get("code", [None])[0]
                    capture.state = params.get("state", [None])[0]
                capture._done.set()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<h1>HubSpot MCP</h1>"
                    b"<p>Authorization received. You can close this tab.</p>"
                )

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def wait(self, timeout: float) -> bool:
        return self._done.wait(timeout=timeout)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


async def run_oauth_login(
    portal_id: str,
    scopes: list[str] | None = None,
    *,
    port: int = _DEFAULT_REDIRECT_PORT,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Run the interactive OAuth authorize flow and persist tokens.

    Starts a localhost callback server on ``port``, builds the region-aware
    authorize URL for the exact scope set, opens a browser, waits for the
    redirect, and exchanges the code (PKCE) via the 2026-03 endpoint (with the
    legacy v3 fallback baked into :func:`exchange_code_for_token`). Tokens are
    persisted by ``_save_oauth_tokens`` → ``save_portal_config``.
    """
    resolved_scopes = _resolve_scopes(scopes)
    redirect_uri = f"http://localhost:{port}/oauth/callback"
    url = get_authorization_url(portal_id, resolved_scopes, redirect_uri)

    capture = _CallbackCapture(port)
    capture.start()
    try:
        if open_browser:
            import webbrowser

            webbrowser.open(url)
        else:
            print(f"Open this URL to authorize:\n{url}\n")

        if not await asyncio.to_thread(capture.wait, _CALLBACK_TIMEOUT_SECONDS):
            raise TimeoutError(
                f"OAuth callback timed out after {_CALLBACK_TIMEOUT_SECONDS:.0f}s "
                f"with no response from HubSpot."
            )
        if capture.error:
            raise RuntimeError(f"OAuth provider returned error: {capture.error}")
        if not capture.code or not capture.state:
            raise RuntimeError("OAuth callback was missing code or state.")

        return await exchange_code_for_token(portal_id, capture.code, capture.state, redirect_uri)
    finally:
        capture.stop()