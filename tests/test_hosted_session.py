"""Resolving a caller's session on the hosted path (Phase 3, stage 1).

This is the piece that makes all 86 tools act on the portal the *caller*
authorised. The tests below care most about three things that are invisible
until they hurt: that one person's request never reaches another's portal, that
clients are reused rather than leaked, and that someone who has authenticated
but not connected HubSpot gets a link instead of an error.
"""
from __future__ import annotations

import time

import pytest
from mcp.server.auth.provider import AccessToken

from hubspot_mcp.auth.hosted import NotConnectedError
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.hosted_session import ClientPool, build_session_resolver
from hubspot_mcp.state.connection_store import HubSpotConnection

ALICE = "user_alice"
BOB = "user_bob"
PORTAL_A = "11111111"
PORTAL_B = "22222222"


class InMemoryConnections:
    def __init__(self, *connections: HubSpotConnection) -> None:
        self._by_subject = {c.subject: c for c in connections}
        self.writes: list[HubSpotConnection] = []

    async def get(self, subject):
        return self._by_subject.get(subject)

    async def put(self, connection):
        self._by_subject[connection.subject] = connection
        self.writes.append(connection)

    async def delete(self, subject):
        self._by_subject.pop(subject, None)


def _connection(subject: str, portal_id: str) -> HubSpotConnection:
    return HubSpotConnection(
        subject=subject,
        portal_id=portal_id,
        refresh_token=f"refresh-{subject}",
        access_token=f"access-{subject}",
        expires_at=time.time() + 3600,
        scopes_granted=("crm.objects.contacts.read",),
    )


@pytest.fixture
def env(monkeypatch, tmp_path):
    """No network, no disk outside tmp, no capability probing."""
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("hubspot_mcp.persistence.CONFIG_DIR", tmp_path)

    async def no_probe(portal_config):
        return None

    monkeypatch.setattr("hubspot_mcp.hosted_session._capabilities", no_probe)
    return tmp_path


def _as_caller(monkeypatch, subject: str | None):
    """Present a verified access token for `subject`, as the transport would."""
    token = (
        None
        if subject is None
        else AccessToken(token="t", client_id="c", scopes=[], expires_at=None, subject=subject)
    )
    monkeypatch.setattr(
        "mcp.server.auth.middleware.auth_context.get_access_token", lambda: token
    )


def _with_connections(monkeypatch, store):
    monkeypatch.setattr("hubspot_mcp.auth.hosted.get_connection_store", lambda: store)


# --------------------------------------------------------------------------- #
# Isolation — the property the whole design exists for
# --------------------------------------------------------------------------- #


async def test_each_caller_gets_their_own_portal(env, monkeypatch):
    store = InMemoryConnections(_connection(ALICE, PORTAL_A), _connection(BOB, PORTAL_B))
    _with_connections(monkeypatch, store)
    resolve = build_session_resolver()

    _as_caller(monkeypatch, ALICE)
    alice = await resolve(None)
    _as_caller(monkeypatch, BOB)
    bob = await resolve(None)

    assert alice["portal_id"] == PORTAL_A
    assert bob["portal_id"] == PORTAL_B
    assert alice["client"] is not bob["client"]
    await resolve.pool.close_all()


async def test_a_callers_token_is_never_another_callers(env, monkeypatch):
    store = InMemoryConnections(_connection(ALICE, PORTAL_A), _connection(BOB, PORTAL_B))
    _with_connections(monkeypatch, store)
    resolve = build_session_resolver()

    _as_caller(monkeypatch, ALICE)
    alice = await resolve(None)
    _as_caller(monkeypatch, BOB)
    bob = await resolve(None)

    assert alice["client"]._client.headers["Authorization"] == f"Bearer access-{ALICE}"
    assert bob["client"]._client.headers["Authorization"] == f"Bearer access-{BOB}"
    await resolve.pool.close_all()


async def test_two_subjects_on_one_portal_do_not_share_a_client(env, monkeypatch):
    """Sharing would refresh one person's grant into the other's record."""
    store = InMemoryConnections(_connection(ALICE, PORTAL_A), _connection(BOB, PORTAL_A))
    _with_connections(monkeypatch, store)
    resolve = build_session_resolver()

    _as_caller(monkeypatch, ALICE)
    alice = await resolve(None)
    _as_caller(monkeypatch, BOB)
    bob = await resolve(None)

    assert alice["client"] is not bob["client"]
    await resolve.pool.close_all()


async def test_an_unauthenticated_caller_gets_no_client(env, monkeypatch):
    _with_connections(monkeypatch, InMemoryConnections(_connection(ALICE, PORTAL_A)))
    resolve = build_session_resolver()
    _as_caller(monkeypatch, None)

    session = await resolve(None)

    assert session["client"] is None
    assert session["portal_id"] is None
    assert "Not authenticated" in session["auth_error"]


# --------------------------------------------------------------------------- #
# Client lifecycle
# --------------------------------------------------------------------------- #


async def test_the_same_caller_reuses_one_client(env, monkeypatch):
    """A client per request leaks a connection pool per request."""
    _with_connections(monkeypatch, InMemoryConnections(_connection(ALICE, PORTAL_A)))
    resolve = build_session_resolver()
    _as_caller(monkeypatch, ALICE)

    first = await resolve(None)
    second = await resolve(None)

    assert first["client"] is second["client"]
    assert len(resolve.pool) == 1
    await resolve.pool.close_all()


