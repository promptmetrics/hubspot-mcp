"""Keyless search over HubSpot's official developer documentation.

``hubspot_docs_search`` ships with a pluggable backend and no implementation, so
it returned an empty result set for every query -- worse than absent, because
its own description tells the model to consult it before a write, and an empty
list reads as "the docs say nothing" rather than "search is not wired up".

The obvious fix -- delete the tool and let the client's own web search cover it
-- moves the dependency somewhere we control less: Claude Code's WebSearch is
US-only, it can be disabled by permissions or managed settings, and a sandboxed
client may have no egress at all. The ``RESEARCH_PROMPT_BLOCK`` in all 44
charters instructs the model to research before writing, so a client that
cannot search silently cannot follow its own charter.

HubSpot publishes an LLM-oriented catalogue, and it is two levels deep --
which matters, because the top level alone is misleading. ``/docs/llms.txt``
holds 305 entries of which 253 are OpenAPI spec JSONs; the prose guides sit
behind 17 sub-index pointers (``/docs/_llms/**.md``) that are themselves
recursive. Expanding them breadth-first reaches ~2,970 guide pages, each
available as clean Markdown, including the semantic pages the charters actually
need ("Workflow actions and enrollment types", "Enroll contact"). A backend
built only on the top level would return spec filenames and look like it worked.

No API key, no third-party vendor, no per-user credential, and it runs
server-side -- so it works regardless of the client's region, tool permissions
or sandbox.

Deliberately official-only: ``community.hubspot.com`` publishes no equivalent
index, and scraping a forum to label answers "community-unverified" would add a
fragile dependency for the lower-trust half of the corpus. A community query
returns a warning naming that, rather than pretending.

``llms.txt`` is a convention, not a contract. Every failure here degrades to a
raised exception, which the tool surfaces as a search warning -- never a crash,
and never a silent empty result.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from hubspot_mcp.tools.docs import OFFICIAL_DOMAIN, DocsResult

INDEX_URL = "https://developers.hubspot.com/docs/llms.txt"
_INDEX_TTL_SECONDS = 24 * 60 * 60
_INDEX_CACHE_NAME = "docs_index.json"
# Fetching a page per candidate is the expensive part; the tool asks for at most
# `max_results_per_source`, and we cap regardless so a broad query cannot fan
# out into dozens of requests.
_MAX_PAGE_FETCHES = 5
# Breadth-first expansion of the sub-indexes. 18 fetches reaches the whole
# catalogue today; the cap stops a future restructure from fetching forever.
_MAX_INDEX_FETCHES = 40
_SUB_INDEX_MARKER = "/_llms/"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
# Terms that match nearly every HubSpot doc carry no signal.
_STOP_WORDS = frozenset({
    "the", "a", "an", "of", "for", "to", "in", "on", "and", "or", "is", "are",
    "how", "what", "why", "when", "do", "does", "i", "my", "with", "hubspot",
    "api", "docs", "doc", "documentation",
})

_ENTRY_RE = re.compile(r"^- \[(?P<title>[^\]]+)\]\((?P<url>[^)]+)\)(?::\s*(?P<desc>.+))?$")
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+)$")


class DocsIndexUnavailable(RuntimeError):
    """The documentation index could not be fetched or parsed."""


@dataclass(frozen=True)
class DocEntry:
    title: str
    url: str
    description: str
    section: str

    def haystack(self) -> list[tuple[str, int]]:
        """Weighted fields to score a query against.

        Only 45 of 305 entries carry a description, so the title, the section
        breadcrumb and the URL slug do most of the work.
        """
        slug = self.url.rsplit("/", 1)[-1].removesuffix(".md").replace("-", " ")
        return [(self.title, 3), (self.description, 2), (self.section, 2), (slug, 1)]


def _terms(text: str) -> set[str]:
    """Content terms, crudely singularised.

    HubSpot's titles say "contacts" where an operator asks about a "contact";
    without this, "batch update contact properties" ranks marketing campaigns
    above the contacts endpoint. A real stemmer would be a dependency for very
    little more accuracy at this corpus size.
    """
    out: set[str] = set()
    for token in re.findall(r"[a-z0-9_]+", text.lower()):
        if token in _STOP_WORDS:
            continue
        out.add(token)
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            out.add(token[:-1])
    return out


def parse_index(text: str) -> list[DocEntry]:
    """Parse ``llms.txt`` into entries, carrying the heading breadcrumb down."""
    entries: list[DocEntry] = []
    breadcrumb: list[str] = []
    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            depth = len(heading.group("hashes"))
            del breadcrumb[depth - 1 :]
            breadcrumb.append(heading.group("text").strip())
            continue
        entry = _ENTRY_RE.match(line.strip())
        if entry:
            entries.append(
                DocEntry(
                    title=entry.group("title").strip(),
                    url=entry.group("url").strip(),
                    description=(entry.group("desc") or "").strip(),
                    # Skip the document title itself ("# HubSpot docs").
                    section=" > ".join(breadcrumb[1:]),
                )
            )
    return entries


def _cache_path() -> Path:
    # The docs index is global, not per-portal, so it sits at the config root
    # rather than under a portal directory.
    from hubspot_mcp.config import CONFIG_DIR

    return CONFIG_DIR / _INDEX_CACHE_NAME


def _read_cached_index() -> list[DocEntry] | None:
    path = _cache_path()
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if time.time() - payload.get("_fetched_at", 0) > _INDEX_TTL_SECONDS:
        return None
    try:
        return [DocEntry(**e) for e in payload["entries"]]
    except (KeyError, TypeError):
        return None


def _write_cached_index(entries: list[DocEntry]) -> None:
    from hubspot_mcp.fileio import write_private_json

    payload: dict[str, Any] = {
        "_fetched_at": time.time(),
        "entries": [e.__dict__ for e in entries],
    }
    try:
        write_private_json(_cache_path(), payload)
    except OSError:
        # A read-only or full disk must not fail the search; we just refetch.
        pass


async def _expand_catalogue(client: httpx.AsyncClient) -> list[DocEntry]:
    """Breadth-first walk from llms.txt through the ``_llms`` sub-indexes.

    Each level is fetched concurrently: serially this takes ~28s, which would be
    paid by whichever query happens to warm a cold cache.
    """
    import asyncio

    seen: set[str] = set()
    catalogue: dict[str, DocEntry] = {}
    frontier = [INDEX_URL]
    fetches = 0

    while frontier and fetches < _MAX_INDEX_FETCHES:
        batch = [u for u in frontier if u not in seen][: _MAX_INDEX_FETCHES - fetches]
        seen.update(batch)
        fetches += len(batch)
        responses = await asyncio.gather(
            *(client.get(u) for u in batch), return_exceptions=True
        )
        frontier = []
        for response in responses:
            if isinstance(response, BaseException) or response.status_code != 200:
                # One unreachable sub-index costs its subtree, not the search.
                continue
            for entry in parse_index(response.text):
                if _SUB_INDEX_MARKER in entry.url:
                    if entry.url not in seen:
                        frontier.append(entry.url)
                elif entry.url.endswith(".md"):
                    # Later levels are more specific; keep the first (shallowest)
                    # entry so section breadcrumbs stay meaningful.
                    catalogue.setdefault(entry.url, entry)
    return list(catalogue.values())


async def load_index(client: httpx.AsyncClient | None = None) -> list[DocEntry]:
    """Return the parsed catalogue, from cache when fresh."""
    cached = _read_cached_index()
    if cached:
        return cached

    http = client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
    try:
        entries = await _expand_catalogue(http)
    except httpx.HTTPError as exc:
        raise DocsIndexUnavailable(f"could not build the docs index: {exc}") from exc
    finally:
        if client is None:
            await http.aclose()

    if not entries:
        # The format changed under us; better to say so than return nothing.
        raise DocsIndexUnavailable(
            f"{INDEX_URL} expanded to zero pages — the docs index format may have changed"
        )
    _write_cached_index(entries)
    return entries


def rank(query: str, entries: list[DocEntry], limit: int) -> list[tuple[DocEntry, float]]:
    """Score entries by weighted term overlap. Deterministic and explainable."""
    query_terms = _terms(query)
    if not query_terms:
        return []
    scored: list[tuple[DocEntry, float]] = []
    for entry in entries:
        hits = 0
        for text, weight in entry.haystack():
            if not text:
                continue
            hits += weight * len(query_terms & _terms(text))
        if hits:
            # Normalise so a long description cannot outrank a precise title.
            scored.append((entry, hits / len(query_terms)))
    # Sort by score, then title, so equal scores order deterministically.
    scored.sort(key=lambda pair: (-pair[1], pair[0].title))
    return scored[:limit]


def _snippet_for(page_text: str, query: str) -> str:
    """The most query-relevant paragraph, else the opening prose."""
    body = page_text.split("---", 2)[-1] if page_text.lstrip().startswith(("---", ">")) else page_text
    paragraphs = [p.strip() for p in body.split("\n\n") if len(p.strip()) > 40]
    if not paragraphs:
        return body.strip()[:400]
    query_terms = _terms(query)
    best = max(paragraphs, key=lambda p: len(query_terms & _terms(p)))
    return " ".join(best.split())


async def search_official_docs(
    query: str, domain: str, max_results: int
) -> list[DocsResult]:
    """``SearchBackend`` implementation for HubSpot's official docs.

    Signature is the tool's ``SearchBackend`` contract:
    ``(query, domain, max_results) -> list[DocsResult]``.
    """
    if domain != OFFICIAL_DOMAIN:
        raise DocsIndexUnavailable(
            f"no keyless index exists for {domain}; only {OFFICIAL_DOMAIN} is searchable"
        )

    entries = await load_index()
    ranked = rank(query, entries, min(max_results, _MAX_PAGE_FETCHES))
    if not ranked:
        return []

    results: list[DocsResult] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for entry, score in ranked:
            snippet = entry.description
            warnings: list[str] = []
            try:
                page = await client.get(entry.url)
                page.raise_for_status()
                snippet = _snippet_for(page.text, query) or entry.description
            except httpx.HTTPError as exc:
                # Fall back to the index description rather than dropping a hit.
                warnings.append(f"page fetch failed ({exc.__class__.__name__}); showing index summary")
            results.append(
                DocsResult(
                    source="official",
                    # Everything here is from developers.hubspot.com, so the
                    # tier is not a guess.
                    trust_tier="official",
                    title=entry.title,
                    url=entry.url,
                    snippet=snippet,
                    score=round(score, 3),
                    warnings=warnings,
                )
            )
    return results
