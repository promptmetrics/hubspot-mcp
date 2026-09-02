"""Connecting a HubSpot account to an authenticated user (Phase 3, stage 1).

Authorising HubSpot is a browser journey, but the person's identity comes from
an MCP access token a browser never carries. The bridge is a one-time ticket
bound to the caller's subject: opening the link is what proves, to the browser
half, who is connecting.

That makes the ticket and the OAuth `state` credentials in a URL, so most of
what follows is about replay, forgery, expiry and leakage rather than the happy
path.
"""
from __future__ import annotations

import time

import pytest

from hubspot_mcp.auth.connect import (
    PUBLIC_URL_ENV,
    STATE_TTL_SECONDS,
    TICKET_TTL_SECONDS,
    ConnectError,
    ConnectFlow,
)
from hubspot_mcp.state.cache_store import FileCacheStore
from hubspot_mcp.state.connection_store import HubSpotConnection

SUBJECT = "user_01HQXZ8P3Q"
PUBLIC_URL = "https://mcp.example.com"
SCOPES = ("crm.objects.contacts.read", "crm.objects.contacts.write")


class InMemoryConnections:
    def __init__(self) -> None:
        self.stored: list[HubSpotConnection] = []

    async def get(self, subject):
        return next((c for c in self.stored if c.subject == subject), None)

    async def put(self, connection):
        self.stored.append(connection)

    async def delete(self, subject):
        self.stored = [c for c in self.stored if c.subject != subject]


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("hubspot_mcp.app_credentials.get_client_id", lambda: "test-client-id")
    return ConnectFlow(
        scopes=SCOPES,
        public_url=PUBLIC_URL,
        cache=FileCacheStore(),
        connections=InMemoryConnections(),
    )


def _token_body(**overrides):
    body = {
        "access_token": "hs-access",
        "refresh_token": "hs-refresh",
        "expires_in": 1800,
        "scope": "crm.objects.contacts.read crm.objects.contacts.write",
        "hub_id": 99999999,
    }
    body.update(overrides)
    return body


def _stub_exchange(monkeypatch, body=None, error=None):
    calls: list[tuple[str, str, str]] = []

    async def fake(code, redirect_uri, code_verifier):
        calls.append((code, redirect_uri, code_verifier))
        if error is not None:
            raise error
        return body if body is not None else _token_body()

    monkeypatch.setattr("hubspot_mcp.oauth_flow.exchange_code_only", fake)
    return calls


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #


async def test_a_ticket_produces_a_link_on_our_own_host(flow):
    link = await flow.issue_ticket(SUBJECT)
    assert link.startswith(f"{PUBLIC_URL}/connect/hubspot?ticket=")


async def test_begin_redirects_to_hubspot_with_pkce(flow):
    url = await flow.begin((await flow.issue_ticket(SUBJECT)).split("ticket=")[1])

    assert url.startswith("https://")
    assert "code_challenge=" in url and "code_challenge_method=S256" in url
    assert "response_type=code" in url
    assert "client_id=test-client-id" in url
    for scope in SCOPES:
        assert scope.replace(".", ".") in url.replace("%2E", ".")


async def test_the_full_journey_stores_the_connection(flow, monkeypatch):
    _stub_exchange(monkeypatch)
    ticket = (await flow.issue_ticket(SUBJECT)).split("ticket=")[1]
    state = (await flow.begin(ticket)).split("state=")[1].split("&")[0]

    connection = await flow.complete("auth-code", state)

    assert connection.subject == SUBJECT
    assert connection.portal_id == "99999999"
    assert connection.refresh_token == "hs-refresh"
    assert connection.scopes_granted == SCOPES
    assert connection.expires_at is not None and connection.expires_at > time.time()
    assert flow.connections.stored == [connection]


async def test_the_redirect_uri_is_server_configured(flow, monkeypatch):
    """A caller-supplied redirect_uri is how an OAuth flow becomes an open redirect."""
    calls = _stub_exchange(monkeypatch)
    ticket = (await flow.issue_ticket(SUBJECT)).split("ticket=")[1]
    state = (await flow.begin(ticket)).split("state=")[1].split("&")[0]
    await flow.complete("auth-code", state)

    assert calls[0][1] == f"{PUBLIC_URL}/connect/hubspot/callback"
    assert flow.redirect_uri == f"{PUBLIC_URL}/connect/hubspot/callback"


# --------------------------------------------------------------------------- #
# Replay, forgery, expiry
# --------------------------------------------------------------------------- #


async def test_a_ticket_is_single_use(flow):
    ticket = (await flow.issue_ticket(SUBJECT)).split("ticket=")[1]
    await flow.begin(ticket)

    with pytest.raises(ConnectError, match="expired or was already used"):
        await flow.begin(ticket)


