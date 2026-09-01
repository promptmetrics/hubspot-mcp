"""The single-tenant boundary (Phase 2, Task 5).

Phase 2 serves one HubSpot portal per deployment, and that is not merely a
simplification — `_unadvertise_unavailable_tools` calls `mcp.remove_tool(...)`
on the module-level server object, so a second portal on the same instance
would change which tools the first one advertises, permanently, until the
instance recycled.

The plan calls this "in scope to enforce, not to fix": the server must refuse
to start rather than silently mis-serve. Policy matches the bearer-auth
guard — permissive on loopback, strict on anything the internet can reach.
"""
from __future__ import annotations

import pytest

from hubspot_mcp.tenancy import (
    PORTAL_ENV,
    MultiTenantConfiguration,
    configured_token_portals,
    enforce_single_tenant,
    is_loopback,
)

PUBLIC_BIND = "0.0.0.0"  # noqa: S104 — the subject of these tests
PORTAL = "99999999"


# --------------------------------------------------------------------------- #
# A public bind must resolve exactly one portal, unambiguously
# --------------------------------------------------------------------------- #


def test_an_explicit_portal_is_accepted():
    assert enforce_single_tenant(PUBLIC_BIND, PORTAL, "env", env={}) == PORTAL
    assert enforce_single_tenant(PUBLIC_BIND, PORTAL, "flag", env={}) == PORTAL


def test_no_portal_refuses_to_start():
    """Over stdio the user runs auth and retries; a deployment has no one to prompt."""
    with pytest.raises(MultiTenantConfiguration, match="No HubSpot portal is configured"):
        enforce_single_tenant(PUBLIC_BIND, None, "none", env={})


def test_a_working_directory_portal_refuses_to_start():
    """On Vercel the working directory is a build artifact, not deployment config."""
    with pytest.raises(MultiTenantConfiguration, match=r"\.hubspot-portal"):
        enforce_single_tenant(PUBLIC_BIND, PORTAL, "file", env={})


def test_the_refusal_names_the_variable_to_set():
    with pytest.raises(MultiTenantConfiguration, match=PORTAL_ENV):
        enforce_single_tenant(PUBLIC_BIND, None, "none", env={})


def test_credentials_for_two_portals_refuse_to_start():
    """The actual shape of an attempted multi-tenant deployment."""
    env = {"HUBSPOT_TOKEN_99999999": "a", "HUBSPOT_TOKEN_11111111": "b"}
    with pytest.raises(MultiTenantConfiguration, match="serves one portal per deployment"):
        enforce_single_tenant(PUBLIC_BIND, PORTAL, "env", env=env)


def test_the_multi_portal_refusal_explains_the_consequence():
    """A guard nobody understands gets disabled; say what it prevents."""
    env = {"HUBSPOT_TOKEN_99999999": "a", "HUBSPOT_TOKEN_11111111": "b"}
    with pytest.raises(MultiTenantConfiguration) as exc:
        enforce_single_tenant(PUBLIC_BIND, PORTAL, "env", env=env)
    assert "advertises" in str(exc.value)
    assert "11111111" in str(exc.value) and "99999999" in str(exc.value)


def test_credentials_for_a_different_portal_refuse_to_start():
    env = {"HUBSPOT_TOKEN_11111111": "b"}
    with pytest.raises(MultiTenantConfiguration, match="One of the two is wrong"):
        enforce_single_tenant(PUBLIC_BIND, PORTAL, "env", env=env)


def test_credentials_for_the_configured_portal_are_fine():
    env = {"HUBSPOT_TOKEN_99999999": "a"}
    assert enforce_single_tenant(PUBLIC_BIND, PORTAL, "env", env=env) == PORTAL


def test_oauth_deployments_carry_no_token_variables():
    """Bring-your-own-app OAuth stores its refresh token elsewhere; that is fine."""
    assert enforce_single_tenant(PUBLIC_BIND, PORTAL, "env", env={"HUBSPOT_MODE": "oauth"}) == PORTAL


