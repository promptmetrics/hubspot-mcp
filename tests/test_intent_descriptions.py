"""Description lint for the intent and grouped surfaces, and surface dispatch.

The benchmark compares /mcp/intent (arm C) with /mcp/grouped (arm D). Both
arms are judged through ``tools/list`` alone, so a thin description or an
undocumented parameter on one side would skew the comparison for reasons
that have nothing to do with tool shape. This pins the shared bar:

* tool counts (intent 10-14, grouped within 2 of intent) and naming
  (``pronova_`` on the intent surface only),
* every description says what the tool is for and what it returns,
* every parameter is documented in the input schema,
* the whole ``tools/list`` payload stays under a 10k-token budget,
* ``_SplitSurfaces`` sends /mcp/intent and /mcp/grouped, and their
  protected-resource metadata paths, to their own apps.
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest
from mcp import Client

INTENT_PREFIX = "pronova_"
TOKEN_BUDGET = 10_000
# Words the org's copy standard bans; tool descriptions are copy too.
BANNED_WORDS = ("unlock", "unleash", "elevate", "game-changer", "revolutionize", "supercharge")
MIN_DESCRIPTION = 80
MAX_DESCRIPTION = 650

# Tools that change data or act on a changeset; the rest are read tools and take response_format.
WRITE_TOOLS = {
    "pronova_apply_changes",
    "pronova_undo_changes",
    "pronova_propose_changes",
    "pronova_log_meeting_outcome",
    "manage_deal",
    "manage_contact",
    "manage_engagement",
    "write_actions",
}
# Arm D design decision, recorded in grouped.py: its reads return HubSpot's body untrimmed, because
# trimming a body is server-side work the control arm leaves out. Every read tool NOT listed here
# must take response_format; adding a name to this set needs the same justification, in the report.
GROUPED_READS_WITHOUT_RESPONSE_FORMAT = {
    "deals",
    "contacts",
    "companies",
    "engagements",
    "associations",
    "pipelines",
    "owners",
    "get_sales_rules",
}


async def _tools(surface: str) -> list[Any]:
    from hubspot_mcp import server

    async with Client(getattr(server, surface)) as client:
        return list((await client.list_tools()).tools)


@pytest.fixture(params=["intent", "grouped"])
def surface_name(request) -> str:
    return request.param


@pytest.fixture
async def surface_tools(surface_name):
    tools = await _tools(surface_name)
    # An empty surface would pass every per-tool check vacuously.
    assert tools, f"the {surface_name} surface registered no tools"
    return surface_name, tools


def _schema(tool: Any) -> dict[str, Any]:
    """The tool's input schema; the SDK names the attribute ``input_schema`` or ``inputSchema``."""
    schema = getattr(tool, "input_schema", None)
    if schema is None:
        schema = tool.inputSchema
    return schema


def _approx_tokens(tools: list[Any]) -> int:
    payload = json.dumps(
        [t.model_dump(mode="json", exclude_none=True, by_alias=True) for t in tools],
        separators=(",", ":"),
    )
    return len(payload) // 4


def _is_documented(prop: Any) -> bool:
    """True when a schema property carries a description, directly or on a union branch."""
    if not isinstance(prop, dict):
        return False
    if str(prop.get("description", "")).strip():
        return True
    for key in ("anyOf", "oneOf", "allOf"):
        branches = prop.get(key) or []
        if any(isinstance(b, dict) and str(b.get("description", "")).strip() for b in branches):
            return True
    return False


# --------------------------------------------------------------------------- #
# Counts and names
# --------------------------------------------------------------------------- #


async def test_intent_surface_has_ten_to_fourteen_tools_all_prefixed_pronova():
    tools = await _tools("intent")
    names = sorted(t.name for t in tools)
    assert 10 <= len(tools) <= 14, f"intent surface has {len(tools)} tools: {names}"
    unprefixed = [n for n in names if not n.startswith(INTENT_PREFIX)]
    assert unprefixed == [], f"intent tools must start with {INTENT_PREFIX!r}: {unprefixed}"


async def test_grouped_tool_count_is_within_two_of_the_intent_count():
    intent, grouped = await _tools("intent"), await _tools("grouped")
    assert abs(len(grouped) - len(intent)) <= 2, (
        f"arm D must match arm C's tool count: intent={len(intent)} grouped={len(grouped)}"
    )


async def test_grouped_tools_do_not_use_the_pronova_prefix():
    names = [t.name for t in await _tools("grouped")]
    assert names, "the grouped surface registered no tools"
    assert [n for n in names if n.startswith(INTENT_PREFIX)] == [], (
        "the grouped control is not an intent surface; its tools must not be named like one"
    )