async def test_a_reused_client_adopts_a_refreshed_token(env, monkeypatch):
    store = InMemoryConnections(_connection(ALICE, PORTAL_A))
    _with_connections(monkeypatch, store)
    resolve = build_session_resolver()
    _as_caller(monkeypatch, ALICE)

    first = await resolve(None)
    await store.put(
        HubSpotConnection(
            subject=ALICE,
            portal_id=PORTAL_A,
            refresh_token="refresh-new",
            access_token="access-new",
            expires_at=time.time() + 3600,
        )
    )
    second = await resolve(None)

    assert first["client"] is second["client"]
    assert second["client"]._client.headers["Authorization"] == "Bearer access-new"
    await resolve.pool.close_all()


async def test_the_pool_is_bounded_and_evicts_the_least_recently_used():
    pool = ClientPool(max_clients=2)
    a = await pool.acquire("a", PortalConfig(portal_id=PORTAL_A, token="t"))
    await pool.acquire("b", PortalConfig(portal_id=PORTAL_A, token="t"))
    await pool.acquire("a", PortalConfig(portal_id=PORTAL_A, token="t"))  # touch a
    await pool.acquire("c", PortalConfig(portal_id=PORTAL_A, token="t"))  # evicts b

    assert len(pool) == 2
    assert await pool.acquire("a", PortalConfig(portal_id=PORTAL_A, token="t")) is a
    await pool.close_all()


async def test_an_evicted_client_is_closed():
    pool = ClientPool(max_clients=1)
    first = await pool.acquire("a", PortalConfig(portal_id=PORTAL_A, token="t"))
    await pool.acquire("b", PortalConfig(portal_id=PORTAL_A, token="t"))

    assert first._client.is_closed, "eviction leaked a connection pool"
    await pool.close_all()


async def test_close_all_closes_everything():
    pool = ClientPool()
    clients = [
        await pool.acquire(str(i), PortalConfig(portal_id=PORTAL_A, token="t")) for i in range(3)
    ]
    await pool.close_all()

    assert len(pool) == 0
    assert all(c._client.is_closed for c in clients)


async def test_a_failing_close_does_not_stop_the_others(capsys):
    pool = ClientPool()
    good = await pool.acquire("a", PortalConfig(portal_id=PORTAL_A, token="t"))
    bad = await pool.acquire("b", PortalConfig(portal_id=PORTAL_A, token="t"))

    async def boom():
        raise RuntimeError("close failed")

    bad.close = boom
    await pool.close_all()

    assert good._client.is_closed
    assert "closing a pooled client failed" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Not connected yet
# --------------------------------------------------------------------------- #


async def test_an_unconnected_caller_gets_a_connect_link(env, monkeypatch):
    """Authenticated but no HubSpot yet is the normal first-run state."""
    monkeypatch.setenv("HUBSPOT_MCP_PUBLIC_URL", "https://mcp.example.com")
    monkeypatch.setattr("hubspot_mcp.app_credentials.get_client_id", lambda: "cid")
    _with_connections(monkeypatch, InMemoryConnections())
    resolve = build_session_resolver()
    _as_caller(monkeypatch, ALICE)

    session = await resolve(None)

    assert session["client"] is None
    assert "https://mcp.example.com/connect/hubspot?ticket=" in session["auth_error"]


async def test_a_transient_failure_does_not_offer_a_connect_link(env, monkeypatch):
    """Re-authorising fixes nothing when HubSpot is merely having a bad minute."""
    monkeypatch.setenv("HUBSPOT_MCP_PUBLIC_URL", "https://mcp.example.com")

    async def transient(self, subject):
        raise NotConnectedError("HubSpot could not refresh right now.", reconnect_required=False)

    monkeypatch.setattr("hubspot_mcp.auth.hosted.HostedOAuthProvider.resolve", transient)
    resolve = build_session_resolver()
    _as_caller(monkeypatch, ALICE)

    session = await resolve(None)

    assert "connect/hubspot?ticket=" not in session["auth_error"]
    assert "bad minute" in session["auth_error"] or "could not refresh" in session["auth_error"]


async def test_an_unmintable_link_still_explains_itself(env, monkeypatch, capsys):
    """A missing public URL must not turn guidance into a 500."""
    monkeypatch.delenv("HUBSPOT_MCP_PUBLIC_URL", raising=False)
    _with_connections(monkeypatch, InMemoryConnections())
    resolve = build_session_resolver()
    _as_caller(monkeypatch, ALICE)

    session = await resolve(None)

    assert "administrator" in session["auth_error"]
    assert "could not mint a connect link" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Resilience
# --------------------------------------------------------------------------- #


async def test_a_capability_probe_failure_does_not_fail_the_call(monkeypatch, tmp_path):
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    _with_connections(monkeypatch, InMemoryConnections(_connection(ALICE, PORTAL_A)))

    async def boom(portal_config):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr("hubspot_mcp.capabilities.probe_portal", boom)
    resolve = build_session_resolver()
    _as_caller(monkeypatch, ALICE)

    session = await resolve(None)

    assert session["client"] is not None
    assert session["capabilities"] is None
    await resolve.pool.close_all()


async def test_the_session_carries_every_key_the_tools_read(env, monkeypatch):
    """A missing key would be a KeyError inside a tool, not a clear error."""
    _with_connections(monkeypatch, InMemoryConnections(_connection(ALICE, PORTAL_A)))
    resolve = build_session_resolver()
    _as_caller(monkeypatch, ALICE)

    session = await resolve(None)

    assert set(session) == {
        "client", "cache", "portal_config", "portal_id", "auth_error", "capabilities",
    }
    await resolve.pool.close_all()