def test_every_problem_is_reported_not_just_the_first():
    """Fixing one and redeploying to hit the next is a slow way to learn."""
    env = {"HUBSPOT_TOKEN_1": "a", "HUBSPOT_TOKEN_2": "b"}
    with pytest.raises(MultiTenantConfiguration) as exc:
        enforce_single_tenant(PUBLIC_BIND, None, "none", env=env)
    assert "No HubSpot portal is configured" in str(exc.value)
    assert "serves one portal per deployment" in str(exc.value)


# --------------------------------------------------------------------------- #
# Loopback stays permissive
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_only_warns(host, capsys):
    assert enforce_single_tenant(host, PORTAL, "file", env={}) == PORTAL
    err = capsys.readouterr().err
    assert "ambiguous portal configuration" in err
    assert "will refuse to start" in err


def test_loopback_with_no_portal_still_returns_none(capsys):
    """stdio's cold-start contract: tools/list answers with auth_error set."""
    assert enforce_single_tenant("127.0.0.1", None, "none", env={}) is None
    assert "No HubSpot portal is configured" in capsys.readouterr().err


@pytest.mark.parametrize(
    "host,loopback",
    [("127.0.0.1", True), ("localhost", True), ("::1", True), ("", True),
     ("0.0.0.0", False), ("::", False), ("10.0.0.4", False), ("mcp.example.com", False)],  # noqa: S104
)
def test_loopback_classification(host, loopback):
    assert is_loopback(host) is loopback


# --------------------------------------------------------------------------- #
# Token-variable discovery
# --------------------------------------------------------------------------- #


def test_token_portals_are_discovered_from_the_environment():
    env = {"HUBSPOT_TOKEN_123": "a", "HUBSPOT_TOKEN_456": "b", "HUBSPOT_PORTAL": "123"}
    assert configured_token_portals(env) == {"123", "456"}


@pytest.mark.parametrize(
    "name",
    ["HUBSPOT_TOKEN", "HUBSPOT_TOKEN_", "HUBSPOT_TOKEN_abc", "HUBSPOT_TIER_123", "MY_HUBSPOT_TOKEN_123"],
)
def test_unrelated_variables_are_not_mistaken_for_portals(name):
    assert configured_token_portals({name: "x"}) == set()


# --------------------------------------------------------------------------- #
# Wired into the hosted entrypoint, not just available
# --------------------------------------------------------------------------- #


def test_build_http_app_enforces_the_boundary(monkeypatch):
    """Vercel imports the ASGI app; the guard has to run there or it runs nowhere."""
    from hubspot_mcp import server
    from hubspot_mcp.auth.bearer_middleware import SECRET_ENV

    monkeypatch.setenv(SECRET_ENV, "s" * 32)
    monkeypatch.delenv("HUBSPOT_PORTAL", raising=False)
    monkeypatch.setattr(server, "detect_default_portal", lambda _cwd: None)
    monkeypatch.setitem(server._SERVER_CONFIG, "portal_id", None)

    with pytest.raises(MultiTenantConfiguration):
        server.build_http_app(PUBLIC_BIND)


def test_build_http_app_accepts_a_single_configured_portal(monkeypatch):
    from hubspot_mcp import server
    from hubspot_mcp.auth.bearer_middleware import SECRET_ENV

    monkeypatch.setenv(SECRET_ENV, "s" * 32)
    monkeypatch.setenv("HUBSPOT_PORTAL", PORTAL)
    assert server.build_http_app(PUBLIC_BIND) is not None


def test_the_portal_source_is_reported(monkeypatch):
    from hubspot_mcp import server

    monkeypatch.setitem(server._SERVER_CONFIG, "portal_id", None)
    monkeypatch.setenv("HUBSPOT_PORTAL", PORTAL)
    assert server._resolve_portal_source() == (PORTAL, "env")

    monkeypatch.setitem(server._SERVER_CONFIG, "portal_id", "12345678")
    assert server._resolve_portal_source() == ("12345678", "flag")

    monkeypatch.setitem(server._SERVER_CONFIG, "portal_id", None)
    monkeypatch.delenv("HUBSPOT_PORTAL", raising=False)
    monkeypatch.setattr(server, "detect_default_portal", lambda _cwd: "87654321")
    assert server._resolve_portal_source() == ("87654321", "file")

    monkeypatch.setattr(server, "detect_default_portal", lambda _cwd: None)
    assert server._resolve_portal_source() == (None, "none")
