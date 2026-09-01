"""Multi Round-Trip Requests for the write gate (SEP-2322, protocol 2026-07-28).

2026-07-28 removes the server-initiated back-channel, so ``ctx.elicit`` raises
NoBackChannelError. MRTR replaces it: the tool returns
``resultType: "input_required"``, the client retries the same call with
``inputResponses`` plus the server-minted ``requestState``. That collapses the
write gate from two tool calls into one round-tripped call.

It is strictly opt-in. A client that has not declared form elicitation gets
``MCPError: Elicitation not supported`` for the WHOLE call, so every write would
break -- these tests pin that the classic preview/approve path is still returned
for such clients.
"""
from __future__ import annotations

import mcp_types as T
import pytest
from mcp import Client

from hubspot_mcp.client import APIResponse
from hubspot_mcp.config import PortalConfig

RAW_ARGS = {"method": "POST", "path": "/crm/v3/objects/contacts"}


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __getattr__(self, name: str):
        if name not in ("get", "post", "patch", "put", "delete", "post_files"):
            raise AttributeError(name)

        async def _call(path: str, *a, **kw):
            self.calls.append((name.upper(), path))
            return APIResponse(status_code=200, body={"id": "1"}, headers={})

        return _call

    async def close(self) -> None:
        pass


@pytest.fixture
def server_with_portal(tmp_path, monkeypatch):
    """Import the server and force a usable, isolated lifespan context."""
    from hubspot_mcp import config, persistence, server

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(persistence, "CONFIG_DIR", tmp_path)
    http = _FakeClient()
    lifespan = {
        "client": http,
        "cache": None,
        "portal_config": PortalConfig(portal_id="99999999", token="t", scopes_granted=[]),
        "portal_id": "99999999",
        "auth_error": None,
    }
    monkeypatch.setattr(server, "_lifespan", lambda ctx: lifespan)
    return server, http


async def _accept(ctx, params):
    return T.ElicitResult(action="accept", content={"approve": True, "confirm_count": 1})


async def test_write_asks_inline_and_applies_on_accept(server_with_portal):
    server, http = server_with_portal
    async with Client(server.mcp, elicitation_callback=_accept) as client:
        result = await client.call_tool("hubspot_raw_api", RAW_ARGS)

    assert result.is_error is False
    assert http.calls == [("POST", "/crm/v3/objects/contacts")], "write did not apply after approval"


async def test_confirmation_carries_the_preview_and_demands_the_count(server_with_portal):
    server, _http = server_with_portal
    seen: dict = {}

    async def capture(ctx, params):
        seen["message"] = params.message
        seen["required"] = params.requested_schema.get("required")
        return T.ElicitResult(action="accept", content={"approve": True, "confirm_count": 1})

    async with Client(server.mcp, elicitation_callback=capture) as client:
        await client.call_tool("hubspot_raw_api", RAW_ARGS)

    # A raw_api POST has no undo path -> FULL_GATE, so the typed-count ceremony
    # survives the move to MRTR rather than being quietly dropped.
    assert "FULL_GATE" in seen["message"]
    assert "action_id:" in seen["message"]
    assert "will NOT be undoable" in seen["message"]
    assert seen["required"] == ["approve", "confirm_count"]


async def test_decline_writes_nothing(server_with_portal):
    server, http = server_with_portal

    async def decline(ctx, params):
        return T.ElicitResult(action="decline")

    async with Client(server.mcp, elicitation_callback=decline) as client:
        result = await client.call_tool("hubspot_raw_api", RAW_ARGS)

    assert result.is_error is False
    assert http.calls == [], "declined write still reached HubSpot"


async def test_refusing_approval_in_the_form_writes_nothing(server_with_portal):
    """Accepting the form but answering approve=false must reject, not apply."""
    server, http = server_with_portal

    async def refuse(ctx, params):
        return T.ElicitResult(action="accept", content={"approve": False})

    async with Client(server.mcp, elicitation_callback=refuse) as client:
        await client.call_tool("hubspot_raw_api", RAW_ARGS)

    assert http.calls == []


async def test_client_without_elicitation_gets_the_classic_preview(server_with_portal):
    """The fallback that keeps non-elicitation and handshake-era clients working."""
    server, http = server_with_portal
    async with Client(server.mcp) as client:
        result = await client.call_tool("hubspot_raw_api", RAW_ARGS)

    assert result.is_error is False
    assert http.calls == [], "write applied without any approval"
    text = result.content[0].text if result.content else ""
    assert "action_id" in text and "requires_count" in text


async def test_reads_are_never_round_tripped(server_with_portal):
    """A read must not acquire a confirmation step just because MRTR exists."""
    server, http = server_with_portal
    asked = False

    async def spy(ctx, params):
        nonlocal asked
        asked = True
        return T.ElicitResult(action="decline")

    async with Client(server.mcp, elicitation_callback=spy) as client:
        await client.call_tool("hubspot_raw_api", {"method": "GET", "path": "/crm/v3/objects/contacts/1"})

    assert asked is False
    assert http.calls == [("GET", "/crm/v3/objects/contacts/1")]


async def test_resume_resolves_the_pending_write_instead_of_minting_another(
    server_with_portal,
):
    """The retry must resolve the EXISTING preview, not re-run the tool.

    The client answers a confirmation by re-sending the same tool call. If the
    wrapper fell through to ``handle_tool`` again it would mint a second pending
    preview and orphan the first, leaving an un-approvable record on disk
    forever. Pinned directly rather than relying on the single-POST assertion
    above to notice it.
    """
    from hubspot_mcp.persistence import list_pending

    server, http = server_with_portal
    async with Client(server.mcp, elicitation_callback=_accept) as client:
        await client.call_tool("hubspot_raw_api", RAW_ARGS)

    assert http.calls == [("POST", "/crm/v3/objects/contacts")]
    # The approved preview is cleared on execute, and no second one was created.
    assert list_pending("99999999") == []
