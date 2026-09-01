"""Keyless search over HubSpot's official developer docs.

Every test mocks HTTP: CI must not depend on developers.hubspot.com being
reachable, and a docs-search test that silently starts hitting the network is a
flaky test waiting to happen.
"""
from __future__ import annotations

import json
import time

import httpx
import pytest
import respx

from hubspot_mcp.docs_backend import (
    INDEX_URL,
    DocsIndexUnavailable,
    load_index,
    parse_index,
    rank,
    search_official_docs,
)

# Mirrors the real shape: a top level that is mostly spec JSON plus sub-index
# pointers, with the prose guides only reachable one level down.
TOP_INDEX = """# HubSpot docs

## OpenAPI Specs

- [automation-automation-v4-v2026-03-flows](/docs/specs/2026-03/automation-flows.json)

## Indexes

- [APIs / CRM (616 pages)](https://developers.hubspot.com/docs/_llms/apis/crm.md): Documentation for CRM.
"""

SUB_INDEX = """# CRM

## Automation

- [Workflow actions and enrollment types](https://developers.hubspot.com/docs/api/automation/enrollment.md): How enrollment triggers work.
- [Create a workflow](https://developers.hubspot.com/docs/api/automation/create.md)

## Objects

- [Update a batch of contacts](https://developers.hubspot.com/docs/api/crm/contacts/batch-update.md): Update many contacts at once.
"""

PAGE = """---
id: abc
---

# Workflow actions and enrollment types

Enrollment triggers decide which records enter a workflow. A record is enrolled
when it first meets the trigger criteria, and re-enrollment must be enabled
explicitly per trigger.

Unrelated trailing paragraph about something else entirely for padding purposes.
"""


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    from hubspot_mcp import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return tmp_path


def _mock_catalogue(mock: respx.MockRouter) -> None:
    mock.get(INDEX_URL).mock(return_value=httpx.Response(200, text=TOP_INDEX))
    mock.get("https://developers.hubspot.com/docs/_llms/apis/crm.md").mock(
        return_value=httpx.Response(200, text=SUB_INDEX)
    )


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def test_parse_carries_the_heading_breadcrumb():
    entries = {e.title: e for e in parse_index(SUB_INDEX)}
    assert entries["Workflow actions and enrollment types"].section == "Automation"
    assert entries["Update a batch of contacts"].section == "Objects"


def test_parse_handles_entries_with_no_description():
    """Only ~45 of 305 top-level entries carry one; the rest must still parse."""
    entry = next(e for e in parse_index(SUB_INDEX) if e.title == "Create a workflow")
    assert entry.description == ""
    assert entry.url.endswith("create.md")


# --------------------------------------------------------------------------- #
# Index expansion -- the part a top-level-only implementation gets wrong
# --------------------------------------------------------------------------- #


@pytest.mark.live_docs_backend
@respx.mock
async def test_expansion_follows_sub_indexes_to_the_real_guides(isolated_cache):
    _mock_catalogue(respx.mock)
    titles = {e.title for e in await load_index()}

    assert "Workflow actions and enrollment types" in titles, (
        "guides live one level below llms.txt; a top-level-only index returns "
        "spec filenames and looks like it works"
    )
    # The OpenAPI spec JSON is not a guide page and must not enter the catalogue.
    assert not any(t.startswith("automation-automation-v4") for t in titles)


@pytest.mark.live_docs_backend
@respx.mock
async def test_index_is_cached_and_not_refetched(isolated_cache):
    _mock_catalogue(respx.mock)
    await load_index()
    calls_after_first = respx.mock.calls.call_count
    await load_index()
    assert respx.mock.calls.call_count == calls_after_first, "warm cache still hit the network"


@pytest.mark.live_docs_backend
@respx.mock
async def test_stale_cache_is_rebuilt(isolated_cache):
    _mock_catalogue(respx.mock)
    await load_index()
    cache = isolated_cache / "docs_index.json"
    payload = json.loads(cache.read_text())
    # Expiry is the cache store's business now, not a field inside the value.
    payload["_expires_at"] = time.time() - 1
    cache.write_text(json.dumps(payload))

    before = respx.mock.calls.call_count
    await load_index()
    assert respx.mock.calls.call_count > before


@pytest.mark.live_docs_backend
@respx.mock
async def test_one_unreachable_sub_index_does_not_fail_the_build(isolated_cache):
    """A dead subtree costs its own pages, not the whole search."""
    respx.mock.get(INDEX_URL).mock(return_value=httpx.Response(200, text=TOP_INDEX))
    respx.mock.get("https://developers.hubspot.com/docs/_llms/apis/crm.md").mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(DocsIndexUnavailable, match="zero pages"):
        await load_index()