async def test_a_state_is_single_use(flow, monkeypatch):
    _stub_exchange(monkeypatch)
    ticket = (await flow.issue_ticket(SUBJECT)).split("ticket=")[1]
    state = (await flow.begin(ticket)).split("state=")[1].split("&")[0]
    await flow.complete("auth-code", state)

    with pytest.raises(ConnectError, match="could not be verified"):
        await flow.complete("auth-code", state)


@pytest.mark.parametrize(
    "value", ["", "short", "../../etc/passwd", "a b", "*", "x" * 200, "tok;en"]
)
async def test_a_forged_ticket_is_refused(flow, value):
    with pytest.raises(ConnectError, match="expired or was already used"):
        await flow.begin(value)


@pytest.mark.parametrize("value", ["", "short", "../../etc/passwd", "a b", "*"])
async def test_a_forged_state_is_refused(flow, value):
    with pytest.raises(ConnectError, match="could not be verified"):
        await flow.complete("auth-code", value)


async def test_forged_and_expired_states_are_indistinguishable(flow, monkeypatch):
    """Telling an attacker which one they hit is free help."""
    ticket = (await flow.issue_ticket(SUBJECT)).split("ticket=")[1]
    state = (await flow.begin(ticket)).split("state=")[1].split("&")[0]

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + STATE_TTL_SECONDS + 60)

    with pytest.raises(ConnectError) as expired:
        await flow.complete("auth-code", state)
    with pytest.raises(ConnectError) as forged:
        await flow.complete("auth-code", "PGFvcmdlZC1zdGF0ZS12YWx1ZQ")

    assert str(expired.value) == str(forged.value)


async def test_an_expired_ticket_is_refused(flow, monkeypatch):
    ticket = (await flow.issue_ticket(SUBJECT)).split("ticket=")[1]
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + TICKET_TTL_SECONDS + 60)

    with pytest.raises(ConnectError, match="expired or was already used"):
        await flow.begin(ticket)


async def test_a_missing_code_is_refused(flow, monkeypatch):
    _stub_exchange(monkeypatch)
    ticket = (await flow.issue_ticket(SUBJECT)).split("ticket=")[1]
    state = (await flow.begin(ticket)).split("state=")[1].split("&")[0]

    with pytest.raises(ConnectError, match="did not return an authorization code"):
        await flow.complete("", state)


async def test_the_connection_binds_to_the_ticketed_subject_only(flow, monkeypatch):
    """The subject comes from the ticket, never from anything in the callback."""
    _stub_exchange(monkeypatch)
    ticket = (await flow.issue_ticket(SUBJECT)).split("ticket=")[1]
    state = (await flow.begin(ticket)).split("state=")[1].split("&")[0]

    connection = await flow.complete("auth-code", state)

    assert connection.subject == SUBJECT
    assert await flow.connections.get("someone-else") is None


# --------------------------------------------------------------------------- #
# Leakage
# --------------------------------------------------------------------------- #


def _backing_store_dump(root) -> str:
    """Everything an operator (or an attacker) would see: paths and contents."""
    return "".join(f"{p}\n{p.read_text()}" for p in root.rglob("*.json"))


async def test_the_ticket_is_not_stored_verbatim(flow, tmp_path):
    """A dump of the backing store must yield nothing replayable — keys included."""
    ticket = (await flow.issue_ticket(SUBJECT)).split("ticket=")[1]
    dumped = _backing_store_dump(tmp_path)
    assert ticket not in dumped
    assert SUBJECT in dumped  # the binding is still there, under a digest key


async def test_the_state_is_not_stored_verbatim(flow, tmp_path):
    ticket = (await flow.issue_ticket(SUBJECT)).split("ticket=")[1]
    state = (await flow.begin(ticket)).split("state=")[1].split("&")[0]
    assert state not in _backing_store_dump(tmp_path)


# --------------------------------------------------------------------------- #
# Portal discovery
# --------------------------------------------------------------------------- #


async def test_the_portal_comes_from_hubspot_not_the_caller(flow, monkeypatch):
    """A public app cannot know the portal in advance — the user picks it."""
    _stub_exchange(monkeypatch, body=_token_body(hub_id=12345678))
    ticket = (await flow.issue_ticket(SUBJECT)).split("ticket=")[1]
    state = (await flow.begin(ticket)).split("state=")[1].split("&")[0]

    assert (await flow.complete("auth-code", state)).portal_id == "12345678"


