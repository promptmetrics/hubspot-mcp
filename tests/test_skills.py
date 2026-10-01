"""HubSpot's Sales skills served from this server.

Pins: the vendored files load unchanged; the connector-to-server mapping names
only tools that exist; the served text is HubSpot's byte for byte after the
header; routing picks a skill from Jev's answers and from keywords; both
surfaces expose the skill tools.
"""
from __future__ import annotations

from importlib.resources import files

import pytest
from mcp import Client

from hubspot_mcp.config import PortalConfig
from hubspot_mcp.jev_router import JevUnavailable
from hubspot_mcp.skills import helper_names, keyword_rank, load_skills, skill_names
from hubspot_mcp.skills.mapping import CONNECTOR_TO_SERVER, server_tools_for, skill_header

NINE = ["call-prep", "contact-lookup", "daily-brief", "follow-up", "hubspot", "import-contacts", "log-call", "onboarding", "pipeline-pulse"]
PORTAL = "99999999"


def test_nine_skills_and_two_helpers_load():
    assert skill_names() == NINE
    assert helper_names() == ["account-research", "brief-fetcher"]
    for name, skill in load_skills().items():
        assert skill.description, name
        assert skill.tools, name
        assert skill.body.startswith("---\nname: " + name), name


def test_vendored_text_is_served_unchanged_after_the_header():
    raw = (files("hubspot_mcp.skills") / "daily-brief" / "SKILL.md").read_text(encoding="utf-8")
    skill = load_skills()["daily-brief"]
    assert skill.body == raw
    header = skill_header("daily-brief", skill.tools, routed=False, helper=False)
    assert header.endswith("\n\n")
    assert "search_crm_objects -> hubspot_search_objects" in header
    assert "manage_crm_objects -> " in header


async def test_every_mapped_tool_exists_on_the_full_surface():
    from hubspot_mcp import server

    names = {t.name for t in await server.mcp.list_tools()}
    for connector, targets in CONNECTOR_TO_SERVER.items():
        for t in targets:
            assert t in names, f"{connector} maps to unknown tool {t}"
    for skill in load_skills().values():
        assert server_tools_for(skill.tools), skill.name


def test_keyword_rank_picks_the_obvious_skills_and_nothing_for_weather():
    assert keyword_rank("Good morning, what's on my plate today?")[0].name == "daily-brief"
    assert keyword_rank("Prep me for my call with Jamie at 3pm")[0].name == "call-prep"
    assert keyword_rank("Draft a follow-up email about the renewal")[0].name == "follow-up"
    assert keyword_rank("What's the weather like in Berlin this evening?") == []


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __getattr__(self, name: str):
        if name not in ("get", "post", "patch", "put", "delete", "post_files"):
            raise AttributeError(name)

        async def _call(path: str, *a, **kw):
            self.calls.append((name.upper(), path))
            from hubspot_mcp.client import APIResponse

            return APIResponse(status_code=200, body={"id": "1", "properties": {}}, headers={})

        return _call

    async def close(self) -> None:
        pass


@pytest.fixture
def both_surfaces(tmp_path, monkeypatch):
    from hubspot_mcp import config, persistence, routed, server

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(persistence, "CONFIG_DIR", tmp_path)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(routed, "_TOOL_INDEX", None)
    lifespan = {
        "client": _FakeClient(),
        "cache": None,
        "portal_config": PortalConfig(portal_id=PORTAL, token="t", scopes_granted=[]),
        "portal_id": PORTAL,
        "auth_error": None,
    }
    monkeypatch.setattr(server, "_lifespan", lambda ctx: lifespan)
    monkeypatch.setattr(server, "_session_resolver", None)
    return server


def _text(result) -> str:
    return result.content[0].text


def _data(result):
    if result.structured_content is not None:
        return result.structured_content
    import json

    return json.loads(result.content[0].text)


async def test_full_surface_loads_a_skill_and_a_helper(both_surfaces):
    server = both_surfaces
    async with Client(server.mcp) as client:
        skill = await client.call_tool("hubspot_load_skill", {"name": "log-call"})
        helper = await client.call_tool("hubspot_load_skill", {"name": "brief-fetcher"})
        missing = await client.call_tool("hubspot_load_skill", {"name": "nope"})
    assert skill.is_error is False
    body = _text(skill)
    assert body.startswith("# Runtime for skill log-call")
    assert "call them by name" in body
    assert body.endswith(load_skills()["log-call"].body)
    assert helper.is_error is False and "# Runtime for helper brief-fetcher" in _text(helper)
    assert missing.is_error is True and "Unknown skill" in _text(missing)
    async with Client(server.mcp) as client:
        spaced = await client.call_tool("hubspot_load_skill", {"name": "Daily Brief"})
    assert spaced.is_error is False and _text(spaced).endswith(load_skills()["daily-brief"].body)


