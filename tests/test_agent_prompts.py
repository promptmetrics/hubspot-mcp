"""The 44 specialist charters exposed as MCP prompts.

Resolves Task 10 of the Phase 1 build plan. hubspot-claude's "agents" are prompt
BUILDERS -- each returns an AgentPrompt(agent_name, system_prompt, tool_names,
domain_description) assembled from shared blocks plus a per-domain tool list --
so they map onto MCP prompts directly, with no conversion to Claude Code
sub-agent markdown and no orchestration ported. That also makes them usable by
any MCP client, not just Claude Code.
"""
from __future__ import annotations

import pytest
from mcp import Client

from hubspot_mcp.agents import _AGENT_REGISTRY


@pytest.fixture
def server_ready(tmp_path, monkeypatch):
    from hubspot_mcp import config, persistence, server
    from hubspot_mcp.config import PortalConfig

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(persistence, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        server,
        "_lifespan",
        lambda ctx: {
            "client": None, "cache": None,
            "portal_config": PortalConfig(portal_id="99999999", token="t", scopes_granted=[]),
            "portal_id": "99999999", "auth_error": None, "capabilities": None,
        },
    )
    return server


async def test_every_agent_is_exposed_as_a_prompt(server_ready):
    async with Client(server_ready.mcp) as client:
        listed = await client.list_prompts()
    names = {p.name for p in listed.prompts}
    assert names == {f"hubspot_{key}" for key in _AGENT_REGISTRY}
    assert len(names) == 44


async def test_prompts_list_is_private_and_deterministic(server_ready):
    """Charters name the portal's custom object types, so the listing is
    portal-specific and must never be cached across authorization contexts."""
    async with Client(server_ready.mcp) as client:
        first = await client.list_prompts()
        second = await client.list_prompts()
    assert first.cache_scope == "private"
    assert first.ttl_ms > 0
    order = [p.name for p in first.prompts]
    assert order == [p.name for p in second.prompts]
    assert order == sorted(order)


async def test_charter_carries_its_tools_and_operating_rules(server_ready):
    async with Client(server_ready.mcp) as client:
        got = await client.get_prompt("hubspot_objects", {})
    text = got.messages[0].content.text
    assert "Objects Agent" in text
    # The per-domain tool list, and the two blocks that make a charter useful
    # rather than decorative.
    assert "hubspot_update_object" in text
    assert "Self-correction" in text
    assert "verify" in text.lower()


async def test_write_capable_charter_mandates_verification(server_ready):
    """REFLECTION_PROMPT_BLOCK is only added for agents that can write; losing
    it would drop the re-fetch-and-compare rule after every write."""
    async with Client(server_ready.mcp) as client:
        objects = (await client.get_prompt("hubspot_objects", {})).messages[0].content.text
    assert "Write verification" in objects or "verify the write" in objects.lower()


async def test_prompts_do_not_expose_ctx_as_an_argument(server_ready):
    async with Client(server_ready.mcp) as client:
        listed = await client.list_prompts()
    for prompt in listed.prompts:
        assert "ctx" not in {a.name for a in (prompt.arguments or [])}
