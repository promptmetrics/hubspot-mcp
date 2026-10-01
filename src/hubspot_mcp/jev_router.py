"""Per-request capability routing for the routed MCP surface.

The routed surface (``/mcp/routed``) advertises three tools instead of 86. The
model describes what it wants in ``find_capabilities(task)`` and this module
decides which specialist charters and which HubSpot tools that needs.

The decision model is TypeSafe's **Jev** on Vercel's AI Gateway: an
evaluation-only model that answers a batch of questions about one piece of
text with calibrated probabilities. One request carries every question, so a
route costs one round trip. The wire format is the one ``@ai-sdk/gateway``
speaks for ``experimental_evaluate`` (``POST {base}/evaluation-model``); the
AI SDK is TypeScript-only, so this module speaks it directly with ``httpx``.

When Jev is unreachable, misconfigured or slow, routing falls back to the
deterministic keyword router in :mod:`hubspot_mcp.agent_routing`, which needs
no network and already gates its own accuracy corpus in CI. The fallback is
recorded on the result so the caller can see which router answered.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

JEV_MODEL_ID = "typesafe-ai/jev"
GATEWAY_BASE_URL_ENV = "AI_GATEWAY_BASE_URL"
GATEWAY_API_KEY_ENV = "AI_GATEWAY_API_KEY"  # noqa: S105 — the env var name, not a secret
# `vercel env run` injects this for a linked project; the gateway accepts it in place of a key.
GATEWAY_OIDC_ENV = "VERCEL_OIDC_TOKEN"  # noqa: S105 — the env var name, not a secret
DEFAULT_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v4/ai"

# A boolean answer at or above this probability counts as a pick. Mirrors the
# sibling Pipedrive proof of concept; it has not been tuned on HubSpot traffic.
THRESHOLD = 0.5
NONE = "none"
DEFAULT_TIMEOUT_S = 3.0


def _timeout_s() -> float:
    try:
        return float(os.getenv("JEV_TIMEOUT_S", DEFAULT_TIMEOUT_S))
    except ValueError:
        return DEFAULT_TIMEOUT_S

CHARTER_QUESTION = "charter"
SKILL_QUESTION = "skill"


def _skill_key(name: str) -> str:
    return f"s_{name}"


def _charter_key(name: str) -> str:
    return f"c_{name}"


def _tool_key(name: str) -> str:
    return f"t_{name}"


@dataclass
class Ranked:
    name: str
    probability: float


@dataclass
class RouteDecision:
    router: str  # "jev" or "keyword"
    primary_charter: str | None
    charters: list[Ranked]  # primary first, then secondaries at or above THRESHOLD
    charter_distribution: dict[str, float]
    tools: list[Ranked]  # tools the router itself picked (charter tools are merged later)
    routing_ms: int
    note: str | None = None
    cost_usd: float | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    # Skills: HubSpot's Sales workflows served by this server. Empty when the
    # caller routes without skills.
    primary_skill: str | None = None
    skills: list[Ranked] = field(default_factory=list)
    skill_distribution: dict[str, float] = field(default_factory=dict)


def build_questions(
    charters: dict[str, str], tools: dict[str, str], skills: dict[str, str] | None = None
) -> dict[str, dict[str, Any]]:
    """One ``choice`` over the charters plus a ``boolean`` per charter and per tool.

    The choice names one winner (or ``none``); the booleans cover requests that
    span two charters, which a single choice cannot express. With ``skills``,
    a second choice and a boolean per skill ride in the same request: a skill
    is a whole workflow (daily brief, call prep), a charter a domain manual.
    """
    questions: dict[str, dict[str, Any]] = {}
    if skills:
        questions[SKILL_QUESTION] = {
            "type": "choice",
            "instructions": (
                "Which ONE HubSpot sales skill (a multi-step workflow) should handle this request? "
                "Respect each skill's 'ALWAYS use' triggers and its exclusions. Pick 'none' if no "
                "workflow fits, for example a one-off lookup or edit, or a request unrelated to HubSpot."
            ),
            "criteria": {
                **skills,
                NONE: "No sales workflow applies: a plain lookup or edit, or not about HubSpot at all.",
            },
        }
        for name, description in skills.items():
            questions[_skill_key(name)] = {
                "type": "boolean",
                "instructions": (
                    f'Does this request need the "{name}" skill, alone or together with another skill? '
                    f"Skill: {description}"
                ),
            }
    if not charters:
        for name, description in tools.items():
            questions[_tool_key(name)] = {
                "type": "boolean",
                "instructions": (
                    f'Will fulfilling this request require calling the HubSpot tool "{name}"? '
                    f"Tool: {description}"
                ),
            }
        return questions
    questions.update({
        CHARTER_QUESTION: {
            "type": "choice",
            "instructions": (
                "Which ONE HubSpot specialist charter should handle this request to a "
                "HubSpot CRM assistant? Pick 'none' if the request is not about HubSpot."
            ),
            "criteria": {
                **charters,
                NONE: "The request is unrelated to HubSpot CRM, marketing, sales, service or its data.",
            },
        }
    })
    for name, description in charters.items():
        questions[_charter_key(name)] = {
            "type": "boolean",
            "instructions": (
                f'Does this request need the "{name}" charter, alone or together with '
                f"another charter? Charter: {description}"
            ),
        }
    for name, description in tools.items():
        questions[_tool_key(name)] = {
            "type": "boolean",
            "instructions": (
                f'Will fulfilling this request require calling the HubSpot tool "{name}"? '
                f"Tool: {description}"
            ),
        }
    return questions


class JevUnavailable(RuntimeError):
    """Jev could not answer: no key, a transport error, a non-2xx, or a bad body."""


def _gateway_url() -> str:
    base = os.getenv(GATEWAY_BASE_URL_ENV, DEFAULT_GATEWAY_BASE_URL).rstrip("/")
    # The bearer key rides on this request; never send it in the clear.
    if not (base.startswith("https://") or base.startswith("http://127.0.0.1") or base.startswith("http://localhost")):
        raise JevUnavailable(f"{GATEWAY_BASE_URL_ENV} must be https (got {base[:40]!r})")
    return f"{base}/evaluation-model"


async def jev_evaluate(state: str, questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """POST one evaluation request and return the parsed body.

    Body: ``{"answers": {key: answer}, "usage": {...}, "providerMetadata": {...}}``
    where a ``choice`` answer is ``{"type":"choice","choice":..., "probabilities":{...}}``
    and a ``boolean`` answer is ``{"type":"boolean","probability":...}``.
    """
    key = os.getenv(GATEWAY_API_KEY_ENV, "").strip()
    auth_method = "api-key"
    if not key:
        key = os.getenv(GATEWAY_OIDC_ENV, "").strip()
        auth_method = "oidc"
    if not key:
        raise JevUnavailable(f"neither {GATEWAY_API_KEY_ENV} nor {GATEWAY_OIDC_ENV} is set")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "ai-gateway-protocol-version": "0.0.1",
        "ai-gateway-auth-method": auth_method,
        "ai-evaluation-model-specification-version": "4",
        "ai-model-id": JEV_MODEL_ID,
    }
    try:
        async with httpx.AsyncClient(timeout=_timeout_s()) as http:
            response = await http.post(
                _gateway_url(), headers=headers, json={"state": state, "questions": questions}
            )
    except httpx.HTTPError as exc:
        raise JevUnavailable(f"gateway request failed: {exc}") from exc
    if response.status_code >= 400:
        raise JevUnavailable(f"gateway returned {response.status_code}: {response.text[:200]}")
    try:
        body = response.json()
    except ValueError as exc:
        raise JevUnavailable("gateway returned a non-JSON body") from exc
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        raise JevUnavailable("gateway body has no answers")
    return body


def _gateway_cost(body: dict[str, Any]) -> float | None:
    cost = ((body.get("providerMetadata") or {}).get("gateway") or {}).get("cost")
    try:
        return float(cost) if cost is not None else None
    except (TypeError, ValueError):
        return None


def _pick(
    answers: dict[str, Any], question: str, key_fn: Any, options: dict[str, str]
) -> tuple[str | None, list[Ranked], dict[str, float]]:
    """Winner of a choice question plus the booleans at or above THRESHOLD."""
    choice = answers.get(question) or {}
    distribution: dict[str, float] = dict(choice.get("probabilities") or {})
    if not distribution and choice.get("choice"):
        distribution = {str(choice["choice"]): 1.0}
    ranked = sorted(distribution.items(), key=lambda kv: kv[1], reverse=True)
    primary: str | None = None
    picked: list[Ranked] = []
    if ranked and ranked[0][0] != NONE and ranked[0][0] in options:
        primary = ranked[0][0]
        picked.append(Ranked(primary, round(ranked[0][1], 3)))
        secondaries = [
            Ranked(name, round(float((answers.get(key_fn(name)) or {}).get("probability", 0.0)), 3))
            for name in options
            if name != primary
        ]
        picked.extend(sorted((s for s in secondaries if s.probability >= THRESHOLD), key=lambda s: -s.probability))
    return primary, picked, {k: round(v, 3) for k, v in ranked[:5]}


def decide_from_answers(
    body: dict[str, Any],
    charters: dict[str, str],
    tools: dict[str, str],
    routing_ms: int,
    skills: dict[str, str] | None = None,
) -> RouteDecision:
    """Turn Jev's answers into a decision. Pure, so tests can feed canned bodies."""
    answers: dict[str, Any] = body["answers"]
    primary_skill: str | None = None
    picked_skills: list[Ranked] = []
    skill_dist: dict[str, float] = {}
    if skills:
        primary_skill, picked_skills, skill_dist = _pick(answers, SKILL_QUESTION, _skill_key, skills)
    choice = answers.get(CHARTER_QUESTION) or {}
    distribution: dict[str, float] = dict(choice.get("probabilities") or {})
    if not distribution and choice.get("choice"):
        distribution = {str(choice["choice"]): 1.0}
    ranked = sorted(distribution.items(), key=lambda kv: kv[1], reverse=True)

    primary: str | None = None
    picked_charters: list[Ranked] = []
    if ranked and ranked[0][0] != NONE and ranked[0][0] in charters:
        primary = ranked[0][0]
        picked_charters.append(Ranked(primary, round(ranked[0][1], 3)))
        secondaries = [
            Ranked(name, round(float((answers.get(_charter_key(name)) or {}).get("probability", 0.0)), 3))
            for name in charters
            if name != primary
        ]
        picked_charters.extend(
            sorted((s for s in secondaries if s.probability >= THRESHOLD), key=lambda s: -s.probability)
        )

    picked_tools = [
        Ranked(name, round(float((answers.get(_tool_key(name)) or {}).get("probability", 0.0)), 3))
        for name in tools
    ]
    picked_tools = sorted((t for t in picked_tools if t.probability >= THRESHOLD), key=lambda t: -t.probability)

    return RouteDecision(
        router="jev",
        primary_charter=primary,
        charters=picked_charters,
        charter_distribution={k: round(v, 3) for k, v in ranked[:5]},
        tools=picked_tools,
        routing_ms=routing_ms,
        cost_usd=_gateway_cost(body),
        usage=dict(body.get("usage") or {}),
        primary_skill=primary_skill,
        skills=picked_skills,
        skill_distribution=skill_dist,
    )


