"""Where a refreshed HubSpot token gets persisted (Phase 3, stage 1).

`HubSpotClient` refreshes its own access token when one nears expiry. Phase 1
wrote the result to the local portal file, which is correct for stdio and wrong
for a hosted deployment twice over: the disk does not survive the instance, and
tokens there belong to a *user* in the connection store rather than to a portal.

Making the refresher injectable is what lets the same client serve both without
either knowing about the other.
"""
from __future__ import annotations

import time

import httpx
import pytest
import respx

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig

PORTAL = "99999999"


# Note: `expires_at=0` would NOT trigger a refresh — the client's check is
# `self.portal.expires_at and ...`, so a falsy value reads as "expiry unknown".
# `HostedOAuthProvider.is_expired` takes the safer view and treats an unknown
# expiry as expired, which is what covers the hosted path.
def _oauth_portal(**overrides) -> PortalConfig:
    data = {
        "portal_id": PORTAL,
        "token": "access-1",
        "auth_type": "oauth",
        "refresh_token": "refresh-1",
        "expires_at": time.time() + 3600,
    }
    data.update(overrides)
    return PortalConfig(**data)


def _refresher(body=None, calls=None):
    async def refresh(portal: PortalConfig):
        if calls is not None:
            calls.append(portal.portal_id)
        return body or {"access_token": "access-2", "expires_in": 1800}

    return refresh


# --------------------------------------------------------------------------- #
# The injected refresher
# --------------------------------------------------------------------------- #


async def test_an_expiring_token_uses_the_injected_refresher():
    calls: list[str] = []
    client = HubSpotClient(_oauth_portal(expires_at=time.time() + 10), token_refresher=_refresher(calls=calls))

    assert await client._get_fresh_token() == "access-2"
    assert calls == [PORTAL]
    await client.close()


async def test_the_refreshed_token_reaches_the_outgoing_header():
    """A refresh that does not update the header refreshes nothing useful."""
    client = HubSpotClient(_oauth_portal(expires_at=time.time() - 1), token_refresher=_refresher())

    await client._get_fresh_token()

    assert client._client.headers["Authorization"] == "Bearer access-2"
    await client.close()


async def test_a_fresh_token_does_not_call_the_refresher():
    calls: list[str] = []
    client = HubSpotClient(_oauth_portal(), token_refresher=_refresher(calls=calls))

    assert await client._get_fresh_token() == "access-1"
    assert calls == []
    await client.close()


async def test_a_private_app_token_is_never_refreshed():
    calls: list[str] = []
    client = HubSpotClient(
        PortalConfig(portal_id=PORTAL, token="pat", auth_type="private_app"),
        token_refresher=_refresher(calls=calls),
    )

    assert await client._get_fresh_token(force=True) == "pat"
    assert calls == []
    await client.close()


async def test_an_unrotated_refresh_token_is_kept():
    client = HubSpotClient(_oauth_portal(expires_at=time.time() - 1), token_refresher=_refresher())
    await client._get_fresh_token()
    assert client.portal.refresh_token == "refresh-1"
    await client.close()


async def test_a_rotated_refresh_token_is_adopted():
    client = HubSpotClient(
        _oauth_portal(expires_at=time.time() - 1),
        token_refresher=_refresher({"access_token": "a2", "expires_in": 1800, "refresh_token": "refresh-2"}),
    )
    await client._get_fresh_token()
    assert client.portal.refresh_token == "refresh-2"
    await client.close()


async def test_concurrent_refreshes_call_the_refresher_once():
    import asyncio

    calls: list[str] = []

    async def slow(portal):
        calls.append(portal.portal_id)
        await asyncio.sleep(0.01)
        return {"access_token": "access-2", "expires_in": 1800}

    client = HubSpotClient(_oauth_portal(expires_at=time.time() - 1), token_refresher=slow)
    await asyncio.gather(*(client._get_fresh_token() for _ in range(5)))

    assert len(calls) == 1
    await client.close()


# --------------------------------------------------------------------------- #
# The default is unchanged
# --------------------------------------------------------------------------- #


async def test_the_default_refresher_still_writes_the_portal_file(tmp_path, monkeypatch):
    """stdio must keep working exactly as it did."""
    from pathlib import Path

    from hubspot_mcp import config

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    # Env rather than patching `app_credentials.get_client_id`: `oauth_flow`
    # imports that name directly at module load, so an attribute patch never
    # reaches it. `get_client_id` reads the environment at call time.
    monkeypatch.setenv("HUBSPOT_CLIENT_ID", "cid")
    monkeypatch.setenv("HUBSPOT_CLIENT_SECRET", "secret")

    client = HubSpotClient(_oauth_portal(expires_at=time.time() - 1))
    with respx.mock:
        respx.post(url__regex=r".*/oauth/.*/token").mock(
            return_value=httpx.Response(200, json={"access_token": "access-2", "expires_in": 1800})
        )
        assert await client._get_fresh_token() == "access-2"
    await client.close()

    assert config.load_portal_config(PORTAL).token == "access-2"


# --------------------------------------------------------------------------- #
# Re-crediting a pooled client
# --------------------------------------------------------------------------- #


async def test_update_credentials_swaps_the_token_without_rebuilding():
    """A pooled client must adopt a new token without discarding its connections."""
    client = HubSpotClient(_oauth_portal())
    transport = client._client

    client.update_credentials(_oauth_portal(token="access-9", expires_at=time.time() + 1800))

    assert client._client is transport, "rebuilt the client and dropped its connection pool"
    assert client._client.headers["Authorization"] == "Bearer access-9"
    assert client.portal.token == "access-9"
    await client.close()


async def test_update_credentials_does_not_leak_the_previous_token():
    client = HubSpotClient(_oauth_portal(token="secret-one"))
    client.update_credentials(_oauth_portal(token="secret-two"))
    assert "secret-one" not in client._client.headers["Authorization"]
    await client.close()


@pytest.mark.parametrize("auth_type", ["oauth", "private_app"])
async def test_update_credentials_works_for_either_auth_type(auth_type):
    client = HubSpotClient(PortalConfig(portal_id=PORTAL, token="a", auth_type=auth_type))
    client.update_credentials(PortalConfig(portal_id=PORTAL, token="b", auth_type=auth_type))
    assert client._client.headers["Authorization"] == "Bearer b"
    await client.close()