async def test_full_surface_find_skills_falls_back_to_keywords(both_surfaces, monkeypatch):
    server = both_surfaces
    from hubspot_mcp import jev_router

    async def _down(state, questions):
        raise JevUnavailable("no key")

    monkeypatch.setattr(jev_router, "jev_evaluate", _down)
    async with Client(server.mcp) as client:
        result = await client.call_tool("hubspot_find_skills", {"request_text": "Good morning, what's on my plate today?"})
    assert result.is_error is False, _text(result)
    data = _data(result)
    assert data["router"] == "keyword"
    assert data["primary_skill"] == "daily-brief"
    assert "hubspot_search_objects" in data["skills"][0]["tools_on_this_server"]
    assert "hubspot_load_skill" in data["next_step"]


async def test_routed_find_capabilities_prefers_a_skill_from_jev(both_surfaces, monkeypatch):
    server = both_surfaces
    from hubspot_mcp import jev_router

    seen: dict = {}

    async def _canned(state, questions):
        seen["questions"] = questions
        return {
            "answers": {
                "skill": {"type": "choice", "choice": "call-prep", "probabilities": {"call-prep": 0.88, "contact-lookup": 0.09, "none": 0.01}},
                "s_call-prep": {"type": "boolean", "probability": 0.9},
                "s_contact-lookup": {"type": "boolean", "probability": 0.55},
                "charter": {"type": "choice", "choice": "objects", "probabilities": {"objects": 0.7, "none": 0.1}},
                "c_objects": {"type": "boolean", "probability": 0.8},
                "t_hubspot_search_objects": {"type": "boolean", "probability": 0.9},
            },
            "usage": {},
            "providerMetadata": {"gateway": {"cost": "0.00034"}},
        }

    monkeypatch.setattr(jev_router, "jev_evaluate", _canned)
    async with Client(server.routed) as client:
        result = await client.call_tool("find_capabilities", {"task": "Prep me for my call with Jamie at 3pm"})
        loaded = await client.call_tool("load_skill", {"name": "call-prep"})
    assert result.is_error is False, _text(result)
    data = _data(result)
    assert seen["questions"]["skill"]["type"] == "choice"
    assert "none" in seen["questions"]["skill"]["criteria"]
    assert "s_daily-brief" in seen["questions"]
    assert data["primary_skill"] == "call-prep"
    assert [s["name"] for s in data["skills"]] == ["call-prep", "contact-lookup"]
    assert data["primary_charter"] == "objects"
    assert 'load_skill(name="call-prep")' in data["next_step"]
    by_name = {t["name"]: t for t in data["tools"]}
    assert "hubspot_get_object" in by_name, "the skill's tools are merged in"
    assert loaded.is_error is False
    assert "call_hubspot(tool=<name>" in _text(loaded)
    assert _text(loaded).endswith(load_skills()["call-prep"].body)


async def test_find_capabilities_returns_skill_tools_outside_the_chosen_charters(both_surfaces, monkeypatch):
    """A skill-only route must still return that skill's tools with schemas (review finding)."""
    server = both_surfaces
    from hubspot_mcp import jev_router

    async def _canned(state, questions):
        return {
            "answers": {
                "skill": {"type": "choice", "choice": "daily-brief", "probabilities": {"daily-brief": 0.95, "none": 0.02}},
                "s_daily-brief": {"type": "boolean", "probability": 0.95},
                "charter": {"type": "choice", "choice": "none", "probabilities": {"none": 0.9}},
            },
            "usage": {},
            "providerMetadata": {"gateway": {"cost": "0.0003"}},
        }

    monkeypatch.setattr(jev_router, "jev_evaluate", _canned)
    async with Client(server.routed) as client:
        result = await client.call_tool("find_capabilities", {"task": "Good morning, what's on my plate today?"})
    data = _data(result)
    assert data["primary_skill"] == "daily-brief" and data["primary_charter"] is None
    by_name = {t["name"]: t for t in data["tools"]}
    assert "hubspot_search_objects" in by_name and by_name["hubspot_search_objects"]["picked_by"] == "skill"
    assert by_name["hubspot_search_objects"]["input_schema"]["type"] == "object"
    assert "hubspot_approve_write" in by_name, "the skill writes, so the approval tools ride along"


async def test_call_hubspot_rejects_unknown_argument_keys(both_surfaces):
    server = both_surfaces
    async with Client(server.routed) as client:
        bad = await client.call_tool(
            "call_hubspot", {"tool": "hubspot_get_object", "args": {"object_type": "contacts", "object_id": "1", "bogus": 1}}
        )
    assert bad.is_error is True
    assert "unknown bogus" in _text(bad)


def test_skill_header_uses_the_canonical_name():
    from hubspot_mcp.routed import render_skill

    assert render_skill("Daily Brief", routed=False).startswith("# Runtime for skill daily-brief")
