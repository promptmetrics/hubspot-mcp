"""Per-request bearer authentication for the HTTP transport (Phase 2).

Phase 1 runs over stdio: the transport is a pipe to a process the user started,
so there is nothing to authenticate. Serving the same server over HTTPS puts it
on the open internet, and every request must carry a shared secret.

**Why an ASGI wrapper rather than the SDK's ``token_verifier``.** Both run per
request, so both are correct on that axis — protocol revision 2026-07-28 removed
the ``initialize`` handshake and ``Mcp-Session-Id`` (SEP-2575), so anything
connection-scoped would authorise every later request on that connection for
free, and neither of these is. The deciding factor is what the two *mean*:
``token_verifier`` requires ``AuthSettings``, which makes this an OAuth 2.1
protected resource — it publishes ``/.well-known/oauth-protected-resource`` and
points 401s at an issuer. Phase 2 has no authorization server to point at, so a
client following that metadata would chase a discovery flow that does not exist.
A shared static secret is HTTP-layer authentication and belongs in an ASGI
wrapper, which also lets a 401 be a real HTTP 401. Phase 3 (per-user OAuth) is
where ``token_verifier`` becomes the right seam.

Note that ``Middleware.on_initialize`` — the MCP-protocol middleware hook — is
the thing that genuinely cannot be used, because the handshake it keys off no
longer runs.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from typing import Any

SECRET_ENV = "HUBSPOT_MCP_SERVER_SECRET"  # noqa: S105 — the env var name, not a secret

# 32 characters of a random secret is ~190 bits at base64 and ~128 at hex.
# Below that the endpoint is guessable, and an endpoint that answers to a
# guessable secret is worse than one with no auth at all: it looks protected.
MIN_SECRET_LENGTH = 32

# `/healthz` must answer unauthenticated or a platform health check cannot use
# it. It exposes no portal data — see `server.healthz`.
DEFAULT_PUBLIC_PATHS = frozenset({"/healthz"})

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", ""})

# The ASGI spec's own shapes, matching Starlette's signatures so this wraps a
# Starlette app without a cast.
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_UNAUTHORIZED_BODY = b'{"error":"unauthorized","detail":"A valid bearer token is required."}'


class MissingServerSecret(RuntimeError):
    """Raised when a network-reachable deployment has no configured secret."""


class BearerAuthMiddleware:
    """ASGI middleware requiring ``Authorization: Bearer <secret>`` per request.

    Deliberately a bare ASGI callable rather than a Starlette
    ``BaseHTTPMiddleware``: the latter buffers the response through an
    intermediate stream, which breaks the streaming responses the MCP transport
    relies on.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        secret: str,
        public_paths: Iterable[str] = DEFAULT_PUBLIC_PATHS,
    ) -> None:
        if not secret:
            raise ValueError("BearerAuthMiddleware requires a non-empty secret.")
        self.app = app
        # Compare fixed-width digests rather than the raw tokens:
        # `compare_digest` is constant-time in content but still reveals length,
        # and the length of a shared secret is worth withholding.
        self._expected = hashlib.sha256(secret.encode()).digest()
        self.public_paths = frozenset(public_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # `lifespan` (and anything non-HTTP) carries no credentials and must
        # pass through, or the app never starts.
        if scope["type"] != "http" or scope.get("path") in self.public_paths:
            await self.app(scope, receive, send)
            return

        if not self._authorized(scope):
            await self._reject(send)
            return

        await self.app(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        for name, value in scope.get("headers") or ():
            if name.lower() != b"authorization":
                continue
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() != "bearer":
                return False
            return hmac.compare_digest(hashlib.sha256(token.strip().encode()).digest(), self._expected)
        return False

    async def _reject(self, send: Send) -> None:
        # One response for missing, malformed and wrong tokens alike — a
        # distinguishing message would tell an attacker which half to work on.
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                    (b"content-length", str(len(_UNAUTHORIZED_BODY)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})


def load_server_secret(env: dict[str, str] | None = None) -> str | None:
    """Return the configured shared secret, or ``None`` if unset/blank."""
    return (env if env is not None else os.environ).get(SECRET_ENV, "").strip() or None


def resolve_server_secret(host: str, *, env: dict[str, str] | None = None) -> str | None:
    """Return the secret to guard ``host`` with, failing closed off-loopback.

    A missing secret on a loopback bind is ordinary local development, so it
    warns and returns ``None``. A missing or too-short secret on any other bind
    is a server about to answer the internet unauthenticated, which must not
    start.
    """
    secret = load_server_secret(env)
    loopback = host.lower() in _LOOPBACK_HOSTS

    if secret is None:
        if loopback:
            print(
                f"hubspot_mcp: no {SECRET_ENV} set — serving {host} without authentication. "
                "Loopback only; any non-local bind will refuse to start.",
                file=sys.stderr,
            )
            return None
        raise MissingServerSecret(
            f"Refusing to serve on {host} without authentication. "
            f"Set {SECRET_ENV} to a random string of at least {MIN_SECRET_LENGTH} characters "
            f"(e.g. `openssl rand -base64 32`)."
        )

    if len(secret) < MIN_SECRET_LENGTH:
        raise MissingServerSecret(
            f"{SECRET_ENV} is {len(secret)} characters; at least {MIN_SECRET_LENGTH} are required. "
            "A guessable secret on a public endpoint is worse than none — it looks protected."
        )
    return secret