async def test_the_token_info_endpoint_is_the_fallback(flow, monkeypatch):
    """Documented fallback for a token response that omits hub_id."""
    import httpx
    import respx

    _stub_exchange(monkeypatch, body=_token_body(hub_id=None))
    ticket = (await flow.issue_ticket(SUBJECT)).split("ticket=")[1]
    state = (await flow.begin(ticket)).split("state=")[1].split("&")[0]

    with respx.mock:
        respx.get(url__regex=r".*/oauth/v1/access-tokens/.*").mock(
            return_value=httpx.Response(200, json={"hub_id": 55555555})
        )
        connection = await flow.complete("auth-code", state)

    assert connection.portal_id == "55555555"


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_a_missing_public_url_is_refused(monkeypatch):
    """The redirect URI must match what is registered on the HubSpot app exactly."""
    monkeypatch.delenv(PUBLIC_URL_ENV, raising=False)
    with pytest.raises(ConnectError, match=PUBLIC_URL_ENV):
        ConnectFlow.from_env()


def test_a_trailing_slash_does_not_double_up(monkeypatch):
    monkeypatch.setenv(PUBLIC_URL_ENV, f"{PUBLIC_URL}/")
    assert ConnectFlow.from_env(scopes=list(SCOPES)).redirect_uri == (
        f"{PUBLIC_URL}/connect/hubspot/callback"
    )


async def test_a_ticket_needs_an_authenticated_user(flow):
    with pytest.raises(ConnectError, match="without an authenticated user"):
        await flow.issue_ticket("")


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


async def _route(handler, **query):
    from starlette.datastructures import QueryParams

    class _Request:
        query_params = QueryParams(query)

    return await handler(_Request())


async def test_the_connect_route_redirects_to_hubspot(monkeypatch, tmp_path):
    from hubspot_mcp import server
    from hubspot_mcp.auth.connect import ConnectFlow

    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("hubspot_mcp.app_credentials.get_client_id", lambda: "test-client-id")
    monkeypatch.setenv(PUBLIC_URL_ENV, PUBLIC_URL)
    monkeypatch.setattr(ConnectFlow, "from_env", classmethod(lambda cls, scopes=None: cls(
        scopes=SCOPES, public_url=PUBLIC_URL, cache=FileCacheStore(), connections=InMemoryConnections()
    )))

    flow = ConnectFlow.from_env()
    ticket = (await flow.issue_ticket(SUBJECT)).split("ticket=")[1]
    response = await _route(server.connect_hubspot, ticket=ticket)

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://")


async def test_a_bad_ticket_renders_a_page_not_a_traceback(monkeypatch, tmp_path):
    from hubspot_mcp import server
    from hubspot_mcp.auth.connect import ConnectFlow

    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    monkeypatch.setenv(PUBLIC_URL_ENV, PUBLIC_URL)
    monkeypatch.setattr(ConnectFlow, "from_env", classmethod(lambda cls, scopes=None: cls(
        scopes=SCOPES, public_url=PUBLIC_URL, cache=FileCacheStore(), connections=InMemoryConnections()
    )))

    response = await _route(server.connect_hubspot, ticket="nope")

    assert response.status_code == 400
    assert b"Could not start the connection" in response.body


async def test_hubspot_error_text_is_escaped(monkeypatch):
    """HubSpot's error_description is attacker-influencable and lands in HTML."""
    from hubspot_mcp import server

    response = await _route(
        server.connect_hubspot_callback,
        error="access_denied",
        error_description="<img src=x onerror=alert(1)>",
    )

    assert response.status_code == 400
    assert b"<img src=x" not in response.body
    assert b"&lt;img src=x" in response.body


async def test_a_declined_consent_explains_itself(monkeypatch):
    from hubspot_mcp import server

    response = await _route(server.connect_hubspot_callback, error="access_denied")

    assert b"HubSpot did not complete the connection" in response.body


async def test_an_unexpected_callback_failure_shows_no_internals(monkeypatch, tmp_path, capsys):
    """A stack trace in a browser helps nobody and may leak configuration."""
    from hubspot_mcp import server
    from hubspot_mcp.auth.connect import ConnectFlow

    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    monkeypatch.setenv(PUBLIC_URL_ENV, PUBLIC_URL)

    async def boom(self, code, state):
        raise RuntimeError("REDIS_URL=redis://user:hunter2@internal:6379")

    monkeypatch.setattr(ConnectFlow, "complete", boom)
    monkeypatch.setattr(ConnectFlow, "from_env", classmethod(lambda cls, scopes=None: cls(
        scopes=SCOPES, public_url=PUBLIC_URL, cache=FileCacheStore(), connections=InMemoryConnections()
    )))

    response = await _route(server.connect_hubspot_callback, code="c", state="s")

    assert response.status_code == 400
    assert b"hunter2" not in response.body
    assert b"Something went wrong" in response.body
    assert "hunter2" in capsys.readouterr().err  # operator still sees it
