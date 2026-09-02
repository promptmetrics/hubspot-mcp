"""The HubSpot app definition (Phase 3, stage 1).

`hubspot-app/` is the app's configuration as code, uploaded with
`hs project upload`. It is in the repo for one reason: the scopes it requires
and the scopes the server requests at authorize time must be the same set. An
app that requires a scope the authorize call omits **fails to install**, and an
app missing a scope a tool needs fails at the first call with a 403 — both a
long way from where the mistake was made.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from hubspot_mcp.scope_registry import authorize_scopes

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_META = ROOT / "hubspot-app" / "src" / "app" / "app-hsmeta.json"
REDIRECT_PATH = "/connect/hubspot/callback"


@pytest.fixture(scope="module")
def app_config() -> dict:
    return json.loads(APP_META.read_text())["config"]


def test_the_required_scopes_are_exactly_what_the_server_requests(app_config):
    """The two must match, or the install fails or the tools 403."""
    assert app_config["auth"]["requiredScopes"] == authorize_scopes()


def test_the_oauth_scope_is_present(app_config):
    """HubSpot's own scope for OAuth access; every OAuth app declares it."""
    assert "oauth" in app_config["auth"]["requiredScopes"]


def test_no_delete_scopes_are_requested(app_config):
    """Least privilege: deletes go through the write gate using write scopes."""
    assert [s for s in app_config["auth"]["requiredScopes"] if s.endswith(".delete")] == []


@pytest.mark.parametrize(
    "prefix,why",
    [
        ("crm.objects.notes.", "engagement scopes HubSpot does not offer"),
        ("crm.objects.calls.", "engagement scopes HubSpot does not offer"),
        ("crm.objects.tasks.", "engagement scopes HubSpot does not offer"),
        ("crm.objects.emails.", "engagement scopes HubSpot does not offer"),
        ("crm.objects.tickets.", "tickets use the umbrella `tickets` scope"),
        ("crm.schemas.tickets.", "tickets use the umbrella `tickets` scope"),
    ],
)
def test_no_scopes_hubspot_will_not_grant(app_config, prefix, why):
    """An unrecognised scope fails the app deploy outright:

        ERROR The scope crm.objects.tickets.write could not be recognized.
    """
    offending = [s for s in app_config["auth"]["requiredScopes"] if s.startswith(prefix)]
    assert offending == [], f"{offending}: {why}"


def test_tickets_are_covered_by_the_umbrella_scope(app_config):
    """Dropping the invalid names must not drop ticket access altogether."""
    assert "tickets" in app_config["auth"]["requiredScopes"]


# --------------------------------------------------------------------------- #
# Distribution and auth
# --------------------------------------------------------------------------- #


def test_distribution_is_marketplace(app_config):
    """Private distribution caps at 10 installs for a standard partner;
    marketplace-unlisted allows 25."""
    assert app_config["distribution"] == "marketplace"


def test_auth_is_oauth(app_config):
    """A Marketplace listing requires OAuth as the sole authorization method."""
    assert app_config["auth"]["type"] == "oauth"


def test_the_redirect_url_matches_the_route_the_server_serves(app_config):
    """HubSpot rejects a mismatch outright, and the message does not say why."""
    urls = app_config["auth"]["redirectUrls"]
    assert len(urls) == 1, "more than one redirect URL invites the wrong one being used"
    assert urls[0].endswith(REDIRECT_PATH)
    assert urls[0].startswith("https://"), "HubSpot requires https for a public app"
    assert not urls[0].endswith("/" + REDIRECT_PATH.lstrip("/") + "/"), "no trailing slash"


def test_the_redirect_host_matches_the_deployment(app_config):
    """One host, three places: this, HUBSPOT_MCP_PUBLIC_URL, and the WorkOS
    resource indicator. This pins the one we control from here."""
    assert app_config["auth"]["redirectUrls"][0] == (
        "https://hubspot-mcp.promptmetrics.dev/connect/hubspot/callback"
    )


def test_outbound_fetch_is_limited_to_the_hubspot_api(app_config):
    assert app_config["permittedUrls"]["fetch"] == ["https://api.hubapi.com"]


# --------------------------------------------------------------------------- #
# Listing readiness
# --------------------------------------------------------------------------- #


def test_support_placeholders_are_obvious():
    """HubSpot verifies these are live before approving a listing. They are
    deliberately not plausible-looking, so nobody ships them by accident.
    """
    support = json.loads(APP_META.read_text())["config"]["support"]
    assert "REPLACE_BEFORE_LISTING" in support["supportEmail"]
    assert support["documentationUrl"].startswith("https://github.com/promptmetrics/hubspot-mcp")
