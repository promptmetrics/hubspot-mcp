import httpx
import pytest

# hubspot-mcp deliberately uses the newer date-based OAuth token endpoint
# (/oauth/2026-03/token, with a 404-only fallback to /oauth/v3/token) where
# hubspot-claude still posts to /oauth/v1/token. Import the real value instead
# of hard-coding a URL, so these mocks follow the implementation.
from hubspot_mcp.app_credentials import _TOKEN_URL
from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig


@pytest.mark.asyncio
async def test_client_get_success(respx_mock):
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(200, json={"id": "1", "properties": {"email": "a@b.com"}})
    )
    resp = await client.get("/crm/v3/objects/contacts/1", portal_id="123")
    assert resp.body["id"] == "1"
    await client.close()


@pytest.mark.asyncio
async def test_client_post_success(respx_mock):
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts").mock(
        return_value=httpx.Response(201, json={"id": "2"})
    )
    resp = await client.post("/crm/v3/objects/contacts", portal_id="123", body={"properties": {"email": "new@example.com"}})
    assert resp.body["id"] == "2"
    await client.close()


@pytest.mark.asyncio
async def test_client_rate_limit(respx_mock):
    from hubspot_mcp.errors import RateLimitError
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    route = respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "0"})
    )
    with pytest.raises(RateLimitError):
        await client.get("/crm/v3/objects/contacts/1", portal_id="123")
    # GET 429 retries up to 3 total attempts before raising (budget exhausted);
    # Retry-After:0 keeps the test fast while still exercising the real sleep.
    assert len(route.calls) == 3
    await client.close()


@pytest.mark.asyncio
async def test_client_429_get_retries_then_succeeds(respx_mock):
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"id": "1"})

    route = respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        side_effect=handler
    )
    resp = await client.get("/crm/v3/objects/contacts/1", portal_id="123")
    assert resp.body["id"] == "1"
    assert len(route.calls) == 2
    await client.close()


@pytest.mark.asyncio
async def test_client_429_post_still_raises(respx_mock):
    from hubspot_mcp.errors import RateLimitError
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    route = respx_mock.post("https://api.hubapi.com/crm/v3/objects/contacts").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "0"})
    )
    with pytest.raises(RateLimitError):
        await client.post(
            "/crm/v3/objects/contacts",
            portal_id="123",
            body={"properties": {"email": "x@y.com"}},
        )
    # Writes never retry on 429 — the loop's pause/approve path is the safe
    # surface for non-idempotent side effects.
    assert len(route.calls) == 1
    await client.close()


@pytest.mark.asyncio
async def test_client_429_retry_sleep_is_capped(respx_mock, monkeypatch):
    import asyncio
    from hubspot_mcp.errors import RateLimitError
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    route = respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "9999"})
    )
    # An unreasonable Retry-After must not hang the coroutine: each retry sleep
    # is capped at _MAX_RETRY_AFTER_SECONDS. Capture the slept values without
    # actually waiting (real sleep(0) yields once).
    slept = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(RateLimitError) as exc_info:
        await client.get("/crm/v3/objects/contacts/1", portal_id="123")
    # 3 attempts -> 2 sleeps, each capped at 60 (never the raw 9999).
    assert slept == [60, 60]
    assert len(route.calls) == 3
    # The raised error still carries the server's raw value for the caller.
    assert exc_info.value.retry_after == 9999
    await client.close()


@pytest.mark.asyncio
async def test_client_hubspot_error(respx_mock):
    from hubspot_mcp.errors import HubSpotError
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    with pytest.raises(HubSpotError):
        await client.get("/crm/v3/objects/contacts/1", portal_id="123")
    await client.close()


@pytest.mark.asyncio
async def test_client_401_triggers_refresh(respx_mock, monkeypatch, tmp_path):
    import time
    from pathlib import Path
    from hubspot_mcp.app_credentials import save_app_credentials
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    save_app_credentials(client_id="client-123", client_secret="secret-456")

    client = HubSpotClient(PortalConfig(
        portal_id="123",
        token="expired-token",
        auth_type="oauth",
        refresh_token="refresh-123",
        expires_at=time.time() + 10000,
    ))

    call_count = {"n": 0}
    def handler(request):
        auth = request.headers.get("Authorization", "")
        call_count["n"] += 1
        if call_count["n"] == 1:
            assert "expired-token" in auth
            return httpx.Response(401, json={"message": "Token expired"})
        assert "valid-token" in auth
        return httpx.Response(200, json={"id": "1"})

    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(side_effect=handler)
    respx_mock.post(_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "valid-token",
            "refresh_token": "refresh-123",
            "expires_in": 21600,
        })
    )

    resp = await client.get("/crm/v3/objects/contacts/1", portal_id="123")
    assert resp.body["id"] == "1"
    assert call_count["n"] == 2
    await client.close()


