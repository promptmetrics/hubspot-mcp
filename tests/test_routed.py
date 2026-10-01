"""The routed surface: three meta-tools over the same handlers as ``/mcp``.

Pins what the routed mode promises: a three-tool ``tools/list``; ``call_hubspot``
reaches the write gate exactly as the registered wrapper does; routing falls
back to the keyword router when Jev cannot answer; Jev answers turn into
probabilities, charters, tool schemas and the appended safety tools; and every
proxied call leaves a trace event labelled ``routed``.
"""
from __future__ import annotations

import json

import pytest
from mcp import Client

from hubspot_mcp.client import APIResponse
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.jev_router import JevUnavailable

PORTAL = "99999999"


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __getattr__(self, name: str):
        if name not in ("get", "post", "patch", "put", "delete", "post_files"):
            raise AttributeError(name)

        async def _call(path: str, *a, **kw):
            self.calls.append((name.upper(), path))
            return APIResponse(
                status_code=200,
                body={"id": "1", "properties": {"amount": "100", "dealname": "Test deal"}},
                headers={},
            )

        return _call

    async def close(self) -> None:
        pass


@pytest.fixture
def routed_server(tmp_path, monkeypatch):
    from hubspot_mcp import config, persistence, routed, server

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(persistence, "CONFIG_DIR", tmp_path)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("HUBSPOT_MCP_TRACE", "1")
    monkeypatch.setattr(routed, "_TOOL_INDEX", None)
    http = _FakeClient()
    lifespan = {
        "client": http,
        "cache": None,
        "portal_config": PortalConfig(portal_id=PORTAL, token="t", scopes_granted=[]),
        "portal_id": PORTAL,
        "auth_error": None,
    }
    monkeypatch.setattr(server, "_lifespan", lambda ctx: lifespan)
    monkeypatch.setattr(server, "_session_resolver", None)
    return server, http


