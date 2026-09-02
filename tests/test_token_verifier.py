"""Verifying an MCP client's access token (Phase 3, stage 1).

The hosted server is an OAuth 2.1 resource server: it never mints tokens, it
verifies ones the authorization server issued. Three checks matter and each is
easy to half-do, so every one is exercised here against real signed JWTs rather
than mocks — signature, exact issuer, and audience.

The audience check is the one whose absence is invisible in testing and fatal in
production: without it a token the *same* authorization server minted for a
*different* MCP server is accepted here.
"""
from __future__ import annotations

import time

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa

from hubspot_mcp.auth.token_verifier import (
    ISSUER_ENV,
    JWTVerifier,
)

ISSUER = "https://tolerant-climb-38-staging.authkit.app"
AUDIENCE = "https://mcp.example.com"
OTHER_AUDIENCE = "https://someone-elses-mcp.example.com"
SUBJECT = "user_01HQXZ8P3Q"
JWKS_URI = f"{ISSUER}/oauth2/jwks"


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


@pytest.fixture(scope="module")
def jwks(keypair):
    import json

    from jwt.algorithms import RSAAlgorithm

    _, public = keypair
    entry = json.loads(RSAAlgorithm.to_jwk(public))
    entry.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    return {"keys": [entry]}


def _mint(keypair, *, alg="RS256", key=None, **overrides) -> str:
    private, _ = keypair
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": SUBJECT,
        "exp": int(time.time()) + 600,
        "iat": int(time.time()),
        "scope": "openid profile email",
        "client_id": "client_abc",
    }
    claims.update({k: v for k, v in overrides.items() if v is not _OMIT})
    for k, v in overrides.items():
        if v is _OMIT:
            claims.pop(k, None)
    return jwt.encode(claims, key if key is not None else private, algorithm=alg, headers={"kid": "test-key"})


class _Omit:
    pass


_OMIT = _Omit()


@pytest.fixture
def verifier(jwks):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{ISSUER}/.well-known/oauth-authorization-server").mock(
            return_value=httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": JWKS_URI})
        )
        mock.get(JWKS_URI).mock(return_value=httpx.Response(200, json=jwks))
        yield JWTVerifier(ISSUER, AUDIENCE)


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #


async def test_a_valid_token_verifies(verifier, keypair):
    result = await verifier.verify_token(_mint(keypair))

    assert result is not None
    assert result.subject == SUBJECT
    assert result.client_id == "client_abc"
    assert result.scopes == ["openid", "profile", "email"]
    assert result.resource == AUDIENCE


async def test_the_subject_is_what_the_connection_store_is_keyed_on(verifier, keypair):
    result = await verifier.verify_token(_mint(keypair, sub="auth0|other"))
    assert result is not None and result.subject == "auth0|other"


# --------------------------------------------------------------------------- #
# Audience — the check whose absence is invisible until it matters
# --------------------------------------------------------------------------- #


async def test_a_token_for_another_mcp_server_is_rejected(verifier, keypair):
    """Same issuer, same signature, different resource. Must not be accepted."""
    assert await verifier.verify_token(_mint(keypair, aud=OTHER_AUDIENCE)) is None


async def test_a_token_with_no_audience_is_rejected(verifier, keypair):
    assert await verifier.verify_token(_mint(keypair, aud=_OMIT)) is None


async def test_an_audience_list_containing_us_is_accepted(verifier, keypair):
    """A token may legitimately name several audiences."""
    result = await verifier.verify_token(_mint(keypair, aud=[OTHER_AUDIENCE, AUDIENCE]))
    assert result is not None


# --------------------------------------------------------------------------- #
# Issuer — exact string comparison, per RFC 9207
# --------------------------------------------------------------------------- #


async def test_a_token_from_another_issuer_is_rejected(verifier, keypair):
    assert await verifier.verify_token(_mint(keypair, iss="https://evil.authkit.app")) is None


async def test_a_configured_trailing_slash_does_not_break_verification(jwks, keypair):
    """Dashboards hand out issuers with trailing slashes; tokens carry them without."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{ISSUER}/.well-known/oauth-authorization-server").mock(
            return_value=httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": JWKS_URI})
        )
        mock.get(JWKS_URI).mock(return_value=httpx.Response(200, json=jwks))
        verifier = JWTVerifier(f"{ISSUER}/", f"{AUDIENCE}/")
        assert await verifier.verify_token(_mint(keypair)) is not None


async def test_metadata_naming_a_different_issuer_is_refused(jwks):
    """Pointed at the wrong host: better to fail loudly than trust its keys."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{ISSUER}/.well-known/oauth-authorization-server").mock(
            return_value=httpx.Response(
                200, json={"issuer": "https://impostor.authkit.app", "jwks_uri": JWKS_URI}
            )
        )
        with pytest.raises(ValueError, match="Issuer mismatch"):
            await JWTVerifier(ISSUER, AUDIENCE).discover_jwks_uri()