def keyword_decision(
    task: str, portal_id: str | None, routing_ms: int, note: str, *, with_skills: bool = False
) -> RouteDecision:
    """The fallback: the deterministic keyword routers, dressed in the same shape.

    Keyword scores are not probabilities. The primary gets 1.0 and every other
    candidate the threshold value, so downstream code treats them as picks
    without pretending to a confidence the router never produced.
    """
    from hubspot_mcp.agent_routing import route_request

    agents = route_request(task, portal_id=portal_id)
    charters = [Ranked(a, 1.0 if i == 0 else THRESHOLD) for i, a in enumerate(agents)]
    decision = RouteDecision(
        router="keyword",
        primary_charter=agents[0] if agents else None,
        charters=charters,
        charter_distribution={c.name: c.probability for c in charters},
        tools=[],
        routing_ms=routing_ms,
        note=note,
    )
    if with_skills:
        from hubspot_mcp.skills import keyword_rank

        ranked = keyword_rank(task)
        decision.skills = [Ranked(r.name, 1.0 if i == 0 else THRESHOLD) for i, r in enumerate(ranked[:3])]
        decision.primary_skill = ranked[0].name if ranked else None
        decision.skill_distribution = {s.name: s.probability for s in decision.skills}
    return decision


async def route(
    task: str,
    *,
    charters: dict[str, str],
    tools: dict[str, str],
    skills: dict[str, str] | None = None,
    portal_id: str | None = None,
) -> RouteDecision:
    """Route ``task``: Jev first, keyword routing when Jev cannot answer.

    ``HUBSPOT_MCP_ROUTER=keyword`` keeps routing on this machine: the request
    text and the tool descriptions then never leave the server. With Jev they
    go to Vercel's AI Gateway and TypeSafe, which the README says plainly.
    """
    started = time.perf_counter()
    with_skills = bool(skills)
    if os.getenv("HUBSPOT_MCP_ROUTER", "auto").strip().lower() == "keyword":
        return keyword_decision(task, portal_id, 0, note="HUBSPOT_MCP_ROUTER=keyword; keyword routing used.", with_skills=with_skills)
    try:
        body = await jev_evaluate(task, build_questions(charters, tools, skills))
    except JevUnavailable as exc:
        ms = int((time.perf_counter() - started) * 1000)
        return keyword_decision(
            task, portal_id, ms, note=f"Jev unavailable ({exc}); keyword routing used.", with_skills=with_skills
        )
    ms = int((time.perf_counter() - started) * 1000)
    try:
        return decide_from_answers(body, charters, tools, ms, skills)
    except (KeyError, TypeError, ValueError) as exc:
        return keyword_decision(
            task, portal_id, ms, note=f"Jev answer unreadable ({exc}); keyword routing used.", with_skills=with_skills
        )
