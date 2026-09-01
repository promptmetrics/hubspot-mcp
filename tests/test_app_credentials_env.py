"""App credentials from the environment (Phase 3, stage 1).

A hosted deployment has no writable home directory, so
`~/.claude/hubspot/app_credentials.json` — the only source Phase 1 had — cannot
exist there. Without this the HubSpot app's client id and secret could not be
supplied to a deployment at all.
"""
from __future__ import annotations

import pytest

from hubspot_mcp import app_credentials
from hubspot_mcp.app_credentials import (
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    REGION_ENV,
    get_client_id,
    get_client_secret,
    get_oauth_endpoints,
    get_region,
    save_app_credentials,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    from pathlib import Path

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in (CLIENT_ID_ENV, CLIENT_SECRET_ENV, REGION_ENV):
        monkeypatch.delenv(name, raising=False)


def test_env_supplies_credentials_with_no_file(monkeypatch):
    monkeypatch.setenv(CLIENT_ID_ENV, "env-client-id")
    monkeypatch.setenv(CLIENT_SECRET_ENV, "env-client-secret")

    assert app_credentials.load_app_credentials() is None
    assert get_client_id() == "env-client-id"
    assert get_client_secret() == "env-client-secret"


def test_the_file_still_works_when_no_env_is_set():
    """The local plugin writes this file on every session start."""
    save_app_credentials("file-client-id", "file-client-secret")
    assert get_client_id() == "file-client-id"
    assert get_client_secret() == "file-client-secret"


def test_env_wins_over_the_file(monkeypatch):
    """A deployment sets env deliberately; the file may be another app's leftover."""
    save_app_credentials("file-client-id", "file-client-secret")
    monkeypatch.setenv(CLIENT_ID_ENV, "env-client-id")
    monkeypatch.setenv(CLIENT_SECRET_ENV, "env-client-secret")

    assert get_client_id() == "env-client-id"
    assert get_client_secret() == "env-client-secret"


@pytest.mark.parametrize("raw", ["", "   ", "\n"])
def test_a_blank_env_var_falls_through_to_the_file(monkeypatch, raw):
    """An env var set to empty is not a credential; it is an unset one."""
    save_app_credentials("file-client-id", "file-client-secret")
    monkeypatch.setenv(CLIENT_ID_ENV, raw)

    assert get_client_id() == "file-client-id"


def test_credentials_are_whitespace_trimmed(monkeypatch):
    """`vercel env pull` and dashboard paste both add trailing newlines."""
    monkeypatch.setenv(CLIENT_ID_ENV, "  env-client-id\n")
    monkeypatch.setenv(CLIENT_SECRET_ENV, "\tenv-client-secret ")

    assert get_client_id() == "env-client-id"
    assert get_client_secret() == "env-client-secret"


def test_nothing_configured_is_none():
    assert get_client_id() is None
    assert get_client_secret() is None


# --------------------------------------------------------------------------- #
# Region
# --------------------------------------------------------------------------- #


def test_region_defaults_to_us():
    assert get_region() == "us"


@pytest.mark.parametrize("region", ["us", "eu"])
def test_region_from_env(monkeypatch, region):
    monkeypatch.setenv(REGION_ENV, region)
    assert get_region() == region


def test_region_env_wins_over_the_file(monkeypatch):
    save_app_credentials("id", "secret", region="eu")
    monkeypatch.setenv(REGION_ENV, "us")
    assert get_region() == "us"


def test_an_unrecognised_region_falls_back_rather_than_raising(monkeypatch):
    """Refusing to start over a typo in an optional variable is the worse failure."""
    monkeypatch.setenv(REGION_ENV, "apac")
    assert get_region() == "us"


def test_the_eu_region_changes_the_authorize_host_only(monkeypatch):
    """The token endpoint is global; only the authorize host is regional."""
    monkeypatch.setenv(REGION_ENV, "eu")
    eu_authorize, eu_token = get_oauth_endpoints()
    monkeypatch.setenv(REGION_ENV, "us")
    us_authorize, us_token = get_oauth_endpoints()

    assert eu_authorize != us_authorize
    assert eu_token == us_token