# --------------------------------------------------------------------------- #
# Signature and algorithm
# --------------------------------------------------------------------------- #


async def test_a_token_signed_by_another_key_is_rejected(verifier):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": SUBJECT, "exp": int(time.time()) + 600},
        other,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    assert await verifier.verify_token(forged) is None


async def test_an_unsigned_token_is_rejected(verifier):
    """`alg: none` is the oldest JWT attack there is."""
    unsigned = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": SUBJECT, "exp": int(time.time()) + 600},
        key="",
        algorithm="none",
    )
    assert await verifier.verify_token(unsigned) is None


async def test_hmac_algorithms_are_not_accepted(verifier, keypair):
    """The JWT confusion attack: sign with the issuer's *public* key as an HMAC secret.

    The public key is, by definition, published. If HS256 were accepted the
    verifier would hand that same public key to an HMAC check and the forgery
    would validate.
    """
    from cryptography.hazmat.primitives import serialization

    _, public = keypair
    public_pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    # Assembled by hand: PyJWT refuses to encode this, but nothing stops an
    # attacker from doing the base64 and the HMAC themselves.
    import base64
    import hmac
    import json as _json

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    header = b64(_json.dumps({"alg": "HS256", "typ": "JWT", "kid": "test-key"}).encode())
    payload = b64(
        _json.dumps(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": SUBJECT, "exp": int(time.time()) + 600}
        ).encode()
    )
    signing_input = header + b"." + payload
    signature = b64(hmac.new(public_pem, signing_input, "sha256").digest())
    forged = (signing_input + b"." + signature).decode()

    assert await verifier.verify_token(forged) is None


def test_only_asymmetric_algorithms_are_allowed():
    from hubspot_mcp.auth.token_verifier import ALLOWED_ALGORITHMS

    assert not any(a.startswith("HS") for a in ALLOWED_ALGORITHMS)
    assert "none" not in [a.lower() for a in ALLOWED_ALGORITHMS]


# --------------------------------------------------------------------------- #
# Expiry and required claims
# --------------------------------------------------------------------------- #


async def test_an_expired_token_is_rejected(verifier, keypair):
    assert await verifier.verify_token(_mint(keypair, exp=int(time.time()) - 60)) is None


async def test_a_token_expiring_within_clock_skew_is_still_accepted(verifier, keypair):
    """A few seconds of drift between us and the issuer must not 401 a user."""
    assert await verifier.verify_token(_mint(keypair, exp=int(time.time()) - 5)) is not None


async def test_a_token_without_a_subject_is_rejected(verifier, keypair):
    """No subject means no way to find whose HubSpot connection this is."""
    assert await verifier.verify_token(_mint(keypair, sub=_OMIT)) is None


async def test_a_token_without_an_expiry_is_rejected(verifier, keypair):
    assert await verifier.verify_token(_mint(keypair, exp=_OMIT)) is None


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c", "Bearer x"])
async def test_malformed_tokens_are_rejected(verifier, token):
    assert await verifier.verify_token(token) is None


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #


async def test_an_unreachable_jwks_returns_none_rather_than_raising(capsys, keypair):
    """A JWKS outage must be a 401, not a 500 with a stack trace.

    The token is well-formed on purpose: a malformed one is rejected before we
    would ever reach for a key, which would not exercise this path.
    """
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{ISSUER}/.well-known/oauth-authorization-server").mock(
            side_effect=httpx.ConnectError("unreachable")
        )
        assert await JWTVerifier(ISSUER, AUDIENCE).verify_token(_mint(keypair)) is None
    assert "token verification unavailable" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_the_issuer_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv(ISSUER_ENV, ISSUER)
    assert JWTVerifier.from_env(AUDIENCE).issuer == ISSUER


def test_a_missing_issuer_is_refused(monkeypatch):
    monkeypatch.delenv(ISSUER_ENV, raising=False)
    with pytest.raises(ValueError, match=ISSUER_ENV):
        JWTVerifier.from_env(AUDIENCE)


@pytest.mark.parametrize("issuer,audience", [("", AUDIENCE), (ISSUER, ""), ("/", AUDIENCE)])
def test_blank_configuration_is_refused(issuer, audience):
    with pytest.raises(ValueError, match="issuer and an audience"):
        JWTVerifier(issuer, audience)
