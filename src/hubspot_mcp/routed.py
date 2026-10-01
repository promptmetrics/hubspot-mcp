"""The routed surface: four tools in place of eighty-eight.

``/mcp`` advertises every HubSpot tool, about 35 KB of JSON that the host loads
into the model's context before the first request. ``/mcp/routed`` advertises
four:

* ``find_capabilities(task)`` routes the request to the specialist charter(s)
  and HubSpot tools it needs, and returns those tools' input schemas inline.
* ``load_charter(name)`` returns the charter's operating instructions. The full
  surface ships the 44 charters as MCP prompts, which Claude Code never pulls
  on its own; a tool result is the one channel every host reads.
* ``load_skill(name)`` returns one of HubSpot's Sales skills with a header that maps
  its connector tool names onto this server's tools.
* ``call_hubspot(tool, args)`` runs any of the 88 tools by name through the
  very same code path as its registered wrapper (``server._run_domain_tool``),
  so the write gate, the inline confirmation and undo behave identically.

The MCP server never sees the user's prompt, only tool arguments. Routing is
therefore a tool the model calls first, and the server instructions say so.

Everything here imports ``hubspot_mcp.server`` lazily: the server module
registers these tools at import time, and a module-level import back would be
circular.
"""
from __future__ import annotations

import time
from typing import Any

import jsonschema
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from hubspot_mcp.jev_router import RouteDecision, route
from hubspot_mcp.skills import helper_names, load_skills, skill_names
from hubspot_mcp.skills.mapping import server_tools_for, skill_header

FIND_DESCRIPTION = (
    "Routes a request to the matching HubSpot specialist charter(s) and tools, with a "
    "probability for each and the tools' input schemas inline. Call this first for anything "
    "involving HubSpot contacts, companies, deals, tickets, pipelines, properties, lists, "
    "workflows, engagements (notes, calls, tasks, emails, meetings), users, reports or data "
    "hygiene. Pass the user's request in their own words."
)
CALL_DESCRIPTION = (
    "Runs one HubSpot tool by name. Use the tool names and input schemas returned by "
    "find_capabilities. Writes go through the safety gate: a result with status='preview' "
    "and an action_id needs the user's confirmation, then "
    "call_hubspot(tool='hubspot_approve_write', args={'action_id': ...})."
)
LOAD_DESCRIPTION = (
    "Loads the operating charter for a specialist named by find_capabilities: scope, tool "
    "usage rules, self-correction and write-verification steps. Follow it."
)
LOAD_SKILL_DESCRIPTION = (
    "Loads one of HubSpot's Sales skills named by find_capabilities (daily-brief, call-prep, follow-up, "
    "log-call, contact-lookup, pipeline-pulse, import-contacts, onboarding, hubspot) or a helper "
    "(account-research, brief-fetcher): the full workflow, with a header mapping its tool names onto "
    "this server. Follow it step by step."
)

ROUTED_INSTRUCTIONS = (
    "HubSpot CRM through four tools. Before any HubSpot work, call find_capabilities with "
    "the user's request in their own words. It returns the matching sales skill(s), the matching "
    "charter(s) and the HubSpot tools they need, each with its input schema. If it names a primary "
    "skill, call load_skill for it and follow that workflow; otherwise call load_charter for the "
    "primary charter. Run every HubSpot tool through call_hubspot(tool, args). A write may return "
    "status='preview' with an action_id: show it to the user and approve it with "
    "call_hubspot(tool='hubspot_approve_write', args={'action_id': ...}) once they confirm."
)

CHARTER_HEADER = (
    "# Runtime\n"
    "The HubSpot tools this charter names are not exposed directly. Run each one through "
    "call_hubspot(tool=<name>, args={...}) with the input schema find_capabilities returned. "
    "A write may return status='preview' with an action_id and an approval tier: show the "
    "preview to the user, and once they confirm call "
    "call_hubspot(tool='hubspot_approve_write', args={'action_id': ..., 'confirm_count': ...}). "
    "Reject with hubspot_reject_write; revert an applied write with hubspot_undo_write. Where "
    "the text below says `hubspot approve <id>` or `hubspot undo <id>`, it means those calls.\n\n"
)

# Reachable through call_hubspot whenever a picked tool writes, so the approval
# flow never needs the safety tools as top-level entries.
SAFETY_FOR_WRITES = (
    "hubspot_approve_write",
    "hubspot_reject_write",
    "hubspot_undo_write",
    "hubspot_list_pending_writes",
)

_TOOL_INDEX: dict[str, Any] | None = None


