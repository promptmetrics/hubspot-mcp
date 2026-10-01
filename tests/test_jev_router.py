"""The Jev gateway client: wire format, failure modes, and the answers-to-decision step."""
from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from hubspot_mcp import jev_router
from hubspot_mcp.jev_router import JevUnavailable, build_questions, decide_from_answers, jev_evaluate, route

URL = "https://ai-gateway.vercel.sh/v4/ai/evaluation-model"


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-key")
    monkeypatch.delenv("AI_GATEWAY_BASE_URL", raising=False)


@respx.mock
async def test_jev_evaluate_sends_the_gateway_headers_and_body(key):
    route_ = respx.post(URL).mock(return_value=Response(200, json={"answers": {"q": {"type": "boolean", "probability": 0.7}}}))
    body = await jev_evaluate("task text", {"q": {"type": "boolean", "instructions": "x"}})
    assert body["answers"]["q"]["probability"] == 0.7
    req = route_.calls.last.request
    assert req.headers["authorization"] == "Bearer test-key"
    assert req.headers["ai-gateway-auth-method"] == "api-key"
    assert req.headers["ai-model-id"] == "typesafe-ai/jev"
    assert req.headers["ai-evaluation-model-specification-version"] == "4"
    assert json.loads(req.content) == {"state": "task text", "questions": {"q": {"type": "boolean", "instructions": "x"}}}


@respx.mock
async def test_jev_evaluate_failure_modes(key):
    respx.post(URL).mock(return_value=Response(401, text="nope"))
    with pytest.raises(JevUnavailable, match="401"):
        await jev_evaluate("t", {})
    respx.post(URL).mock(return_value=Response(200, text="not json"))
    with pytest.raises(JevUnavailable, match="non-JSON"):
        await jev_evaluate("t", {})
    respx.post(URL).mock(return_value=Response(200, json={"nope": 1}))
    with pytest.raises(JevUnavailable, match="no answers"):
        await jev_evaluate("t", {})


async def test_missing_key_and_cleartext_gateway_are_refused(monkeypatch):
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("VERCEL_OIDC_TOKEN", raising=False)
    with pytest.raises(JevUnavailable, match="neither"):
        await jev_evaluate("t", {})
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "k")
    monkeypatch.setenv("AI_GATEWAY_BASE_URL", "http://gateway.example.com/v4/ai")
    with pytest.raises(JevUnavailable, match="https"):
        await jev_evaluate("t", {})


def test_bad_timeout_env_falls_back(monkeypatch):
    monkeypatch.setenv("JEV_TIMEOUT_S", "3s")
    assert jev_router._timeout_s() == 3.0


def test_build_questions_skips_the_charter_choice_without_charters():
    q = build_questions({}, {"hubspot_get_object": "Get one."}, {"daily-brief": "Brief."})
    assert "skill" in q and "charter" not in q and "t_hubspot_get_object" in q and "s_daily-brief" in q


async def test_unreadable_answers_fall_back_to_keywords(monkeypatch):
    async def _weird(state, questions):
        return {"answers": {"skill": {"type": "choice", "choice": "daily-brief", "probabilities": {"daily-brief": "x"}}}}

    monkeypatch.setattr(jev_router, "jev_evaluate", _weird)
    d = await route("Good morning, what's on my plate today?", charters={}, tools={}, skills={"daily-brief": "Brief."})
    assert d.router == "keyword" and "unreadable" in (d.note or "")
    assert d.primary_skill == "daily-brief"


def test_decide_from_answers_threshold_and_distribution():
    body = {"answers": {"charter": {"type": "choice", "choice": "objects", "probabilities": {"objects": 0.6, "users": 0.3}},
                        "c_users": {"type": "boolean", "probability": 0.49}, "t_a": {"type": "boolean", "probability": 0.5}}}
    d = decide_from_answers(body, {"objects": "o", "users": "u"}, {"a": "A", "b": "B"}, 12)
    assert d.primary_charter == "objects" and [c.name for c in d.charters] == ["objects"]
    assert [t.name for t in d.tools] == ["a"] and d.routing_ms == 12


async def test_router_env_keeps_routing_local(monkeypatch):
    monkeypatch.setenv("HUBSPOT_MCP_ROUTER", "keyword")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "k")

    async def _boom(state, questions):
        raise AssertionError("Jev must not be called")

    monkeypatch.setattr(jev_router, "jev_evaluate", _boom)
    d = await route("Prep me for my call with Jamie", charters={}, tools={}, skills={"call-prep": "Prep."})
    assert d.router == "keyword" and "HUBSPOT_MCP_ROUTER" in (d.note or "")
