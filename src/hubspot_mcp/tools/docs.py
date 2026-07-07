from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from hubspot_mcp.tools import tool

Source = Literal["official", "community"]
TrustTier = Literal["official", "community-accepted", "community-unverified"]

OFFICIAL_DOMAIN = "developers.hubspot.com"
COMMUNITY_DOMAIN = "community.hubspot.com"
_MAX_SNIPPET_CHARS = 300


@dataclass
class DocsResult:
    source: Source
    trust_tier: TrustTier
    title: str
    url: str
    snippet: str
    last_updated: str | None = None
    score: float = 0.0
    warnings: list[str] = field(default_factory=list)


SearchBackend = Callable[[str, str, int], Awaitable[list[DocsResult]]]

_default_backend: SearchBackend | None = None


def set_search_backend(backend: SearchBackend | None) -> None:
    """Register the production search backend (call at startup)."""
    global _default_backend
    _default_backend = backend


def _truncate_snippet(text: str) -> str:
    if len(text) <= _MAX_SNIPPET_CHARS:
        return text
    return text[: _MAX_SNIPPET_CHARS - 1].rstrip() + "…"


def _build_query(query: str, domain_hint: str | None, api_version: str | None) -> str:
    parts = [query]
    if domain_hint:
        parts.append(domain_hint)
    if api_version:
        parts.append(api_version)
    return " ".join(parts)


@tool(
    name="hubspot_docs_search",
    description=(
        "Search HubSpot's official documentation and/or the HubSpot community "
        "for guidance on API semantics, error meanings, list/workflow behavior, "
        "or tier-dependent feature availability. Call BEFORE proposing a write "
        "when uncertain, or AFTER an unexpected error to interpret it. Results "
        "are labeled by source and trust tier; community results are hypotheses "
        "to verify, not authoritative facts."
    ),
)
async def hubspot_docs_search(
    query: str,
    sources: list[Source] | None = None,
    max_results_per_source: int = 5,
    domain_hint: str | None = None,
    api_version: str | None = None,
    search_backend: SearchBackend | None = None,
) -> dict[str, Any]:
    selected_sources: list[Source] = sources or ["official", "community"]
    backend = search_backend or _default_backend
    warnings: list[str] = []

    if backend is None:
        return {
            "query": query,
            "results": [],
            "search_warnings": ["no search backend configured"],
        }

    full_query = _build_query(query, domain_hint, api_version)
    results: list[DocsResult] = []

    for source in selected_sources:
        domain = OFFICIAL_DOMAIN if source == "official" else COMMUNITY_DOMAIN
        try:
            source_results = await backend(full_query, domain, max_results_per_source)
        except Exception as exc:
            warnings.append(f"{source} search failed: {exc}")
            continue
        for r in source_results:
            r.snippet = _truncate_snippet(r.snippet)
        results.extend(source_results)

    if not results and not warnings:
        warnings.append("no results from any source")

    results.sort(key=lambda r: r.score, reverse=True)
    return {
        "query": query,
        "results": [
            {
                "source": r.source,
                "trust_tier": r.trust_tier,
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "last_updated": r.last_updated,
                "score": r.score,
            }
            for r in results
        ],
        "search_warnings": warnings,
    }