async def _tool_index() -> dict[str, Any]:
    """Name → SDK ``Tool`` (name, description, inputSchema) for the full surface."""
    global _TOOL_INDEX
    if _TOOL_INDEX is None:
        from hubspot_mcp import server

        _TOOL_INDEX = {t.name: t for t in await server.mcp.list_tools()}
    return _TOOL_INDEX


def _safety_functions() -> dict[str, Any]:
    from hubspot_mcp import server

    return {name: fn for name, fn, _ in server._safety_tool_registrations()}


def _charters(portal_config: Any, portal_id: str | None) -> dict[str, Any]:
    """Every charter built for this portal. Not cached: the builders are cheap string
    work, and a cache would drift from what load_charter renders once custom objects change."""
    from hubspot_mcp.agents import _AGENT_REGISTRY

    return {key: builder(portal_config) for key, builder in _AGENT_REGISTRY.items()}


def _is_write(tool_name: str) -> bool:
    from hubspot_mcp.scope_registry import WRITE_TOOLS, get_required_scopes

    if tool_name == "hubspot_raw_api" or tool_name in WRITE_TOOLS:
        return True
    return any(s.endswith((".write", ".delete")) for s in get_required_scopes([tool_name]))


def _emit(portal_id: str | None, event_type: str, data: dict[str, Any]) -> None:
    from hubspot_mcp.trace import emit_safely

    emit_safely(portal_id, event_type, data)


def _format(decision: RouteDecision, charters: dict[str, Any], index: dict[str, Any]) -> dict[str, Any]:
    picked: dict[str, dict[str, Any]] = {}
    for r in decision.tools:
        picked[r.name] = {"probability": r.probability, "picked_by": "router"}
    for c in decision.charters:
        for name in charters[c.name].tool_names:
            if name in picked:
                picked[name]["picked_by"] = "router+charter"
            else:
                picked[name] = {"probability": None, "picked_by": "charter"}
    for sk in decision.skills:
        for name in server_tools_for(load_skills()[sk.name].tools):
            if name in picked:
                if "skill" not in picked[name]["picked_by"]:
                    picked[name]["picked_by"] += "+skill"
            else:
                picked[name] = {"probability": None, "picked_by": "skill"}
    if any(_is_write(n) for n in picked):
        for s in SAFETY_FOR_WRITES:
            picked.setdefault(s, {"probability": None, "picked_by": "safety"})

    def _rank(by: str) -> int:
        if by.startswith("router"):
            return 0
        if by == "safety":
            return 3
        return 2

    tools = [
        {
            "name": name,
            **meta,
            "description": index[name].description,
            "input_schema": index[name].input_schema,
        }
        for name, meta in sorted(
            picked.items(),
            key=lambda kv: (_rank(kv[1]["picked_by"]), -(kv[1]["probability"] or 0.0), kv[0]),
        )
        if name in index
    ]
    if decision.primary_skill:
        next_step = (
            f'Call load_skill(name="{decision.primary_skill}") and follow that workflow, running its '
            "tools through call_hubspot with the names the header gives."
            + (f' The charter "{decision.primary_charter}" is available through load_charter if the skill does not cover a step.' if decision.primary_charter else "")
        )
    elif decision.primary_charter:
        next_step = (
            f'Call load_charter(name="{decision.primary_charter}"), follow it, and run the '
            "tools above through call_hubspot."
        )
    else:
        next_step = (
            "No HubSpot charter matches. If the request is about HubSpot after all, call "
            "call_hubspot with one of the tools above; otherwise answer without HubSpot."
        )
    out: dict[str, Any] = {
        "router": decision.router,
        "primary_skill": decision.primary_skill,
        "skills": [
            {
                "name": s.name,
                "probability": s.probability,
                "description": load_skills()[s.name].description[:300],
                "tools_on_this_server": server_tools_for(load_skills()[s.name].tools),
            }
            for s in decision.skills
        ],
        "skill_choice_distribution": decision.skill_distribution,
        "primary_charter": decision.primary_charter,
        "charters": [
            {
                "name": c.name,
                "probability": c.probability,
                "domain": charters[c.name].domain_description,
            }
            for c in decision.charters
        ],
        "charter_choice_distribution": decision.charter_distribution,
        "tools": tools,
        "next_step": next_step,
        "routing_ms": decision.routing_ms,
    }
    if decision.note:
        out["note"] = decision.note
    if decision.cost_usd is not None:
        out["routing_cost_usd"] = decision.cost_usd
    return out


