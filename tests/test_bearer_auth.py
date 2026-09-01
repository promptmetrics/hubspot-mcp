"""Per-request bearer auth for the HTTP transport (Phase 2, Task 3).

Over stdio the transport is a pipe to a process the user started, so there is
nothing to authenticate. Over HTTPS the same server answers the internet, and
2026-07-28 removed the handshake (SEP-2575) — there is no connection-setup
phase to authenticate in and no session identity to carry a decision forward.
So the check has to run on every request, and these tests pin that it does.
"""
from __future__ import annotations

import pytest

from hubspot_mcp.auth.bearer_middleware import (
    MIN_SECRET_LENGTH,
    SECRET_ENV,
    BearerAuthMiddleware,
    MissingServerSecret,
    load_server_secret,
    resolve_server_secret,
)

SECRET = "s" * MIN_SECRET_LENGTH
# Binding every interface is the subject of these tests, not a mistake in them.
PUBLIC_BIND = "0.0.0.0"  # noqa: S104


class SpyApp:
    """Records every request that gets past the middleware."""

    def __init__(self) -> None:
        self.reached: list[str] = []

    async def __call__(self, scope, receive, send) -> None:
        self.reached.append(scope.get("path", ""))
        body = b'{"ok":true}'
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})


async def _call(app, path="/mcp", headers=None, scope_type="http", method="POST"):
    """Drive one ASGI request and return `(status, headers, body)`."""
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": scope_type,
        "path": path,
        "method": method,
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    await app(scope, receive, send)
    if not sent:
        return None, {}, b""
    start = sent[0]
    body = b"".join(m.get("body", b"") for m in sent[1:])
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, body


@pytest.fixture
def guarded():
    spy = SpyApp()
    return BearerAuthMiddleware(spy, secret=SECRET), spy


# --------------------------------------------------------------------------- #
# Every request is checked
# --------------------------------------------------------------------------- #


async def test_correct_token_reaches_the_app(guarded):
    app, spy = guarded
    status, _, _ = await _call(app, headers={"Authorization": f"Bearer {SECRET}"})
    assert status == 200
    assert spy.reached == ["/mcp"]


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-authorization-header"),
        pytest.param({"Authorization": ""}, id="empty-header"),
        pytest.param({"Authorization": SECRET}, id="no-bearer-scheme"),
        pytest.param({"Authorization": f"Basic {SECRET}"}, id="wrong-scheme"),
        pytest.param({"Authorization": "Bearer "}, id="empty-token"),
        pytest.param({"Authorization": "Bearer wrong"}, id="wrong-token"),
        pytest.param({"Authorization": f"Bearer {SECRET}x"}, id="token-with-suffix"),
        pytest.param({"Authorization": f"Bearer {SECRET[:-1]}"}, id="token-truncated"),
    ],
)
async def test_bad_credentials_are_rejected(guarded, headers):
    app, spy = guarded
    status, response_headers, _ = await _call(app, headers=headers)
    assert status == 401
    assert response_headers["www-authenticate"] == "Bearer"
    assert spy.reached == [], "an unauthenticated request reached the app"


async def test_every_request_is_checked_not_just_the_first(guarded):
    """There is no handshake under 2026-07-28, so nothing may be trusted twice."""
    app, spy = guarded
    good = {"Authorization": f"Bearer {SECRET}"}

    assert (await _call(app, headers=good))[0] == 200
    assert (await _call(app, headers={"Authorization": "Bearer wrong"}))[0] == 401
    assert (await _call(app, headers=good))[0] == 200

    assert spy.reached == ["/mcp", "/mcp"]


async def test_rejection_does_not_distinguish_failure_modes(guarded):
    """A different message per failure tells an attacker which half to attack."""
    app, _ = guarded
    _, _, missing = await _call(app, headers={})
    _, _, wrong = await _call(app, headers={"Authorization": "Bearer wrong"})
    assert missing == wrong


async def test_healthz_is_public(guarded):
    app, spy = guarded
    status, _, _ = await _call(app, path="/healthz")
    assert status == 200
    assert spy.reached == ["/healthz"]


