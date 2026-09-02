"""The hosted server as an OAuth 2.1 resource server (Phase 3, stage 1).

Configuration decides which of three postures the server takes:

| `HUBSPOT_MCP_OAUTH_ISSUER` | `HUBSPOT_MCP_SERVER_SECRET` | Posture |
|---|---|---|
| set | ignored | per-request OAuth, protected-resource metadata published |
| unset | set | shared-secret bearer (self-hosting) |
| unset | unset | unauthenticated, loopback binds only |

Hosted mode is exercised in a subprocess because the auth settings are resolved
when `server` is imported — reloading the module mid-test would re-register 86
tools and 44 prompts, which is a worse test than a clean interpreter.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

ISSUER = "https://tolerant-climb-38-staging.authkit.app"
PUBLIC_URL = "https://hubspot-mcp-promptmetrics.vercel.app"
RESOURCE = f"{PUBLIC_URL}/mcp"
PUBLIC_BIND = "0.0.0.0"  # noqa: S104 — the subject of these tests


def _in_hosted_server(code: str, **env: str) -> str:
    """Run `code` in a fresh interpreter with the server imported.

    `HUBSPOT_PORTAL` is set only when hosted OAuth is off: with it on, an
    ambient portal is a startup failure by design (see
    `tenancy.enforce_no_ambient_portal`).
    """
    base = {"PATH": "/usr/bin:/bin", "HOME": "/tmp"}
    if env.get("HUBSPOT_MCP_OAUTH_ISSUER", "").strip():
        # Hosted also requires durable state; never connected to, only checked for.
        base["REDIS_URL"] = "redis://localhost:6379/0"
    else:
        base["HUBSPOT_PORTAL"] = "99999999"
    result = subprocess.run(
        [sys.executable, "-c", "import hubspot_mcp.server as s\n" + code],
        capture_output=True,
        text=True,
        env={**base, **env},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# --------------------------------------------------------------------------- #
# The resource identifier
# --------------------------------------------------------------------------- #


def test_the_resource_identifier_is_the_public_url_plus_the_mount_path():
    """It must match what the client sends and what the issuer stamps as `aud`."""
    out = _in_hosted_server(
        "print(str(s._AUTH_SETTINGS.resource_server_url))",
        HUBSPOT_MCP_OAUTH_ISSUER=ISSUER,
        HUBSPOT_MCP_PUBLIC_URL=PUBLIC_URL,
    )
    assert out == RESOURCE


def test_a_trailing_slash_on_the_public_url_does_not_change_the_resource():
    out = _in_hosted_server(
        "print(str(s._AUTH_SETTINGS.resource_server_url))",
        HUBSPOT_MCP_OAUTH_ISSUER=ISSUER,
        HUBSPOT_MCP_PUBLIC_URL=f"{PUBLIC_URL}/",
    )
    assert out == RESOURCE


def test_the_verifier_and_the_settings_agree():
    """A mismatch here would 401 every request, with nothing obviously wrong."""
    out = _in_hosted_server(
        "print(s._TOKEN_VERIFIER.issuer, s._TOKEN_VERIFIER.audience, "
        "str(s._AUTH_SETTINGS.issuer_url).rstrip('/'), str(s._AUTH_SETTINGS.resource_server_url))",
        HUBSPOT_MCP_OAUTH_ISSUER=ISSUER,
        HUBSPOT_MCP_PUBLIC_URL=PUBLIC_URL,
    )
    v_iss, v_aud, s_iss, s_res = out.split()
    assert v_iss == s_iss == ISSUER
    assert v_aud == s_res == RESOURCE


def test_the_mount_path_is_the_one_in_the_resource_identifier():
    out = _in_hosted_server(
        "print(s.MCP_PATH, str(s._AUTH_SETTINGS.resource_server_url))",
        HUBSPOT_MCP_OAUTH_ISSUER=ISSUER,
        HUBSPOT_MCP_PUBLIC_URL=PUBLIC_URL,
    )
    path, resource = out.split()
    assert resource.endswith(path)


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_the_protected_resource_metadata_route_is_published():
    """RFC 9728 is how a client discovers where to authenticate. It is a MUST."""
    out = _in_hosted_server(
        "app = s.build_http_app('0.0.0.0')\n"
        "print([r.path for r in app.routes if 'well-known' in r.path])",
        HUBSPOT_MCP_OAUTH_ISSUER=ISSUER,
        HUBSPOT_MCP_PUBLIC_URL=PUBLIC_URL,
        HUBSPOT_MCP_SERVER_SECRET="s" * 32,
    )
    assert "oauth-protected-resource" in out


def test_no_protected_resource_metadata_without_hosted_auth():
    # Without hosted auth the app is wrapped in the shared-secret middleware, so
    # reach through to the Starlette app it guards.
    out = _in_hosted_server(
        "app = s.build_http_app('0.0.0.0')\n"
        "print([r.path for r in app.app.routes if 'well-known' in r.path])",
        HUBSPOT_MCP_SERVER_SECRET="s" * 32,
    )
    assert "oauth-protected-resource" not in out


# --------------------------------------------------------------------------- #
# OAuth replaces the shared secret, it does not stack with it
# --------------------------------------------------------------------------- #


def test_hosted_auth_drops_the_shared_secret_wrapper():
    """Otherwise every user would need a secret nobody should be sharing."""
    out = _in_hosted_server(
        "print(type(s.build_http_app('0.0.0.0')).__name__)",
        HUBSPOT_MCP_OAUTH_ISSUER=ISSUER,
        HUBSPOT_MCP_PUBLIC_URL=PUBLIC_URL,
        HUBSPOT_MCP_SERVER_SECRET="s" * 32,
    )
    assert out == "Starlette"


def test_without_hosted_auth_the_shared_secret_still_guards():
    out = _in_hosted_server(
        "print(type(s.build_http_app('0.0.0.0')).__name__)",
        HUBSPOT_MCP_SERVER_SECRET="s" * 32,
    )
    assert out == "BearerAuthMiddleware"


# --------------------------------------------------------------------------- #
# Misconfiguration fails at startup, not on the first request
# --------------------------------------------------------------------------- #


def test_an_issuer_without_a_public_url_refuses_to_start():
    """The resource identifier is built from the public URL; without it every
    token's audience mismatches and every request 401s for no visible reason."""
    result = subprocess.run(
        [sys.executable, "-c", "import hubspot_mcp.server"],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/tmp",
            "HUBSPOT_MCP_OAUTH_ISSUER": ISSUER,
        },
    )
    assert result.returncode != 0
    assert "HUBSPOT_MCP_PUBLIC_URL" in result.stderr


def test_the_local_path_is_untouched():
    """stdio needs no authorization at all; the spec says so explicitly."""
    out = _in_hosted_server("print(s._AUTH_SETTINGS, s._TOKEN_VERIFIER)")
    assert out == "None None"


@pytest.mark.parametrize("issuer", ["", "   "])
def test_a_blank_issuer_is_not_hosted_mode(issuer):
    out = _in_hosted_server("print(s._AUTH_SETTINGS)", HUBSPOT_MCP_OAUTH_ISSUER=issuer)
    assert out == "None"