async def find_capabilities(ctx: Context, task: str) -> Any:
    """Route a HubSpot request to its charter(s) and tools, with probabilities. Call this first."""
    from hubspot_mcp import server

    lf = await server._safety_ctx(ctx)
    task = (task or "").strip()
    if len(task) < 3:
        raise ToolError("task must describe the request in the user's own words.")

    index = await _tool_index()
    safety_names = set(_safety_functions())
    charters = _charters(lf.get("portal_config"), lf.get("portal_id"))
    decision = await route(
        task,
        charters={key: built.domain_description for key, built in charters.items()},
        tools={n: (t.description or "") for n, t in index.items() if n not in safety_names},
        skills=skill_descriptions(),
        portal_id=lf.get("portal_id"),
    )
    result = _format(decision, charters, index)
    _emit(
        lf.get("portal_id"),
        "route_decision",
        {
            "router": decision.router,
            "primary_skill": decision.primary_skill,
            "skills": [s.name for s in decision.skills],
            "primary_charter": decision.primary_charter,
            "charters": [c.name for c in decision.charters],
            "tool_count": len(result["tools"]),
            "routing_ms": decision.routing_ms,
            "routing_cost_usd": decision.cost_usd,
            "note": decision.note,
        },
    )
    return result


async def call_hubspot(ctx: Context, tool: str, args: dict[str, Any] | None = None) -> Any:
    """Run one HubSpot tool by name, through the same gate as the full surface."""
    from hubspot_mcp import server

    index = await _tool_index()
    if tool not in index:
        raise ToolError(f"Unknown HubSpot tool {tool!r}. Use a name returned by find_capabilities.")
    payload = dict(args or {})
    try:
        jsonschema.validate(payload, index[tool].input_schema)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "root"
        raise ToolError(f"Invalid args for {tool} at {where}: {exc.message}") from exc
    known = set((index[tool].input_schema or {}).get("properties") or {})
    if known and (unknown := sorted(set(payload) - known)):
        raise ToolError(
            f"Invalid args for {tool}: unknown {', '.join(unknown)}. Accepted: {', '.join(sorted(known))}."
        )

    safety = _safety_functions()
    if tool in safety:
        started = time.perf_counter()
        lf = await server._session(ctx)
        result = await safety[tool](ctx, **payload)
        _emit(
            lf.get("portal_id"),
            "tool_call",
            {
                "tool_name": tool,
                "mode": "routed",
                "ok": True,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return result
    return await server._run_domain_tool(ctx, tool, payload, mode="routed")


def skill_descriptions() -> dict[str, str]:
    """Skill name → frontmatter description, the text Jev routes on."""
    return {n: load_skills()[n].description for n in skill_names()}


def render_skill(name: str, *, routed: bool) -> str:
    """Header plus the vendored text, byte-identical after the header."""
    # "Daily Brief", "daily brief" and "daily_brief" all mean daily-brief.
    key = "-".join(name.strip().lower().replace("_", " ").replace("-", " ").split())
    skill = load_skills().get(key)
    if skill is None:
        raise ToolError(
            f"Unknown skill {name!r}. Skills: {', '.join(skill_names())}. Helpers: {', '.join(helper_names())}."
        )
    return skill_header(skill.name, skill.tools, routed=routed, helper=skill.helper) + skill.body


async def load_skill(ctx: Context, name: str) -> str:
    """Return one of HubSpot's Sales skills, or a helper, for the routed surface."""
    from hubspot_mcp import server

    lf = await server._session(ctx)
    if lf.get("auth_error") and not lf.get("connect_url"):
        raise ToolError(lf["auth_error"])
    return render_skill(name, routed=True)


async def load_charter(ctx: Context, name: str) -> str:
    """Return a specialist charter's operating instructions for the routed surface."""
    from hubspot_mcp import server
    from hubspot_mcp.agents import _AGENT_REGISTRY

    lf = await server._session(ctx)
    # Same rule as the MCP prompts: a charter renders without a portal, and
    # refusing while HubSpot is not yet connected would hide the instructions
    # that explain how to connect.
    if lf.get("auth_error") and not lf.get("connect_url"):
        raise ToolError(lf["auth_error"])
    builder = _AGENT_REGISTRY.get(name)
    if builder is None:
        raise ToolError(f"Unknown charter {name!r}. Known charters: {', '.join(sorted(_AGENT_REGISTRY))}.")
    return CHARTER_HEADER + builder(lf.get("portal_config")).system_prompt


def register_routed_tools(routed: MCPServer) -> None:
    from hubspot_mcp.server import _answering_when_not_connected

    routed.add_tool(
        _answering_when_not_connected(find_capabilities),
        name="find_capabilities",
        description=FIND_DESCRIPTION,
    )
    routed.add_tool(call_hubspot, name="call_hubspot", description=CALL_DESCRIPTION)
    routed.add_tool(load_charter, name="load_charter", description=LOAD_DESCRIPTION)
    routed.add_tool(load_skill, name="load_skill", description=LOAD_SKILL_DESCRIPTION)