async def test_lifespan_passes_through(guarded):
    """A guarded lifespan scope would stop the app from ever starting."""
    app, spy = guarded
    await _call(app, path="", scope_type="lifespan")
    assert spy.reached == [""]


def test_secret_is_not_stored_in_the_clear(guarded):
    """A traceback or repr must not be able to print the shared secret."""
    app, _ = guarded
    assert SECRET.encode() not in app._expected
    assert not any(SECRET in str(v) for v in vars(app).values())


def test_empty_secret_is_refused():
    with pytest.raises(ValueError, match="non-empty secret"):
        BearerAuthMiddleware(SpyApp(), secret="")


# --------------------------------------------------------------------------- #
# Fail closed: a network-reachable bind must not start unauthenticated
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("host", [PUBLIC_BIND, "::", "10.0.0.4", "mcp.example.com"])
def test_non_loopback_bind_refuses_to_start_without_a_secret(host):
    with pytest.raises(MissingServerSecret, match="Refusing to serve"):
        resolve_server_secret(host, env={})


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_bind_without_a_secret_is_allowed(host, capsys):
    assert resolve_server_secret(host, env={}) is None
    assert SECRET_ENV in capsys.readouterr().err


def test_short_secret_is_refused_even_on_loopback():
    short = "s" * (MIN_SECRET_LENGTH - 1)
    with pytest.raises(MissingServerSecret, match="characters"):
        resolve_server_secret("127.0.0.1", env={SECRET_ENV: short})


def test_valid_secret_is_returned():
    assert resolve_server_secret(PUBLIC_BIND, env={SECRET_ENV: SECRET}) == SECRET


@pytest.mark.parametrize("raw", ["", "   ", "\n"])
def test_blank_secret_counts_as_unset(raw):
    assert load_server_secret({SECRET_ENV: raw}) is None


def test_secret_is_whitespace_trimmed():
    """A trailing newline from `vercel env pull` must not change the secret."""
    assert load_server_secret({SECRET_ENV: f"  {SECRET}\n"}) == SECRET


# --------------------------------------------------------------------------- #
# The wiring: the hosted entrypoint must be guarded, not just `run()`
# --------------------------------------------------------------------------- #


@pytest.fixture
def single_portal(monkeypatch):
    """A hosted app also has to satisfy the single-tenant guard (Task 5)."""
    monkeypatch.setenv("HUBSPOT_PORTAL", "99999999")
    monkeypatch.delenv("HUBSPOT_TOKEN_11111111", raising=False)


def test_build_http_app_wraps_the_transport_when_a_secret_is_set(monkeypatch, single_portal):
    from hubspot_mcp import server

    monkeypatch.setenv(SECRET_ENV, SECRET)
    app = server.build_http_app(PUBLIC_BIND)
    assert isinstance(app, BearerAuthMiddleware)


def test_build_http_app_refuses_a_public_bind_without_a_secret(monkeypatch, single_portal):
    """The Vercel entrypoint imports this app; unguarded here means unguarded live."""
    from hubspot_mcp import server

    monkeypatch.delenv(SECRET_ENV, raising=False)
    with pytest.raises(MissingServerSecret):
        server.build_http_app(PUBLIC_BIND)


async def test_healthz_reports_ok_without_credentials(monkeypatch, single_portal):
    from hubspot_mcp import __version__, server

    monkeypatch.setenv(SECRET_ENV, SECRET)
    app = server.build_http_app(PUBLIC_BIND)
    status, _, body = await _call(app, path="/healthz", method="GET")
    assert status == 200
    assert b'"status":"ok"' in body.replace(b", ", b",")
    assert __version__.encode() in body


async def test_mcp_endpoint_is_401_without_credentials(monkeypatch, single_portal):
    from hubspot_mcp import server

    monkeypatch.setenv(SECRET_ENV, SECRET)
    app = server.build_http_app(PUBLIC_BIND)
    status, _, _ = await _call(app, path="/mcp")
    assert status == 401
