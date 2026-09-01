"""T5: routing corpus accuracy gate + `hubspot route` subcommand shape.

Loads tests/routing_corpus.yaml and asserts `route_request` output for every
`mode: fast_path` entry.  expected_routes are the CORRECT agent(s) for the
request — this is an accuracy assert, not a "lock current output" baseline, so
a routing change that misroutes an entry must update the label (or fix the
router) rather than silently pass.  `mode: llm` entries are aspirational targets
for the stub LLM router and are skipped (LLM router not live).  Also verifies
the `hubspot route '<request>'` CLI subcommand emits the frozen JSON shape
``{"agents": [...], "rationale": "..."}`` and that every routable agent has at
least one fast_path corpus entry (coverage check — a newly added agent with no
corpus entry fails CI).

Run via: pytest -k routing_corpus
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hubspot_mcp.agent_routing import route_request

CORPUS_PATH = Path(__file__).parent / "routing_corpus.yaml"


def _load_corpus() -> list[dict]:
    with CORPUS_PATH.open() as fh:
        entries = yaml.safe_load(fh)
    assert entries is not None, "routing_corpus.yaml is empty"
    return entries


def _fast_path_entries() -> list[dict]:
    return [e for e in _load_corpus() if e.get("mode") == "fast_path"]


def _llm_entries() -> list[dict]:
    return [e for e in _load_corpus() if e.get("mode") == "llm"]


@pytest.mark.parametrize(
    "entry",
    _fast_path_entries(),
    ids=lambda e: f"{e['mode']}:{e['request'][:30]!r}",
)
def test_fast_path_corpus_matches_route_request(entry):
    """Every fast_path entry's expected_routes must equal live route_request output."""
    assert route_request(entry["request"]) == entry["expected_routes"]


# When all llm entries have been promoted to fast_path, _llm_entries() is empty.
# pytest 8.1 still probes the ids callable with a NOT_SET sentinel at collection,
# so the isinstance guard keeps collection clean; the test then skips normally.
@pytest.mark.parametrize(
    "entry",
    _llm_entries(),
    ids=lambda e: f"llm:{e['request'][:30]!r}" if isinstance(e, dict) else "llm:none",
)
def test_llm_corpus_entries_skipped(entry):
    """LLM-router entries are not gated against the keyword router (stub not live)."""
    pytest.skip("LLM router is a stub; llm corpus entries are not gated")


def test_corpus_has_fast_path_and_edge_cases():
    entries = _load_corpus()
    modes = {e.get("mode") for e in entries}
    assert "fast_path" in modes
    by_req = {e["request"]: e for e in entries}
    assert by_req[""]["expected_routes"] == []
    assert by_req["hello"]["expected_routes"] == []


def test_fast_path_corpus_covers_all_routable_agents():
    """Every routable agent (all registry keys except triage/verify, which are
    orchestration-internal and intentionally unroutable) must appear in at least
    one fast_path entry's expected_routes — so a newly added agent with no
    corpus entry fails CI instead of silently going unreachable on the fast path.
    """
    from hubspot_mcp.agents import _AGENT_REGISTRY

    routable = {k for k in _AGENT_REGISTRY if k not in ("triage", "verify")}
    covered: set[str] = set()
    for entry in _fast_path_entries():
        covered.update(entry["expected_routes"])
    missing = routable - covered
    assert not missing, (
        f"Routable agents with no fast_path corpus entry: {sorted(missing)}. "
        "Add a corpus entry whose expected_routes includes each missing agent."
    )


def test_route_terms_keys_match_registry():
    """_AGENT_ROUTE_TERMS keys must exactly equal _AGENT_REGISTRY keys.

    Catches reverse drift: an agent removed from _AGENT_REGISTRY but left in
    _AGENT_ROUTE_TERMS would let route_request return an invalid agent name
    with CI green (the corpus-match and coverage checks both iterate the
    registry, not the route-terms dict). An agent added to the registry but
    missing from _AGENT_ROUTE_TERMS would silently be unroutable on the fast
    path. Both directions must fail CI.
    """
    from hubspot_mcp.agents import _AGENT_REGISTRY, _AGENT_ROUTE_TERMS

    only_terms = set(_AGENT_ROUTE_TERMS) - set(_AGENT_REGISTRY)
    only_registry = set(_AGENT_REGISTRY) - set(_AGENT_ROUTE_TERMS)
    assert not only_terms, (
        f"_AGENT_ROUTE_TERMS has keys not in _AGENT_REGISTRY (stale -> invalid route output): {sorted(only_terms)}"
    )
    assert not only_registry, (
        f"_AGENT_REGISTRY has keys missing from _AGENT_ROUTE_TERMS (silently unroutable): {sorted(only_registry)}"
    )


# --------------------------------------------------------------------------- #
# The routing decision is exposed as the `hubspot_route` MCP tool rather than
# upstream's `hubspot route` CLI subcommand, but the frozen
# {agents, rationale} contract is preserved so this corpus gates both.
# --------------------------------------------------------------------------- #


@pytest.fixture
def routed(tmp_path, monkeypatch):
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

    async def _call(request_text: str):
        return await server.hubspot_route(None, request_text=request_text)

    return _call


async def test_route_tool_emits_the_frozen_shape(routed):
    payload = await routed("list workflows")
    assert set(payload) >= {"agents", "rationale"}
    assert payload["agents"] == route_request("list workflows", portal_id="99999999")
    assert "sorted candidates" in payload["rationale"] or "selected agent" in payload["rationale"]


async def test_route_tool_empty_request(routed):
    payload = await routed("")
    assert payload["agents"] == []
    assert "empty request" in payload["rationale"]


async def test_route_tool_no_match(routed):
    payload = await routed("hello")
    assert payload["agents"] == []
    assert "no keyword match" in payload["rationale"]


async def test_route_tool_returns_charters_for_each_agent(routed):
    """The MCP-specific half: each routed agent names its prompt and tools, so a
    client can fetch the charter via prompts/get and know its tool budget."""
    payload = await routed("find duplicate contacts")
    assert payload["agents"], "expected a route for a clearly-hygiene request"
    assert [c["agent"] for c in payload["charters"]] == payload["agents"]
    for charter in payload["charters"]:
        assert charter["prompt"] == f"hubspot_{charter['agent']}"
        assert charter["tools"], f"{charter['agent']} charter advertises no tools"