@pytest.mark.live_docs_backend
@respx.mock
async def test_unreachable_index_raises_rather_than_returning_empty(isolated_cache):
    """The original bug: an empty result reads as 'the docs say nothing'."""
    respx.mock.get(INDEX_URL).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(DocsIndexUnavailable):
        await load_index()


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #


def test_ranking_prefers_the_semantic_guide():
    entries = parse_index(SUB_INDEX)
    top = rank("workflow enrollment", entries, 3)
    assert top, "expected a match"
    assert top[0][0].title == "Workflow actions and enrollment types"


def test_ranking_tolerates_singular_plural():
    """'contact' must reach titles that say 'contacts'."""
    entries = parse_index(SUB_INDEX)
    top = rank("batch update contact", entries, 1)
    assert top[0][0].title == "Update a batch of contacts"


def test_ranking_is_deterministic_for_equal_scores():
    entries = parse_index(SUB_INDEX)
    assert [e.title for e, _ in rank("workflow", entries, 5)] == [
        e.title for e, _ in rank("workflow", entries, 5)
    ]


def test_a_query_of_only_stop_words_matches_nothing():
    assert rank("how do i", parse_index(SUB_INDEX), 5) == []


# --------------------------------------------------------------------------- #
# The SearchBackend contract
# --------------------------------------------------------------------------- #


@pytest.mark.live_docs_backend
@respx.mock
async def test_backend_returns_official_tier_results_with_snippets(isolated_cache):
    _mock_catalogue(respx.mock)
    respx.mock.get("https://developers.hubspot.com/docs/api/automation/enrollment.md").mock(
        return_value=httpx.Response(200, text=PAGE)
    )
    respx.mock.get(url__regex=r".*\.md$").mock(return_value=httpx.Response(200, text=PAGE))

    results = await search_official_docs("workflow enrollment", "developers.hubspot.com", 2)

    assert results
    top = results[0]
    assert top.source == "official"
    # Everything here comes from developers.hubspot.com, so the tier is a fact,
    # not an inference.
    assert top.trust_tier == "official"
    assert "enrollment" in top.snippet.lower()
    assert top.url.startswith("https://developers.hubspot.com/")


@pytest.mark.live_docs_backend
@respx.mock
async def test_page_fetch_failure_degrades_to_the_index_description(isolated_cache):
    _mock_catalogue(respx.mock)
    respx.mock.get(url__regex=r".*/api/.*\.md$").mock(return_value=httpx.Response(404))

    results = await search_official_docs("workflow enrollment", "developers.hubspot.com", 1)

    assert results, "a dead page should not drop the hit"
    assert "How enrollment triggers work." in results[0].snippet
    assert results[0].warnings


@pytest.mark.live_docs_backend
async def test_community_domain_is_refused_with_a_reason(isolated_cache):
    """No keyless community index exists; say so rather than pretend."""
    with pytest.raises(DocsIndexUnavailable, match="community.hubspot.com"):
        await search_official_docs("anything", "community.hubspot.com", 3)


@pytest.mark.live_docs_backend
@respx.mock
async def test_tool_surfaces_a_backend_failure_as_a_warning_not_silence(isolated_cache):
    """End to end: the tool must never again return an empty success."""
    from hubspot_mcp.tools.docs import hubspot_docs_search

    respx.mock.get(INDEX_URL).mock(side_effect=httpx.ConnectError("no route"))
    out = await hubspot_docs_search("workflow enrollment", sources=["official"])

    assert out["results"] == []
    assert out["search_warnings"], "an empty result with no warning reads as 'the docs say nothing'"
    assert "official search failed" in out["search_warnings"][0]


# --------------------------------------------------------------------------- #
# Dispatch path -- what the unit tests above cannot see
# --------------------------------------------------------------------------- #


async def test_reachable_through_invoke_tool(monkeypatch):
    """Every call through the MCP path raised TypeError before this.

    ``invoke_tool`` passes ``client`` and ``portal_id`` to every tool, and
    ``hubspot_docs_search`` was the only one of 79 that accepted neither. The
    other docs tests call the function directly with explicit kwargs, so a
    green suite told us nothing about whether the tool could actually be
    dispatched.
    """
    from hubspot_mcp.tools import invoke_tool

    async def stub(query, domain, limit):
        return []

    monkeypatch.setattr("hubspot_mcp.tools.docs._builtin_backend", lambda: stub)
    result = await invoke_tool(
        "hubspot_docs_search", "99999999", client=object(), query="workflow enrollment"
    )
    assert result["results"] == []


async def test_client_and_portal_id_stay_out_of_the_json_schema():
    """They exist only to satisfy the dispatcher, not for a model to supply."""
    from hubspot_mcp import server

    tools = {t.name: t for t in await server.mcp.list_tools()}
    props = set((tools["hubspot_docs_search"].input_schema or {}).get("properties", {}))
    assert "client" not in props
    assert "portal_id" not in props
    assert "query" in props
