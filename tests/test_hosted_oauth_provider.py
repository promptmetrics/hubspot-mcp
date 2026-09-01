"""Resolving a caller's HubSpot session (Phase 3, stage 1).

`HostedOAuthProvider` answers "which portal did *this caller* authorise?" — the
question a hosted deployment has to ask on every request, where Phase 1 only
ever asked "which portal is this process configured for?".

Most of what matters here is failure behaviour. A refresh can fail because
HubSpot revoked the grant (the user must reconnect) or because HubSpot had a bad
minute (they must not). Getting that backwards either strands a working
connection or sends people round an OAuth flow for nothing.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from hubspot_mcp.auth.hosted import (
    REFRESH_LEEWAY_SECONDS,
    HostedOAuthProvider,
    NotConnectedError,
)
from hubspot_mcp.state.connection_store import HubSpotConnection

SUBJECT = "user_01HQXZ8P3Q"
PORTAL = "99999999"


class InMemoryConnections:
    def __init__(self, connection: HubSpotConnection | None = None) -> None:
        self._by_subject = {connection.subject: connection} if connection else {}
        self.writes: list[HubSpotConnection] = []

    async def get(self, subject):
        return self._by_subject.get(subject)

    async def put(self, connection):
        self._by_subject[connection.subject] = connection
        self.writes.append(connection)

    async def delete(self, subject):
        self._by_subject.pop(subject, None)


def _connection(**overrides) -> HubSpotConnection:
    data = {
        "subject": SUBJECT,
        "portal_id": PORTAL,
        "refresh_token": "refresh-1",
        "access_token": "access-1",
        "expires_at": time.time() + 3600,
        "scopes_granted": ("crm.objects.contacts.read",),
    }
    data.update(overrides)
    return HubSpotConnection(**data)


def _stub_refresh(monkeypatch, result):
    """Patch the network half of the refresh; `result` is a body or an exception."""
    calls: list[str] = []

    async def fake(refresh_token: str):
        calls.append(refresh_token)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("hubspot_mcp.oauth_flow.refresh_tokens_only", fake)
    return calls


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.hubapi.com/oauth/2026-03/token")
    return httpx.HTTPStatusError(
        f"{code}", request=request, response=httpx.Response(code, request=request)
    )


# --------------------------------------------------------------------------- #
# The happy paths
# --------------------------------------------------------------------------- #


async def test_a_fresh_token_is_returned_without_refreshing(monkeypatch):
    store = InMemoryConnections(_connection())
    calls = _stub_refresh(monkeypatch, {})

    config = await HostedOAuthProvider(store).resolve(SUBJECT)

    assert config.portal_id == PORTAL
    assert config.token == "access-1"
    assert config.auth_type == "oauth"
    assert calls == [], "refreshed a token that had an hour left"


async def test_an_expiring_token_is_refreshed_and_written_back(monkeypatch):
    store = InMemoryConnections(_connection(expires_at=time.time() + 10))
    _stub_refresh(monkeypatch, {"access_token": "access-2", "expires_in": 1800})

    config = await HostedOAuthProvider(store).resolve(SUBJECT)

    assert config.token == "access-2"
    assert len(store.writes) == 1
    assert store.writes[0].access_token == "access-2"


async def test_the_refresh_leeway_is_applied(monkeypatch):
    """A token must never be handed out with only seconds left on it."""
    store = InMemoryConnections(_connection(expires_at=time.time() + REFRESH_LEEWAY_SECONDS - 30))
    calls = _stub_refresh(monkeypatch, {"access_token": "access-2", "expires_in": 1800})

    await HostedOAuthProvider(store).resolve(SUBJECT)

    assert calls == ["refresh-1"]


async def test_a_rotated_refresh_token_is_stored(monkeypatch):
    store = InMemoryConnections(_connection(expires_at=0))
    _stub_refresh(
        monkeypatch,
        {"access_token": "access-2", "expires_in": 1800, "refresh_token": "refresh-2"},
    )

    await HostedOAuthProvider(store).resolve(SUBJECT)

    assert store.writes[0].refresh_token == "refresh-2"


async def test_an_unrotated_refresh_token_is_kept(monkeypatch):
    """HubSpot does not always rotate; blanking it would break every later refresh."""
    store = InMemoryConnections(_connection(expires_at=0))
    _stub_refresh(monkeypatch, {"access_token": "access-2", "expires_in": 1800})

    await HostedOAuthProvider(store).resolve(SUBJECT)

    assert store.writes[0].refresh_token == "refresh-1"


async def test_scopes_survive_a_refresh_that_omits_them(monkeypatch):
    """A refresh response may omit `scope`; overwriting would make scope reports lie."""
    store = InMemoryConnections(_connection(expires_at=0))
    _stub_refresh(monkeypatch, {"access_token": "access-2", "expires_in": 1800})

    config = await HostedOAuthProvider(store).resolve(SUBJECT)

    assert config.scopes_granted == ["crm.objects.contacts.read"]


# --------------------------------------------------------------------------- #
# Not connected
# --------------------------------------------------------------------------- #


async def test_an_unconnected_subject_raises(monkeypatch):
    with pytest.raises(NotConnectedError, match="No HubSpot account is connected"):
        await HostedOAuthProvider(InMemoryConnections()).resolve(SUBJECT)


async def test_the_caller_cannot_name_someone_elses_portal():
    """Resolution is by subject only — portal id is an output of auth, not an input."""
    import inspect

    params = inspect.signature(HostedOAuthProvider.resolve).parameters
    assert list(params) == ["self", "subject"]


# --------------------------------------------------------------------------- #
# Refresh failure: conclusive vs transient
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_a_rejected_grant_requires_reconnection(monkeypatch, status):
    store = InMemoryConnections(_connection(expires_at=0))
    _stub_refresh(monkeypatch, _status_error(status))

    with pytest.raises(NotConnectedError) as exc:
        await HostedOAuthProvider(store).resolve(SUBJECT)

    assert exc.value.reconnect_required is True
    assert "Reconnect" in str(exc.value)


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_a_transient_failure_does_not_require_reconnection(monkeypatch, status):
    """Sending a user round OAuth because of a 503 rotates a working credential."""
    store = InMemoryConnections(_connection(expires_at=0))
    _stub_refresh(monkeypatch, _status_error(status))

    with pytest.raises(NotConnectedError) as exc:
        await HostedOAuthProvider(store).resolve(SUBJECT)

    assert exc.value.reconnect_required is False
    assert "temporary" in str(exc.value)


async def test_a_network_error_is_never_conclusive(monkeypatch):
    store = InMemoryConnections(_connection(expires_at=0))
    _stub_refresh(monkeypatch, httpx.ConnectError("dns failure"))

    with pytest.raises(NotConnectedError) as exc:
        await HostedOAuthProvider(store).resolve(SUBJECT)

    assert exc.value.reconnect_required is False


async def test_a_failed_refresh_does_not_discard_the_connection(monkeypatch):
    """Even a rejected grant is the only record of which portal they had."""
    store = InMemoryConnections(_connection(expires_at=0))
    _stub_refresh(monkeypatch, _status_error(400))

    with pytest.raises(NotConnectedError):
        await HostedOAuthProvider(store).resolve(SUBJECT)

    assert await store.get(SUBJECT) is not None
    assert store.writes == []


# --------------------------------------------------------------------------- #
# Concurrency
# --------------------------------------------------------------------------- #


async def test_concurrent_calls_refresh_once(monkeypatch):
    """Two refreshes racing means the loser writes back a rotated-away token."""
    store = InMemoryConnections(_connection(expires_at=0))
    calls: list[str] = []

    async def slow_refresh(refresh_token: str):
        calls.append(refresh_token)
        await asyncio.sleep(0.01)
        return {"access_token": "access-2", "expires_in": 1800, "refresh_token": "refresh-2"}

    monkeypatch.setattr("hubspot_mcp.oauth_flow.refresh_tokens_only", slow_refresh)
    provider = HostedOAuthProvider(store)

    results = await asyncio.gather(*(provider.resolve(SUBJECT) for _ in range(5)))

    assert len(calls) == 1, f"refreshed {len(calls)} times concurrently"
    assert {r.token for r in results} == {"access-2"}


async def test_different_subjects_do_not_block_each_other(monkeypatch):
    other = "user_02OTHER"
    store = InMemoryConnections(_connection(expires_at=0))
    await store.put(_connection(subject=other, expires_at=0, refresh_token="refresh-other"))
    store.writes.clear()

    started = asyncio.Event()

    async def gated_refresh(refresh_token: str):
        if refresh_token == "refresh-1":
            started.set()
            await asyncio.sleep(0.05)
        return {"access_token": f"new-{refresh_token}", "expires_in": 1800}

    monkeypatch.setattr("hubspot_mcp.oauth_flow.refresh_tokens_only", gated_refresh)
    provider = HostedOAuthProvider(store)

    first = asyncio.create_task(provider.resolve(SUBJECT))
    await started.wait()
    # Must not be serialised behind the other subject's in-flight refresh.
    second = await asyncio.wait_for(provider.resolve(other), timeout=0.5)

    assert second.token == "new-refresh-other"
    assert (await first).token == "new-refresh-1"