def _data(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def _text(result) -> str:
    return result.content[0].text


async def test_routed_lists_exactly_four_tools(routed_server):
    server, _ = routed_server
    names = {t.name for t in await server.routed.list_tools()}
    assert names == {"find_capabilities", "call_hubspot", "load_charter", "load_skill"}
    assert len(await server.mcp.list_tools()) == 88, "the full surface: 86 + the two skill tools"


async def test_call_hubspot_reaches_the_write_gate_then_approves_through_the_proxy(routed_server):
    server, http = routed_server
    async with Client(server.routed) as client:
        result = await client.call_tool(
            "call_hubspot",
            {
                "tool": "hubspot_update_object",
                "args": {"object_type": "deals", "object_id": "1", "properties": {"amount": "5"}},
            },
        )
        assert result.is_error is False, _text(result)
        data = _data(result)
        assert data.get("status") == "preview", data
        assert data.get("action_id")
        assert "CONFIRM" in json.dumps(data), "amount is a sensitive property and must not auto-apply"
        assert all(m == "GET" for m, _ in http.calls), "nothing may be written before approval"

        approved = await client.call_tool(
            "call_hubspot",
            {"tool": "hubspot_approve_write", "args": {"action_id": data["action_id"]}},
        )
        assert approved.is_error is False, _text(approved)
    assert any(m == "PATCH" for m, _ in http.calls), "approval through the proxy must apply the write"


async def test_call_hubspot_rejects_unknown_tool_and_invalid_args(routed_server):
    server, http = routed_server
    async with Client(server.routed) as client:
        unknown = await client.call_tool("call_hubspot", {"tool": "hubspot_nope", "args": {}})
        assert unknown.is_error is True
        assert "Unknown HubSpot tool" in _text(unknown)

        bad = await client.call_tool(
            "call_hubspot", {"tool": "hubspot_update_object", "args": {"object_type": "deals"}}
        )
        assert bad.is_error is True
        assert "Invalid args" in _text(bad)
    assert http.calls == [], "a rejected call must not reach HubSpot"


async def test_find_capabilities_falls_back_to_keyword_routing(routed_server, monkeypatch):
    server, _ = routed_server
    from hubspot_mcp import jev_router

    async def _down(state, questions):
        raise JevUnavailable("no key")

    monkeypatch.setattr(jev_router, "jev_evaluate", _down)
    async with Client(server.routed) as client:
        result = await client.call_tool(
            "find_capabilities", {"task": "Show me all active workflows"}
        )
    assert result.is_error is False, _text(result)
    data = _data(result)
    assert data["router"] == "keyword"
    assert "keyword routing used" in data["note"]
    assert data["primary_charter"] == "workflows"
    assert data["tools"], "charter tools must be merged in even without router picks"
    assert all("input_schema" in t and t["input_schema"]["type"] == "object" for t in data["tools"])
    assert "load_charter" in data["next_step"]


async def test_find_capabilities_turns_jev_answers_into_a_decision(routed_server, monkeypatch):
    server, _ = routed_server
    from hubspot_mcp import jev_router

    seen: dict = {}

    async def _canned(state, questions):
        seen["state"] = state
        seen["questions"] = questions
        return {
            "answers": {
                "charter": {
                    "type": "choice",
                    "choice": "objects",
                    "probabilities": {"objects": 0.9, "associations": 0.06, "none": 0.01},
                },
                "c_objects": {"type": "boolean", "probability": 0.92},
                "c_associations": {"type": "boolean", "probability": 0.61},
                "c_workflows": {"type": "boolean", "probability": 0.02},
                "t_hubspot_update_object": {"type": "boolean", "probability": 0.8},
                "t_hubspot_list_workflows": {"type": "boolean", "probability": 0.1},
            },
            "usage": {"inputTokens": 3000, "outputTokens": 0},
            "providerMetadata": {"gateway": {"cost": "0.00024"}},
        }

    monkeypatch.setattr(jev_router, "jev_evaluate", _canned)
    async with Client(server.routed) as client:
        result = await client.call_tool("find_capabilities", {"task": "Change the Acme deal amount to 5"})
    assert result.is_error is False, _text(result)
    data = _data(result)

    assert seen["state"] == "Change the Acme deal amount to 5"
    assert seen["questions"]["charter"]["type"] == "choice"
    assert "none" in seen["questions"]["charter"]["criteria"]
    assert "t_hubspot_update_object" in seen["questions"]
    assert "t_hubspot_approve_write" not in seen["questions"], "safety tools are not routed over"

    assert data["router"] == "jev"
    assert data["primary_charter"] == "objects"
    assert [c["name"] for c in data["charters"]] == ["objects", "associations"]
    assert data["charters"][0]["probability"] == 0.9
    assert data["routing_cost_usd"] == 0.00024
    by_name = {t["name"]: t for t in data["tools"]}
    assert by_name["hubspot_update_object"]["picked_by"] == "router+charter"
    assert by_name["hubspot_update_object"]["probability"] == 0.8
    assert "hubspot_list_workflows" not in by_name, "below threshold and outside the charters"
    for safety in ("hubspot_approve_write", "hubspot_reject_write", "hubspot_undo_write"):
        assert by_name[safety]["picked_by"] == "safety"


async def test_load_charter_prefixes_the_runtime_header(routed_server):
    server, _ = routed_server
    async with Client(server.routed) as client:
        ok = await client.call_tool("load_charter", {"name": "objects"})
        missing = await client.call_tool("load_charter", {"name": "nope"})
    assert ok.is_error is False
    text = _text(ok)
    assert text.startswith("# Runtime")
    assert "call_hubspot(" in text
    assert "hubspot_update_object" in text
    assert missing.is_error is True
    assert "Unknown charter" in _text(missing)


async def test_every_proxied_call_leaves_a_routed_trace(routed_server):
    server, _ = routed_server
    from hubspot_mcp.trace import get_recent_traces

    async with Client(server.routed) as client:
        result = await client.call_tool(
            "call_hubspot", {"tool": "hubspot_get_object", "args": {"object_type": "contacts", "object_id": "1"}}
        )
    assert result.is_error is False, _text(result)
    events = [e for e in get_recent_traces(PORTAL) if e.event_type == "tool_call"]
    assert len(events) == 1
    assert events[0].data["tool_name"] == "hubspot_get_object"
    assert events[0].data["mode"] == "routed"
    assert events[0].data["ok"] is True
    assert events[0].data["elapsed_ms"] >= 0


async def test_full_surface_calls_are_traced_as_full(routed_server):
    server, _ = routed_server
    from hubspot_mcp.trace import get_recent_traces

    async with Client(server.mcp) as client:
        result = await client.call_tool("hubspot_get_object", {"object_type": "contacts", "object_id": "1"})
    assert result.is_error is False
    events = [e for e in get_recent_traces(PORTAL) if e.event_type == "tool_call"]
    assert [e.data["mode"] for e in events] == ["full"]


class _App:
    """A stand-in Starlette app: records lifespan entry and which app got a request."""

    def __init__(self, log: list[str], tag: str) -> None:
        self.log, self.tag = log, tag
        outer = self

        class _Router:
            def lifespan_context(self, app):
                from contextlib import asynccontextmanager

                @asynccontextmanager
                async def _cm():
                    outer.log.append(f"{outer.tag}:up")
                    yield
                    outer.log.append(f"{outer.tag}:down")

                return _cm()

        self.router = _Router()

    async def __call__(self, scope, receive, send):
        self.log.append(f"{self.tag}:{scope['path']}")


async def test_split_surfaces_dispatches_by_path_and_runs_both_lifespans():
    from hubspot_mcp.server import _SplitSurfaces

    log: list[str] = []
    app = _SplitSurfaces(_App(log, "full"), _App(log, "routed"))

    messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    sent: list[dict] = []

    async def receive():
        return next(messages)

    async def send(m):
        sent.append(m)

    await app({"type": "lifespan"}, receive, send)
    assert log == ["full:up", "routed:up", "routed:down", "full:down"]
    assert [m["type"] for m in sent] == ["lifespan.startup.complete", "lifespan.shutdown.complete"]

    for path, want in [
        ("/mcp", "full"),
        ("/mcp/routed", "routed"),
        ("/mcp/routed/", "routed"),
        ("/mcp/routedx", "full"),
        ("/.well-known/oauth-protected-resource/mcp/routed", "routed"),
        ("/.well-known/oauth-protected-resource/mcp", "full"),
        ("/healthz", "full"),
    ]:
        log.clear()
        await app({"type": "http", "path": path}, receive, send)
        assert log == [f"{want}:{path}"], path


async def test_engagement_object_types_are_valid_and_unknown_types_are_readable(routed_server):
    server, http = routed_server
    async with Client(server.routed) as client:
        ok = await client.call_tool(
            "call_hubspot", {"tool": "hubspot_get_object", "args": {"object_type": "notes", "object_id": "1"}}
        )
        bad = await client.call_tool(
            "call_hubspot", {"tool": "hubspot_get_object", "args": {"object_type": "nopes", "object_id": "1"}}
        )
    assert ok.is_error is False, _text(ok)
    assert ("GET", "/crm/v3/objects/notes/1") in http.calls
    assert bad.is_error is True
    assert "Invalid object_type 'nopes'" in _text(bad), "the validator's message must reach the model"


async def test_undo_of_an_engagement_create_deletes_the_typed_object(routed_server):
    server, http = routed_server
    from hubspot_mcp.handlers import undo_action

    snapshot = {
        "metadata": {
            "intent_type": "create",
            "undoable": True,
            "tool_name": "hubspot_create_note",
            "created_ids": ["42"],
        }
    }
    ok, message = await undo_action(snapshot, PORTAL, None, client=http)
    assert ok is True, message
    assert ("DELETE", "/crm/v3/objects/notes/42") in http.calls
