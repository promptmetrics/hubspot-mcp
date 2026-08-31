"""Research workflow helpers for sub-agents.

Sub-agents perform research using Claude Code's native WebSearch tool with
site-restricted queries against HubSpot's official docs and community forum.
This module provides the prompt template injected into every sub-agent (via
the base agent builder) and a URL classifier the orchestrator uses to
validate or normalize what agents report in informing_sources.
"""

from __future__ import annotations

from typing import Literal

Source = Literal["official", "community"]
TrustTier = Literal["official", "community-accepted", "community-unverified"]

OFFICIAL_DOMAINS: tuple[str, ...] = (
    "developers.hubspot.com",
    "knowledge.hubspot.com",
)
COMMUNITY_DOMAIN = "community.hubspot.com"


RESEARCH_PROMPT_BLOCK = """\
## Research guidance

Before proposing any write, if you are uncertain about HubSpot semantics,
property type validity, list/workflow behavior, or tier-dependent feature
availability, research first using the WebSearch tool with site-restricted
queries:

  - Official docs:   site:developers.hubspot.com <your query>
  - Knowledge base:  site:knowledge.hubspot.com <your query>
  - Community:       site:community.hubspot.com <your query>

After an unexpected API error, search the error category or message text the
same way to interpret it.

Treat community results as hypotheses to verify against the API, not as
authoritative facts. If a community post is more than ~2 years old, marked
unanswered, or contradicts the official docs, weight it accordingly.

When research informs a write, populate informing_sources on the
PreviewResult you return. Each entry must be:

  {
    "source": "official" | "community",
    "trust_tier": "official" | "community-accepted" | "community-unverified",
    "title": "<page title>",
    "url": "<url>",
    "last_updated": "<ISO date or null>"
  }

Trust-tier rules:
  - "official"               — URL on developers.hubspot.com or knowledge.hubspot.com
  - "community-accepted"     — community.hubspot.com post marked as the accepted
                               answer OR authored by a HubSpot employee badge
  - "community-unverified"   — any other community.hubspot.com post

If you fetch full page contents with WebFetch, only retain the parts directly
relevant to the proposed write. Do not pad informing_sources with results
you did not actually use.
"""


def classify_url(url: str) -> tuple[Source, TrustTier]:
    """Best-effort source/trust classification from a URL alone.

    The orchestrator uses this to validate or default what a sub-agent reports
    in informing_sources. Sub-agents are expected to assign trust_tier
    directly using richer context (accepted-answer flag, employee badge, post
    age); this function exists so the orchestrator can catch obvious mistakes
    (e.g., a random non-HubSpot URL labeled as "official").
    """
    lower = url.lower()
    if any(domain in lower for domain in OFFICIAL_DOMAINS):
        return ("official", "official")
    if COMMUNITY_DOMAIN in lower:
        return ("community", "community-unverified")
    # Anything else: treat as community-unverified rather than fabricating an
    # "official" tier we cannot justify from the URL.
    return ("community", "community-unverified")
