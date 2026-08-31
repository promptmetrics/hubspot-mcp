import pytest

from hubspot_mcp.tools.docs import (
    DocsResult,
    SearchBackend,
    _build_query,
    _truncate_snippet,
    hubspot_docs_search,
    set_search_backend,
)


def test_truncate_snippet_short():
    assert _truncate_snippet("short") == "short"


def test_truncate_snippet_long():
    text = "a" * 400
    result = _truncate_snippet(text)
    assert len(result) <= 300
    assert result.endswith("…")


def test_build_query_basic():
    assert _build_query("contacts", None, None) == "contacts"


def test_build_query_with_hints():
    assert _build_query("contacts", "crm", "v3") == "contacts crm v3"


@pytest.mark.asyncio
async def test_no_backend_returns_empty_with_warning():
    set_search_backend(None)
    result = await hubspot_docs_search(query="contacts")
    assert result["results"] == []
    assert "no search backend configured" in result["search_warnings"]


@pytest.mark.asyncio
async def test_backend_failure_graceful():
    async def failing_backend(query, domain, limit):
        raise RuntimeError("provider down")

    result = await hubspot_docs_search(
        query="q", sources=["official"], search_backend=failing_backend,
    )
    assert result["results"] == []
    assert any("official search failed" in w for w in result["search_warnings"])


@pytest.mark.asyncio
async def test_single_source_filter():
    async def fake_backend(query, domain, limit):
        if "developers" in domain:
            return [DocsResult(
                source="official", trust_tier="official",
                title="t", url="u", snippet="s", score=0.9,
            )]
        return [DocsResult(
            source="community", trust_tier="community-unverified",
            title="t", url="u", snippet="s", score=0.4,
        )]

    result = await hubspot_docs_search(
        query="q", sources=["official"], search_backend=fake_backend,
    )
    sources = {r["source"] for r in result["results"]}
    assert sources == {"official"}


@pytest.mark.asyncio
async def test_results_sorted_by_score():
    async def fake_backend(query, domain, limit):
        return [
            DocsResult(source="official", trust_tier="official", title="low", url="u1", snippet="s", score=0.3),
            DocsResult(source="official", trust_tier="official", title="high", url="u2", snippet="s", score=0.9),
        ]

    result = await hubspot_docs_search(query="q", sources=["official"], search_backend=fake_backend)
    titles = [r["title"] for r in result["results"]]
    assert titles == ["high", "low"]


@pytest.mark.asyncio
async def test_snippet_truncation_applied():
    async def fake_backend(query, domain, limit):
        return [
            DocsResult(source="official", trust_tier="official", title="t", url="u", snippet="a" * 500, score=0.5),
        ]

    result = await hubspot_docs_search(query="q", search_backend=fake_backend)
    assert len(result["results"][0]["snippet"]) <= 300
