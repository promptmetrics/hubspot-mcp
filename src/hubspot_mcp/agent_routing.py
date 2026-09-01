"""Deterministic keyword routing from a request to the specialist charters.

Extracted from hubspot-claude's ``orchestrator`` module, which this port does
not carry: the orchestrator also owns the durable loop, cron and schedule
machinery that MCP's own transport and the client's session replace. The
routing itself depends only on ``agents._AGENT_ROUTE_TERMS``, so it lives here
as a standalone module instead.

R1 requires >= 95% accuracy on ``tests/routing_corpus.yaml``, gated in CI.
"""
from __future__ import annotations

import re

_SEQUENTIAL_TRIGGERS = [" and ", " then ", " followed by "]

# Lazy cache of word-boundary regexes for route scoring. Populated on first
# ``route_request`` call via ``_compiled_route_patterns()``.
_ROUTE_PATTERNS: dict[str, list[re.Pattern[str]]] = {}


def _compiled_route_patterns() -> dict[str, list[re.Pattern[str]]]:
    """Lazily build and cache word-boundary regexes for every agent's route terms.

    Deferred to first call (not module load) to keep orchestrator import light
    and avoid any import-cycle with the ``hubspot_mcp.agents`` package.
    """
    if not _ROUTE_PATTERNS:
        from hubspot_mcp.agents import _AGENT_ROUTE_TERMS
        for agent, terms in _AGENT_ROUTE_TERMS.items():
            _ROUTE_PATTERNS[agent] = [
                re.compile(rf"\b{re.escape(term)}\b") for term in terms
            ]
    return _ROUTE_PATTERNS

def route_request(request_text: str, portal_id: str | None = None) -> list[str]:
    """Keyword-based routing with conjunction detection.

    Scores every agent in ``_AGENT_ROUTE_TERMS`` (all 44 specialists) using
    word-boundary regex, so ``"stage"`` no longer matches inside ``"stages"``
    and a bare ``"create"``/``"find"`` (kept out of every domain term list) cannot
    win a route on its own. Cross-object association requires an explicit
    association verb plus ≥2 object nouns — ``"at"``/``"for"`` were dropped from
    the association phrases so innocuous phrasing no longer forces associations.
    """
    text = request_text.lower()
    scores: dict[str, int] = {}
    for agent, patterns in _compiled_route_patterns().items():
        for pat in patterns:
            if pat.search(text):
                scores[agent] = scores.get(agent, 0) + 1

    # Portal custom object types: a cached custom type name (e.g. "pets") is a
    # record noun, so a request like "find all pets" routes to the objects agent
    # rather than falling through to []. Lazy + guarded so a missing/cold cache
    # or absent portal_id never breaks routing.
    if portal_id:
        try:
            from hubspot_mcp.cache import SchemaCache
            for ct in SchemaCache(portal_id).list_custom_object_names():
                if ct and re.search(rf"\b{re.escape(ct.lower())}\b", text):
                    scores["objects"] = scores.get("objects", 0) + 1
        except (OSError, ValueError, KeyError):
            # Cold/missing/malformed-cache graceful degradation: degrade to
            # term-only scoring rather than breaking routing. Narrowed on
            # purpose — OSError covers FileNotFoundError, ValueError covers
            # json.JSONDecodeError, KeyError covers absent schema keys. A
            # TypeError/AttributeError/ImportError here is a real bug in the
            # cache layer and must surface, not silently route to [].
            pass

    if not scores:
        return []

    best = sorted(scores, key=lambda k: scores[k], reverse=True)
    primary_score = scores[best[0]]

    # Cross-object association: ≥2 object nouns + an explicit association verb.
    # "record(s)" are included so "link records to companies" still fires.
    _OBJECT_TYPES = {"contact", "contacts", "company", "companies", "deal", "deals",
                     "ticket", "tickets", "record", "records"}
    _ASSOC_PHRASES = ["associated with", "linked to", "related to", "associate",
                      "associate with", "link", "link to", "relate", "relate to"]
    found_objs = {obj for obj in _OBJECT_TYPES if re.search(rf"\b{re.escape(obj)}\b", text)}
    # Word-boundary match the verbs too — bare substring ``in`` would let
    # "relate" match inside "correlate" and force associations for a non-association
    # analytics request. Multi-word phrases ("associated with") still match cleanly.
    has_assoc_phrase = any(
        re.search(rf"\b{re.escape(phrase)}\b", text) for phrase in _ASSOC_PHRASES
    )
    if len(found_objs) >= 2 and has_assoc_phrase:
        return sorted(["objects", "associations"])

    # Conjunction detection: if "and"/"then"/"followed by" links two high-scoring distinct domains
    has_conjunction = any(trigger in text for trigger in _SEQUENTIAL_TRIGGERS)
    if has_conjunction and len(best) >= 2:
        secondary_score = scores.get(best[1], 0)
        if secondary_score > 0 and primary_score < 2 * secondary_score:
            return sorted([best[0], best[1]])

    if len(best) > 1 and primary_score >= 2 * scores.get(best[1], 0):
        return [best[0]]
    if len(best) > 1 and scores.get(best[1], 0) > 0:
        return sorted([best[0], best[1]])
    return [best[0]]