@pytest.mark.asyncio
async def test_client_401_after_refresh_raises(respx_mock, monkeypatch, tmp_path):
    import time
    from hubspot_mcp.errors import HubSpotError
    from hubspot_mcp.app_credentials import save_app_credentials

    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    save_app_credentials(client_id="client-123", client_secret="secret-456")

    client = HubSpotClient(PortalConfig(
        portal_id="123",
        token="bad-token",
        auth_type="oauth",
        refresh_token="refresh-123",
        expires_at=time.time() - 100,
    ))

    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(401, json={"message": "Token expired"})
    )
    respx_mock.post(_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "still-bad-token",
            "refresh_token": "refresh-123",
            "expires_in": 21600,
        })
    )

    with pytest.raises(HubSpotError, match="Token invalid after refresh"):
        await client.get("/crm/v3/objects/contacts/1", portal_id="123")
    await client.close()


@pytest.mark.asyncio
async def test_client_400_validation_with_field_errors(respx_mock):
    from hubspot_mcp.errors import HubSpotError, ErrorCategory
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(
            400,
            json={"errors": [{"field": "email", "message": "invalid"}]},
        )
    )
    with pytest.raises(HubSpotError) as exc_info:
        await client.get("/crm/v3/objects/contacts/1", portal_id="123")
    assert exc_info.value.category == ErrorCategory.VALIDATION
    assert exc_info.value.field_errors == [{"field": "email", "message": "invalid"}]
    await client.close()


@pytest.mark.asyncio
async def test_client_400_validation_without_errors_key(respx_mock):
    from hubspot_mcp.errors import HubSpotError, ErrorCategory
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(400, json={"message": "Bad request"})
    )
    with pytest.raises(HubSpotError) as exc_info:
        await client.get("/crm/v3/objects/contacts/1", portal_id="123")
    assert exc_info.value.category == ErrorCategory.VALIDATION
    assert exc_info.value.field_errors is None
    await client.close()


@pytest.mark.asyncio
async def test_client_401_auth_category(respx_mock, monkeypatch, tmp_path):
    import time
    from hubspot_mcp.errors import HubSpotError, ErrorCategory
    from hubspot_mcp.app_credentials import save_app_credentials

    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    save_app_credentials(client_id="client-123", client_secret="secret-456")

    client = HubSpotClient(PortalConfig(
        portal_id="123",
        token="bad-token",
        auth_type="oauth",
        refresh_token="refresh-123",
        expires_at=time.time() - 100,
    ))

    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(401, json={"message": "Token expired"})
    )
    respx_mock.post(_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "still-bad-token",
            "refresh_token": "refresh-123",
            "expires_in": 21600,
        })
    )

    with pytest.raises(HubSpotError) as exc_info:
        await client.get("/crm/v3/objects/contacts/1", portal_id="123")
    assert exc_info.value.category == ErrorCategory.AUTH
    await client.close()


@pytest.mark.asyncio
async def test_client_403_scope_category(respx_mock):
    from hubspot_mcp.errors import ScopeError, ErrorCategory
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )
    with pytest.raises(ScopeError) as exc_info:
        await client.get(
            "/crm/v3/objects/contacts/1",
            portal_id="123",
            expected_scopes=["crm.objects.contacts.read"],
        )
    assert exc_info.value.category == ErrorCategory.SCOPE
    await client.close()


@pytest.mark.asyncio
async def test_client_403_scope_without_expected_scopes(respx_mock):
    from hubspot_mcp.errors import HubSpotError, ErrorCategory
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )
    with pytest.raises(HubSpotError) as exc_info:
        await client.get("/crm/v3/objects/contacts/1", portal_id="123")
    assert exc_info.value.category == ErrorCategory.SCOPE
    await client.close()


@pytest.mark.asyncio
async def test_client_404_not_found_category(respx_mock):
    from hubspot_mcp.errors import HubSpotError, ErrorCategory
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(404, json={"message": "Not found"})
    )
    with pytest.raises(HubSpotError) as exc_info:
        await client.get("/crm/v3/objects/contacts/1", portal_id="123")
    assert exc_info.value.category == ErrorCategory.NOT_FOUND
    await client.close()


@pytest.mark.asyncio
async def test_client_409_conflict_category(respx_mock):
    from hubspot_mcp.errors import HubSpotError, ErrorCategory
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(409, json={"message": "Conflict"})
    )
    with pytest.raises(HubSpotError) as exc_info:
        await client.get("/crm/v3/objects/contacts/1", portal_id="123")
    assert exc_info.value.category == ErrorCategory.CONFLICT
    await client.close()