async def test_tool_names_are_unique_snake_case_on_each_surface(surface_tools):
    name, tools = surface_tools
    names = [t.name for t in tools]
    assert len(names) == len(set(names)), f"{name}: duplicate tool names"
    bad = [n for n in names if not re.fullmatch(r"[a-z][a-z0-9_]*", n)]
    assert bad == [], f"{name}: tool names must be snake_case: {bad}"


# --------------------------------------------------------------------------- #
# Descriptions
# --------------------------------------------------------------------------- #


async def test_every_description_states_purpose_and_output(surface_tools):
    name, tools = surface_tools
    problems: list[str] = []
    for t in tools:
        desc = (t.description or "").strip()
        if len(desc) < MIN_DESCRIPTION:
            problems.append(f"{t.name}: description is {len(desc)} chars, under {MIN_DESCRIPTION}")
        if len(desc) > MAX_DESCRIPTION:
            problems.append(f"{t.name}: description is {len(desc)} chars, over {MAX_DESCRIPTION}")
        if "return" not in desc.lower():
            problems.append(f"{t.name}: description never says what it returns")
        if not re.match(r"[A-Z]", desc):
            problems.append(f"{t.name}: description should open with a capitalised sentence stating purpose")
    assert problems == [], f"{name} surface:\n" + "\n".join(problems)


async def test_descriptions_avoid_banned_words(surface_tools):
    name, tools = surface_tools
    hits = [
        f"{t.name}: {w!r}"
        for t in tools
        for w in BANNED_WORDS
        if w in (t.description or "").lower()
    ]
    assert hits == [], f"{name} surface uses banned words: {hits}"


async def test_descriptions_are_not_copy_pasted_between_tools(surface_tools):
    name, tools = surface_tools
    seen: dict[str, str] = {}
    dupes = []
    for t in tools:
        key = (t.description or "").strip().lower()
        if key in seen:
            dupes.append(f"{t.name} repeats {seen[key]}")
        seen[key] = t.name
    assert dupes == [], f"{name} surface: {dupes}"


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #


async def test_every_parameter_is_documented_in_the_input_schema(surface_tools):
    name, tools = surface_tools
    missing = [
        f"{t.name}.{pname}"
        for t in tools
        for pname, prop in (_schema(t).get("properties") or {}).items()
        if not _is_documented(prop)
    ]
    assert missing == [], f"{name} surface has undocumented parameters: {missing}"


async def test_no_injected_parameter_leaks_into_a_tool_schema(surface_tools):
    name, tools = surface_tools
    leaks = [
        f"{t.name}.{p}"
        for t in tools
        for p in (_schema(t).get("properties") or {})
        if p in {"ctx", "context", "client", "portal_id"}
    ]
    assert leaks == [], f"{name}: injected parameters must not reach the model: {leaks}"


async def test_required_parameters_exist_in_the_schema(surface_tools):
    name, tools = surface_tools
    bad = [
        f"{t.name}.{r}"
        for t in tools
        for r in (_schema(t).get("required") or [])
        if r not in (_schema(t).get("properties") or {})
    ]
    assert bad == [], f"{name}: required parameter missing from properties: {bad}"


async def test_response_format_offers_concise_and_detailed(surface_tools):
    name, tools = surface_tools
    bad = []
    for t in tools:
        prop = (_schema(t).get("properties") or {}).get("response_format")
        if prop is None:
            continue
        schema = json.dumps(prop)
        if '"concise"' not in schema or '"detailed"' not in schema:
            bad.append(t.name)
    assert bad == [], f"{name}: response_format must offer concise and detailed: {bad}"


async def test_every_read_tool_takes_response_format_or_is_a_named_exception(surface_tools):
    name, tools = surface_tools
    exempt = GROUPED_READS_WITHOUT_RESPONSE_FORMAT if name == "grouped" else set()
    missing = [
        t.name
        for t in tools
        if t.name not in WRITE_TOOLS
        and t.name not in exempt
        and "response_format" not in (_schema(t).get("properties") or {})
    ]
    assert missing == [], f"{name}: read tools without response_format (contract hard rule): {missing}"


async def test_the_response_format_exceptions_are_real_tools_that_still_lack_it():
    """A stale exception list would hide a gap: each name must exist and still lack the parameter."""
    grouped = {t.name: t for t in await _tools("grouped")}
    stale = [
        n
        for n in sorted(GROUPED_READS_WITHOUT_RESPONSE_FORMAT)
        if n not in grouped or "response_format" in (_schema(grouped[n]).get("properties") or {})
    ]
    assert stale == [], f"remove from GROUPED_READS_WITHOUT_RESPONSE_FORMAT: {stale}"
    names = {t.name for t in await _tools("intent")} | set(grouped)
    assert WRITE_TOOLS <= names, f"unknown write tools: {sorted(WRITE_TOOLS - names)}"


