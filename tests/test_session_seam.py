"""The per-request session seam (Phase 3, stage 1).

Phase 1 and 2 resolve one portal per process at startup: `app_lifespan` builds a
single `HubSpotClient` and every tool reads it out of the lifespan context. A
hosted deployment has to decide whose portal a request acts on *per request*,
from the caller's access token.

`_session(ctx)` is that decision point, and these tests pin that it really is
the only one — that installing a resolver diverts the entire 86-tool surface,
the safety layer and the 44 charters, with no tool body changing.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from hubspot_mcp import config, persistence, server
from hubspot_mcp.config import PortalConfig

PORTAL_A = "11111111"
PORTAL_B = "22222222"

SERVER_SRC = Path(inspect.getfile(server))


class RecordingClient:
    """Stands in for a per-user HubSpot client; records what it was asked for."""

    def __init__(self, portal_id: str) -> None:
        self.portal_id = portal_id
        self.calls: list[tuple[str, str]] = []

    def _record(self, method: str):
        async def _call(path: str, *a, **kw):
            self.calls.append((method, path))
            return {}

        return _call

    def __getattr__(self, name: str):
        if name in ("get", "post", "patch", "put", "delete", "post_files"):
            return self._record(name.upper())
        raise AttributeError(name)

    async def close(self) -> None:
        pass


def _session_for(portal_id: str) -> dict[str, Any]:
    return {
        "client": RecordingClient(portal_id),
        "cache": None,
        "portal_config": PortalConfig(portal_id=portal_id, token="tok", scopes_granted=[]),
        "portal_id": portal_id,
        "auth_error": None,
        "capabilities": None,
    }


@pytest.fixture(autouse=True)
def _reset_resolver():
    server.set_session_resolver(None)
    yield
    server.set_session_resolver(None)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(persistence, "CONFIG_DIR", tmp_path)
    return tmp_path


# --------------------------------------------------------------------------- #
# The seam exists and is honoured
# --------------------------------------------------------------------------- #


async def test_no_resolver_falls_back_to_the_single_portal(monkeypatch):
    """stdio keeps exactly its Phase 1 behaviour: one portal per process."""
    expected = _session_for(PORTAL_A)
    monkeypatch.setattr(server, "_lifespan", lambda ctx: expected)
    assert await server._session(object()) is expected


async def test_a_resolver_overrides_the_lifespan(monkeypatch):
    hosted = _session_for(PORTAL_B)
    monkeypatch.setattr(server, "_lifespan", lambda ctx: _session_for(PORTAL_A))

    async def resolver(ctx):
        return hosted

    server.set_session_resolver(resolver)
    assert await server._session(object()) is hosted


async def test_the_resolver_sees_the_request_context(monkeypatch):
    """The hosted resolver reads the caller's token off `ctx`; it must receive it."""
    seen: list[Any] = []
    monkeypatch.setattr(server, "_lifespan", lambda ctx: _session_for(PORTAL_A))

    async def resolver(ctx):
        seen.append(ctx)
        return _session_for(PORTAL_B)

    server.set_session_resolver(resolver)
    sentinel = object()
    await server._session(sentinel)
    assert seen == [sentinel]


async def test_set_session_resolver_none_restores_single_portal(monkeypatch):
    local = _session_for(PORTAL_A)
    monkeypatch.setattr(server, "_lifespan", lambda ctx: local)

    async def resolver(ctx):
        return _session_for(PORTAL_B)

    server.set_session_resolver(resolver)
    server.set_session_resolver(None)
    assert await server._session(object()) is local


# --------------------------------------------------------------------------- #
# The whole tool surface goes through it
# --------------------------------------------------------------------------- #


def _session_consumers() -> dict[str, list[str]]:
    """Map every function in server.py to how it obtains a session."""
    tree = ast.parse(SERVER_SRC.read_text())
    found: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                if call.func.id in {"_session", "_lifespan", "_safety_ctx"}:
                    found.setdefault(node.name, []).append(call.func.id)
    return found


def test_nothing_reaches_past_the_seam_to_the_lifespan():
    """`_lifespan` is the single-portal implementation, not an accessor to use.

    A tool calling it directly would always answer for the process-wide portal,
    which on a multi-tenant deployment is another user's CRM.
    """
    offenders = [
        fn
        for fn, calls in _session_consumers().items()
        if "_lifespan" in calls and fn != "_session"
    ]
    assert offenders == [], (
        f"these bypass the session seam and must call `_session`: {offenders}"
    )


def test_the_seam_is_asynchronous():
    """Hosted resolution reads a connection store; it cannot be synchronous."""
    assert inspect.iscoroutinefunction(server._session)


async def test_a_domain_tool_uses_the_resolved_session(isolated, monkeypatch):
    """The decisive one: 86 tool bodies unchanged, all diverted by one function."""
    hosted = _session_for(PORTAL_B)

    async def resolver(ctx):
        return hosted

    monkeypatch.setattr(server, "_lifespan", lambda ctx: _session_for(PORTAL_A))
    server.set_session_resolver(resolver)

    captured: dict[str, Any] = {}

    async def fake_handle_tool(client, cache, portal_config, params):
        captured["portal_id"] = portal_config.portal_id
        return {"ok": True, "data": {"status": "preview", "action_id": "act-1"}}

    monkeypatch.setattr(server, "handle_tool", fake_handle_tool)

    wrapper = server._make_domain_wrapper(
        next(t for t in server.list_tools() if t.name == "hubspot_get_object")
    )
    await wrapper(ctx=None, object_type="contacts", object_id="1")

    assert captured["portal_id"] == PORTAL_B, "the tool acted on the lifespan portal, not the caller's"


async def test_the_safety_layer_uses_the_resolved_session(monkeypatch):
    """Approve/reject/undo must never act on a portal the caller does not own."""
    hosted = _session_for(PORTAL_B)

    async def resolver(ctx):
        return hosted

    monkeypatch.setattr(server, "_lifespan", lambda ctx: _session_for(PORTAL_A))
    server.set_session_resolver(resolver)

    assert (await server._safety_ctx(None))["portal_id"] == PORTAL_B


async def test_an_auth_error_from_the_resolver_still_surfaces(monkeypatch):
    """A user with no HubSpot connection gets the guidance, not a stack trace."""
    from mcp.server.mcpserver.exceptions import ToolError

    async def resolver(ctx):
        session = _session_for(PORTAL_B)
        session["auth_error"] = "Connect your HubSpot account: https://example.com/connect"
        return session

    monkeypatch.setattr(server, "_lifespan", lambda ctx: _session_for(PORTAL_A))
    server.set_session_resolver(resolver)

    with pytest.raises(ToolError, match="Connect your HubSpot account"):
        await server._safety_ctx(None)