@pytest.mark.asyncio
async def test_client_500_server_category(respx_mock):
    from hubspot_mcp.errors import HubSpotError, ErrorCategory
    client = HubSpotClient(PortalConfig(portal_id="123", token="test-token"))
    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    with pytest.raises(HubSpotError) as exc_info:
        await client.get("/crm/v3/objects/contacts/1", portal_id="123")
    assert exc_info.value.category == ErrorCategory.SERVER
    await client.close()


# ---------------------------------------------------------------------------
# Phase 3 PR-B (back-pressure): rate-limit header parsing + last-seen snapshot.
# ---------------------------------------------------------------------------


def test_parse_rate_limit_valid_headers():
    from hubspot_mcp.client import parse_rate_limit

    headers = {
        "X-HubSpot-RateLimit-Remaining": "37",
        "X-HubSpot-RateLimit-Interval-Milliseconds": "10000",
    }
    remaining, reset_seconds = parse_rate_limit(headers)
    assert remaining == 37
    assert reset_seconds == 10.0


def test_parse_rate_limit_case_insensitive():
    # httpx lowercases header keys via dict(resp.headers); parsing must not care.
    from hubspot_mcp.client import parse_rate_limit

    remaining, reset_seconds = parse_rate_limit(
        {"x-hubspot-ratelimit-remaining": "5", "x-hubspot-ratelimit-interval-milliseconds": "5000"}
    )
    assert remaining == 5
    assert reset_seconds == 5.0


def test_parse_rate_limit_absent_returns_none():
    from hubspot_mcp.client import parse_rate_limit

    assert parse_rate_limit({}) == (None, None)
    assert parse_rate_limit({"content-type": "application/json"}) == (None, None)


def test_parse_rate_limit_malformed_degrades_per_slot():
    from hubspot_mcp.client import parse_rate_limit

    remaining, reset_seconds = parse_rate_limit(
        {"X-HubSpot-RateLimit-Remaining": "notanint",
         "X-HubSpot-RateLimit-Interval-Milliseconds": "also-bad"}
    )
    assert remaining is None
    assert reset_seconds is None
    # A good remaining with a missing interval still yields the remaining.
    remaining, reset_seconds = parse_rate_limit({"X-HubSpot-RateLimit-Remaining": "12"})
    assert remaining == 12
    assert reset_seconds is None


def test_record_and_get_last_rate_state(monkeypatch):
    import hubspot_mcp.client as client_mod
    from hubspot_mcp.client import get_last_rate_state, record_rate_state

    monkeypatch.setattr(client_mod, "_LAST_RATE_STATE", {})
    monkeypatch.setattr(client_mod.time, "time", lambda: 1000.0)
    remaining, reset_at = record_rate_state(
        "portal-x",
        {"X-HubSpot-RateLimit-Remaining": "4", "X-HubSpot-RateLimit-Interval-Milliseconds": "10000"},
    )
    assert remaining == 4
    assert reset_at == 1010.0  # now + interval seconds
    assert get_last_rate_state("portal-x") == (4, 1010.0)


def test_record_rate_state_preserves_prior_on_unparseable(monkeypatch):
    import hubspot_mcp.client as client_mod
    from hubspot_mcp.client import get_last_rate_state, record_rate_state

    monkeypatch.setattr(client_mod, "_LAST_RATE_STATE", {})
    record_rate_state(
        "portal-y",
        {"X-HubSpot-RateLimit-Remaining": "9", "X-HubSpot-RateLimit-Interval-Milliseconds": "10000"},
    )
    prior = get_last_rate_state("portal-y")
    record_rate_state("portal-y", {"content-type": "application/json"})
    assert get_last_rate_state("portal-y") == prior


@pytest.mark.asyncio
async def test_request_records_rate_state(respx_mock, monkeypatch):
    import hubspot_mcp.client as client_mod

    monkeypatch.setattr(client_mod, "_LAST_RATE_STATE", {})
    client = HubSpotClient(PortalConfig(portal_id="rate-portal", token="test-token"))
    respx_mock.get("https://api.hubapi.com/crm/v3/objects/contacts/1").mock(
        return_value=httpx.Response(
            200,
            json={"id": "1"},
            headers={"X-HubSpot-RateLimit-Remaining": "3",
                     "X-HubSpot-RateLimit-Interval-Milliseconds": "10000"},
        )
    )
    await client.get("/crm/v3/objects/contacts/1", portal_id="rate-portal")
    remaining, _reset = client_mod.get_last_rate_state("rate-portal")
    assert remaining == 3
    await client.close()