def test_every_response_format_parameter_describes_what_detailed_adds_without_naming_deal_fields():
    from hubspot_mcp.intent import common

    description = common.ResponseFormatParam.__metadata__[0].description
    assert "detailed" in description and "concise" in description
    for deal_only in ("probability", "next step"):
        assert deal_only not in description, "the shared text is used by tools that do not return deal lines"


async def test_the_grouped_instructions_describe_what_the_write_tools_really_do():
    from hubspot_mcp import server
    from hubspot_mcp.intent import GROUPED_INSTRUCTIONS

    text = GROUPED_INSTRUCTIONS
    assert "status='applied'" in text and "already in HubSpot" in text, "a safe write is applied at once"
    assert "status='preview'" in text and "write_actions(action='approve'" in text
    assert "one per family" not in text, "there is no manage_company and engagements span families"
    grouped = {t.name for t in await server.grouped.list_tools()}
    named = set(re.findall(r"\b(deals|contacts|companies|engagements|associations|pipelines|owners|manage_[a-z]+|write_actions)\b", text))
    assert named <= grouped, f"the instructions name tools the surface does not have: {sorted(named - grouped)}"


# --------------------------------------------------------------------------- #
# Budget
# --------------------------------------------------------------------------- #


async def test_tools_list_stays_under_the_token_budget(surface_tools):
    name, tools = surface_tools
    tokens = _approx_tokens(tools)
    assert tokens < TOKEN_BUDGET, (
        f"{name} tools/list is about {tokens} tokens (chars/4); budget is {TOKEN_BUDGET}"
    )


async def test_the_two_surfaces_are_comparable_in_size():
    """A far larger list on one side would make the comparison about prompt size, not tool shape."""
    a, b = _approx_tokens(await _tools("intent")), _approx_tokens(await _tools("grouped"))
    assert max(a, b) <= 1.5 * min(a, b) or max(a, b) - min(a, b) < 1_500, (
        f"intent tools/list is ~{a} tokens, grouped is ~{b}; keep them comparable"
    )


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


class _App:
    """A stand-in Starlette app that records which app received a request."""

    def __init__(self, log: list[str], tag: str) -> None:
        self.log, self.tag = log, tag
        self.routes: list[Any] = []

    async def __call__(self, scope, receive, send):
        self.log.append(f"{self.tag}:{scope['path']}")


def _split(log: list[str]):
    from hubspot_mcp import server

    return server._SplitSurfaces(
        _App(log, "full"),
        _App(log, "routed"),
        {
            server.INTENT_PATH: _App(log, "intent"),
            server.GROUPED_PATH: _App(log, "grouped"),
        },
    )


def test_surface_paths_are_the_documented_mounts():
    from hubspot_mcp import server

    assert server.INTENT_PATH == "/mcp/intent"
    assert server.GROUPED_PATH == "/mcp/grouped"


@pytest.mark.parametrize(
    ("path", "want"),
    [
        ("/mcp", "full"),
        ("/mcp/routed", "routed"),
        ("/mcp/intent", "intent"),
        ("/mcp/intent/", "intent"),
        ("/mcp/grouped", "grouped"),
        ("/mcp/grouped/", "grouped"),
        ("/.well-known/oauth-protected-resource/mcp/intent", "intent"),
        ("/.well-known/oauth-protected-resource/mcp/grouped", "grouped"),
        ("/.well-known/oauth-protected-resource/mcp/routed", "routed"),
        ("/.well-known/oauth-protected-resource/mcp", "full"),
        ("/mcp/intentx", "full"),
        ("/mcp/groupedx", "full"),
        ("/healthz", "full"),
    ],
)
async def test_split_surfaces_sends_each_path_to_its_own_app(path, want):
    log: list[str] = []
    app = _split(log)

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        pass

    await app({"type": "http", "path": path}, receive, send)
    assert log == [f"{want}:{path}"], f"{path} reached {log}, wanted {want}"


def test_split_surfaces_exposes_every_surfaces_routes():
    app = _split([])
    app._full.routes = ["full-route"]
    app._surfaces["/mcp/intent"].routes = ["intent-route"]
    app._surfaces["/mcp/grouped"].routes = ["grouped-route"]
    assert {"full-route", "intent-route", "grouped-route"} <= set(app.routes)


def test_the_http_app_mounts_both_surfaces(monkeypatch):
    """build_http_app wires the real servers in; an unmounted surface would answer as /mcp."""
    from hubspot_mcp import server

    monkeypatch.delenv("HUBSPOT_MCP_SERVER_SECRET", raising=False)
    monkeypatch.setattr(server, "_resolve_portal_source", lambda: ("99999999", "test"))
    app = server.build_http_app("127.0.0.1")
    surfaces = getattr(app, "_surfaces", None) or getattr(getattr(app, "app", None), "_surfaces", None)
    assert surfaces is not None, "could not reach the split-surface dispatcher"
    assert {server.INTENT_PATH, server.GROUPED_PATH, server.ROUTED_PATH} <= set(surfaces)
