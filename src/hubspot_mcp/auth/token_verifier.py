"""Verify the access tokens an MCP client presents (Phase 3, hosted path).

The hosted server is an OAuth 2.1 **resource server**. It never mints tokens; it
verifies ones an external authorization server issued, and it must satisfy three
requirements from MCP revision `2026-07-28` that are easy to half-do:

1. **Signature**, against the issuer's published JWKS.
2. **Issuer**, matched exactly. RFC 9207 comparison is simple string comparison,
   so a trailing slash is a different issuer.
3. **Audience** — the token must have been minted *for this server*. Without
   this check a token the same authorization server issued for a *different* MCP
   server is accepted here. An agent moves between many servers in one session,
   which is exactly the confused-deputy case the requirement exists to prevent.

Every failure returns ``None`` rather than raising: the SDK turns that into the
401 the spec requires, and distinguishing "expired" from "wrong audience" from
"bad signature" in a response only helps whoever is probing.
"""
from __future__ import annotations

import sys
from typing import Any

import httpx
import jwt
from mcp.server.auth.provider import AccessToken

ISSUER_ENV = "HUBSPOT_MCP_OAUTH_ISSUER"

# Asymmetric only. Allowing an HMAC algorithm here is the classic JWT confusion
# attack: the issuer's *public* key is public, so an attacker could sign their
# own token with it and have it verify. `none` is excluded for the obvious
# reason.
ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")

# Enough to absorb clock skew between us and the authorization server, not
# enough to meaningfully extend a token's life.
CLOCK_SKEW_LEEWAY_SECONDS = 30


class JWTVerifier:
    """A :class:`mcp.server.auth.provider.TokenVerifier` over a JWKS endpoint."""

    def __init__(
        self,
        issuer: str,
        audience: str,
        *,
        jwks_uri: str | None = None,
        algorithms: tuple[str, ...] = ALLOWED_ALGORITHMS,
    ) -> None:
        # Normalise our own configuration once, so a trailing slash pasted from
        # a dashboard cannot silently reject every token. What we must NOT do is
        # normalise the *token's* claims before comparing.
        self.issuer = issuer.rstrip("/")
        self.audience = audience.rstrip("/")
        if not self.issuer or not self.audience:
            raise ValueError("JWTVerifier requires both an issuer and an audience.")
        self._jwks_uri = jwks_uri
        self._algorithms = list(algorithms)
        self._jwks: jwt.PyJWKSet | None = None

    @classmethod
    def from_env(cls, audience: str, env: dict[str, str] | None = None) -> JWTVerifier:
        import os

        source = env if env is not None else os.environ
        issuer = source.get(ISSUER_ENV, "").strip()
        if not issuer:
            raise ValueError(
                f"{ISSUER_ENV} is not set. It is the authorization server's issuer URL, "
                "e.g. https://your-project.authkit.app"
            )
        return cls(issuer, audience)

    async def discover_jwks_uri(self) -> str:
        """Resolve the JWKS endpoint from the issuer's metadata, once."""
        if self._jwks_uri:
            return self._jwks_uri
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.issuer}/.well-known/oauth-authorization-server")
            resp.raise_for_status()
            metadata = resp.json()

        # The metadata's own `issuer` is authoritative; if it disagrees with what
        # we were configured with, we are pointed at the wrong server and must
        # not proceed.
        published = str(metadata.get("issuer", "")).rstrip("/")
        if published != self.issuer:
            raise ValueError(
                f"Issuer mismatch: configured {self.issuer!r}, but that host publishes "
                f"{published!r}."
            )
        jwks_uri = metadata.get("jwks_uri")
        if not jwks_uri:
            raise ValueError(f"{self.issuer} publishes no jwks_uri.")
        self._jwks_uri = str(jwks_uri)
        return self._jwks_uri

    async def _signing_key(self, token: str) -> Any:
        """Return the verification key for ``token``, refetching on rotation.

        The JWKS is fetched with ``httpx`` rather than ``jwt.PyJWKClient``,
        which uses a blocking ``urllib`` call — wrong on an event loop, and it
        bypasses the HTTP layer the rest of this codebase tests against.
        """
        kid = jwt.get_unverified_header(token).get("kid")
        keys = self._jwks or await self._fetch_jwks()
        try:
            return self._select(keys, kid)
        except KeyError:
            # Unknown kid: the issuer has rotated. Refetch once — this is what
            # makes key rotation a non-event rather than an outage.
            return self._select(await self._fetch_jwks(), kid)

    async def _fetch_jwks(self) -> jwt.PyJWKSet:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(await self.discover_jwks_uri())
            resp.raise_for_status()
            self._jwks = jwt.PyJWKSet.from_dict(resp.json())
        return self._jwks

    @staticmethod
    def _select(keys: jwt.PyJWKSet, kid: str | None) -> Any:
        for key in keys.keys:
            if kid is None or key.key_id == kid:
                return key.key
        raise KeyError(kid)

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return the verified token, or ``None`` for anything not usable."""
        if not token:
            return None
        try:
            signing_key = await self._signing_key(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key,
                algorithms=self._algorithms,
                audience=self.audience,
                issuer=self.issuer,
                leeway=CLOCK_SKEW_LEEWAY_SECONDS,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError:
            # Expired, wrong audience, wrong issuer, bad signature, missing
            # claim — all one answer to the caller.
            return None
        except Exception as exc:  # noqa: BLE001 — a JWKS fetch failure must not 500
            print(f"hubspot_mcp: token verification unavailable: {exc}", file=sys.stderr)
            return None

        scope = claims.get("scope") or ""
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or claims.get("azp") or ""),
            scopes=scope.split() if isinstance(scope, str) else list(scope),
            expires_at=int(claims["exp"]),
            resource=self.audience,
            subject=str(claims["sub"]),
            claims=claims,
        )
