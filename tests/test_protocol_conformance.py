"""MCP 2026-07-28 conformance for the hubspot-mcp server.

Pins the protocol guarantees the migration off FastMCP bought, so a future SDK
bump cannot regress them silently. These have no upstream equivalent —
hubspot-claude is a CLI, not an MCP server.
"""
from __future__ import annotations

import pytest
from mcp import Client
from mcp_types.version import LATEST_PROTOCOL_VERSION

PORTAL_ID = "99999999"  # deliberately unauthenticated


@pytest.fixture
def unauthenticated_server(monkeypatch, tmp_path):
    """Import the server with no usable portal, so the cold-start path is live."""
    monkeypatch.setenv("HUBSPOT_PORTAL", PORTAL_ID)
    from hubspot_mcp import config, persistence, server

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(persistence, "CONFIG_DIR", tmp_path)
    return server


async def test_speaks_2026_07_28(unauthenticated_server):
    async with Client(unauthenticated_server.mcp) as client:
        assert client.protocol_version == "2026-07-28"
        assert client.protocol_version == LATEST_PROTOCOL_VERSION


async def test_tools_list_carries_cache_hints(unauthenticated_server):
    """SEP-2549. cacheScope MUST be 'private'.

    The advertised tool list becomes portal-dependent once capability gating
    lands, and a shared intermediary must never serve one portal's tool list to
    another. Public here would be a cross-tenant leak.
    """
    async with Client(unauthenticated_server.mcp) as client:
        result = await client.list_tools()
        assert result.cache_scope == "private"
        assert result.ttl_ms > 0


async def test_tools_list_answers_when_unauthenticated(unauthenticated_server):
    """No handshake exists under 2026-07-28, so discovery must work cold.

    The lifespan yields ``client=None`` + ``auth_error`` rather than raising;
    raising would take down discovery for every client.
    """
    async with Client(unauthenticated_server.mcp) as client:
        result = await client.list_tools()
        assert len(result.tools) == 82
        assert "hubspot_get_object" in {t.name for t in result.tools}


async def test_failed_call_sets_is_error(unauthenticated_server):
    """A failing tool must not look like a success.

    Tool functions return ``{"error": ...}`` envelopes internally; the server
    re-raises them as ToolError so the protocol layer sets is_error.
    """
    async with Client(unauthenticated_server.mcp) as client:
        result = await client.call_tool(
            "hubspot_get_object", {"object_id": "1", "object_type": "contacts"}
        )
        assert result.is_error is True
        assert "auth login" in result.content[0].text


async def test_ctx_never_reaches_the_wire(unauthenticated_server):
    """Context injection is resolved from __annotations__, schemas from
    __signature__ — a wrapper that sets only the latter leaks ctx into all 76
    domain schemas and never receives a Context."""
    async with Client(unauthenticated_server.mcp) as client:
        result = await client.list_tools()
        leaked = [
            t.name for t in result.tools if "ctx" in (t.input_schema or {}).get("properties", {})
        ]
        assert leaked == []


async def test_tool_order_is_deterministic(unauthenticated_server):
    """Spec SHOULD: a stable tools/list order keeps LLM prompt caches warm."""
    async with Client(unauthenticated_server.mcp) as client:
        first = [t.name for t in (await client.list_tools()).tools]
        second = [t.name for t in (await client.list_tools()).tools]
    assert first == second
    assert first == sorted(first), "tools/list should be sorted by name"


async def test_serves_handshake_era_clients_too(unauthenticated_server):
    """Both protocol eras must work from one deployment.

    Clients in the wild span revisions, and the plugin ships to whatever Claude
    Code build the user has. `server/discover` advertises only 2026-07-28, but
    a legacy client's `initialize` still negotiates down -- so advertising the
    modern era must not lock older clients out.
    """
    async with Client(unauthenticated_server.mcp, mode="legacy") as client:
        assert client.protocol_version != "2026-07-28"
        result = await client.list_tools()
        assert len(result.tools) == 82
