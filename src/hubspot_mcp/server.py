"""MCP server (official ``mcp`` SDK): lifespan warm-client pool + dynamic per-tool registration.

Design (approach C — tools-only, no agent orchestrator):

* **Lifespan** resolves the active portal via a :class:`TokenProvider`, warms a
  single ``HubSpotClient`` + ``SchemaCache``, and yields them in the lifespan
  context. It does *not* raise on missing auth. Under 2026-07-28 there is no
  ``initialize`` handshake to fail, so ``server/discover`` and ``tools/list``
  must answer cold; raising here would take the whole server down instead.
  Instead it yields ``client=None`` + an ``auth_error`` string, and tool
  wrappers raise ``ToolError`` so ``tools/call`` fails fast with guidance until
  ``hubspot-mcp auth login`` is run.

* **Registration**: one ``@mcp.tool``-equivalent wrapper per registry entry (76
  domain tools). Each wrapper carries the tool's domain parameters (minus
  ``client``/``portal_id``) plus a leading ``ctx: Context``, so the SDK
  generates the correct per-tool JSON schema. The wrapper delegates to
  :func:`handle_tool`, which routes reads → ``invoke_tool`` and writes → the
  safety preview gate. ``HandlerError`` and returned error envelopes are
  re-raised as ``ToolError`` so the protocol layer sets ``is_error``.

* **Safety tools** (approve / reject / list-pending / audit / undo) are
  registered alongside the domain tools — see :func:`_register_safety_tools`.

Context injection note: the SDK finds the context parameter via
``typing.get_type_hints(fn)`` (``utilities/context_injection.find_context_parameter``),
which reads ``fn.__annotations__``, while the JSON schema is derived from
``inspect.signature(fn, eval_str=True)``, which honours ``__signature__``.
:func:`_make_domain_wrapper` synthesises both, so it must set ``__annotations__``
as well as ``__signature__`` — setting only the latter leaves ``ctx`` unresolved,
which both leaks it into every tool's input schema and stops it ever being
injected. ``tests/test_smoke.py`` pins this.
"""
import inspect
import os
import sys
import typing
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import mcp_types as T
from mcp.server.caching import CacheHint
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from hubspot_mcp import __version__
from hubspot_mcp.auth import EnvTokenProvider, OAuthProvider
from hubspot_mcp.auth.base import NotAuthenticatedError, TokenProvider
from hubspot_mcp.cache import SchemaCache, warm_standard_schemas
from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig, detect_default_portal
from hubspot_mcp.handlers import (
    HandlerError,
    handle_approve,
    handle_reject,
    handle_tool,
)
from hubspot_mcp.tools import ToolDef, list_tools

# Module-level config populated by ``configure_server`` before ``mcp.run()``.
# Kept here (not a closure) so ``__main__`` can set it and the lifespan can read
# it without threading state through the server constructor.
_SERVER_CONFIG: dict[str, Any] = {"portal_id": None, "mode": "oauth"}


def configure_server(*, portal_id: str | None = None, mode: str = "oauth") -> None:
    """Set the active portal + auth mode before ``mcp.run()``."""
    _SERVER_CONFIG["portal_id"] = portal_id
    _SERVER_CONFIG["mode"] = mode if mode in ("oauth", "token") else "oauth"


def _resolve_portal_source() -> tuple[str | None, str]:
    """Resolve the portal and report where it came from.

    The source matters to :func:`hubspot_mcp.tenancy.enforce_single_tenant`:
    working-directory detection is right for the local plugin and wrong for a
    hosted deployment, where the working directory is a build artifact.
    """
    if _SERVER_CONFIG["portal_id"]:
        return _SERVER_CONFIG["portal_id"], "flag"
    env = os.getenv("HUBSPOT_PORTAL")
    if env:
        return env, "env"
    detected = detect_default_portal(os.getcwd())
    return (detected, "file") if detected else (None, "none")


def _resolve_portal_id() -> str | None:
    return _resolve_portal_source()[0]


def _unresolved_lifespan() -> dict[str, Any]:
    """The hosted lifespan context: a session nobody should read.

    Every request goes through the resolver, so this is only reached if
    something bypasses it — in which case it must not hand back a working
    client for whatever portal happened to be configured.
    """
    return {
        "client": None,
        "cache": None,
        "portal_config": None,
        "portal_id": None,
        "auth_error": "No HubSpot session resolved for this request.",
        "capabilities": None,
    }


def _make_provider(mode: str) -> TokenProvider:
    return OAuthProvider() if mode == "oauth" else EnvTokenProvider()


@asynccontextmanager
async def app_lifespan(server: MCPServer):
    if _TOKEN_VERIFIER is not None:
        # Hosted: nothing portal-specific exists at startup, because the portal
        # is a property of whoever is calling. Everything is resolved per
        # request; the lifespan's only job is owning the client pool's lifetime.
        from hubspot_mcp.hosted_session import build_session_resolver

        resolver = build_session_resolver()
        set_session_resolver(resolver)
        try:
            yield _unresolved_lifespan()
        finally:
            set_session_resolver(None)
            await resolver.pool.close_all()
        return

    portal_id = _resolve_portal_id()
    provider = _make_provider(_SERVER_CONFIG["mode"])

    client: HubSpotClient | None = None
    cache: SchemaCache | None = None
    portal_config: PortalConfig | None = None
    auth_error: str | None = None
    capabilities = None
    capabilities_conclusive = False

    if portal_id is None:
        auth_error = (
            "No HubSpot portal configured. Set HUBSPOT_PORTAL, create a "
            ".hubspot-portal file, or run with --portal <id>."
        )
    else:
        try:
            portal_config = await provider.resolve(portal_id)
            cache = await warm_standard_schemas(portal_config)
            client = HubSpotClient(portal_config)
            capabilities, capabilities_conclusive = await _probe_capabilities(portal_config)
        except NotAuthenticatedError as exc:
            auth_error = str(exc)
        except Exception as exc:  # noqa: BLE001 — surface any init failure as guidance
            auth_error = f"Failed to initialize HubSpot client for portal {portal_id}: {exc}"

    try:
        if capabilities is not None and capabilities_conclusive:
            _unadvertise_unavailable_tools(capabilities)
        yield {
            "client": client,
            "cache": cache,
            "portal_config": portal_config,
            "portal_id": portal_id,
            "auth_error": auth_error,
            "capabilities": capabilities,
        }
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:  # noqa: BLE001 — teardown must never mask startup
                pass


# SEP-2549 cache hints. ``scope="private"`` is deliberate and load-bearing:
# the advertised tool list becomes portal-dependent once capability gating
# lands, and a shared intermediary must never serve one portal's list to
# another. ``ttl_ms`` is a freshness hint only — ``listChanged`` still applies.
# The Streamable HTTP mount path. Load-bearing beyond routing: the OAuth
# resource identifier is this path appended to the public URL, and that string
# has to match in three places — what the authorization server stamps as `aud`,
# what we verify, and what the client sends as `resource`. One constant, so they
# cannot drift.
MCP_PATH = "/mcp"


def _hosted_auth() -> tuple[Any, Any]:
    """Return ``(AuthSettings, TokenVerifier)`` when hosted OAuth is configured.

    Configured means ``HUBSPOT_MCP_OAUTH_ISSUER`` is set. Absent it, the server
    stays on the Phase 1 path — stdio needs no authorization at all, and a
    self-hosted HTTP deployment can still use the shared-secret bearer wrapper.
    """
    from mcp.server.auth.settings import AuthSettings
    from pydantic import AnyHttpUrl

    from hubspot_mcp.auth.connect import PUBLIC_URL_ENV
    from hubspot_mcp.auth.token_verifier import ISSUER_ENV, JWTVerifier

    issuer = os.getenv(ISSUER_ENV, "").strip().rstrip("/")
    if not issuer:
        return None, None

    public_url = os.getenv(PUBLIC_URL_ENV, "").strip().rstrip("/")
    if not public_url:
        raise RuntimeError(
            f"{ISSUER_ENV} is set but {PUBLIC_URL_ENV} is not. The resource identifier "
            "is built from the public URL, and a token whose audience does not match it "
            "is rejected — so serving without it would 401 every request."
        )

    resource = f"{public_url}{MCP_PATH}"
    return (
        AuthSettings(
            issuer_url=AnyHttpUrl(issuer),
            resource_server_url=AnyHttpUrl(resource),
        ),
        JWTVerifier(issuer, resource),
    )


_AUTH_SETTINGS, _TOKEN_VERIFIER = _hosted_auth()

mcp = MCPServer(
    "hubspot-mcp",
    version=__version__,
    lifespan=app_lifespan,
    auth=_AUTH_SETTINGS,
    token_verifier=_TOKEN_VERIFIER,
    cache_hints={
        "tools/list": CacheHint(ttl_ms=300_000, scope="private"),
        "prompts/list": CacheHint(ttl_ms=300_000, scope="private"),
        "server/discover": CacheHint(ttl_ms=300_000, scope="private"),
    },
)



async def _probe_capabilities(portal_config: PortalConfig):
    """Probe portal entitlements; never fail startup over it."""
    from hubspot_mcp.capabilities import probe_portal, probe_was_conclusive

    try:
        matrix = await probe_portal(portal_config)
    except Exception:  # noqa: BLE001 — an unreachable portal must not stop the server
        return None, False
    return matrix, await probe_was_conclusive(portal_config.portal_id)


def _unadvertise_unavailable_tools(matrix) -> None:
    """Drop tools this portal is not entitled to from ``tools/list``.

    Only ever called with a CONCLUSIVE probe. On a transient probe failure the
    matrix sits at its defaults -- workflows/users/marketing/cms/custom_objects
    are all False -- so filtering on it would silently unadvertise a dozen tools
    after one network blip, and the model would simply report it cannot manage
    workflows. An inconclusive probe advertises everything and lets the
    call-time check explain any refusal instead.

    Safe to mutate the module-level server: one process serves one portal
    (``_resolve_portal_id``). It is also why ``tools/list`` must be
    ``cacheScope: "private"`` -- the listing is portal-specific.
    """
    from hubspot_mcp.capabilities import missing_capabilities_for_tool, tool_capability_requirements

    for tool_name in tool_capability_requirements():
        if missing_capabilities_for_tool(tool_name, matrix):
            try:
                mcp.remove_tool(tool_name)
            except Exception:  # noqa: BLE001 — never registered, or already gone
                pass


# The per-request session seam (Phase 3).
#
# Every one of the 86 tools reaches its HubSpot client, schema cache and
# PortalConfig through `_session`, so this one function decides whose portal a
# request acts on. Local/stdio resolves one portal per process at startup and
# `_session` just hands back the lifespan context. The hosted path installs a
# resolver that derives the portal from the caller's access token instead, and
# no tool body changes — the same shape as `state.get_store()`.
# `Callable`/`Awaitable` are imported for real rather than quoted: this module
# has no `from __future__ import annotations` (see the module docstring), so the
# alias is evaluated at import time.
SessionResolver = Callable[[Context], Awaitable[dict[str, Any]]]

_session_resolver: SessionResolver | None = None


def set_session_resolver(resolver: SessionResolver | None) -> None:
    """Install a per-request session resolver, or reset to single-portal mode."""
    global _session_resolver
    _session_resolver = resolver


async def _session(ctx: Context) -> dict[str, Any]:
    """Resolve the HubSpot session this request acts on.

    Returns the established shape — ``client``, ``cache``, ``portal_config``,
    ``portal_id``, ``auth_error``, ``capabilities``. ``auth_error`` set means
    the tools still answer but cannot act, which is what lets `tools/list` work
    cold; under 2026-07-28 there is no handshake in which to fail instead.
    """
    if _session_resolver is not None:
        return await _session_resolver(ctx)
    return _lifespan(ctx)


def _lifespan(ctx: Context) -> dict[str, Any]:
    """Return the lifespan-context dict (single-portal mode only).

    Prefer :func:`_session`: this bypasses the resolver and so always answers
    for the process-wide portal, which is wrong on a multi-tenant deployment.
    """
    return ctx.request_context.lifespan_context


def _raise_if_error(data: Any) -> Any:
    """Surface a tool's error envelope as a real MCP error.

    Tool functions catch ``(HubSpotError, RateLimitError, ScopeError)`` and
    return ``{"error": ..., "tool": ...}`` rather than raising — an internal
    contract the handler layer depends on (discarding it once caused a silent
    "restored 0 records" undo bug upstream). But returned as-is it reaches the
    client as an ordinary successful result, so a failed call looks like a
    success. Raising ``ToolError`` here makes the protocol layer set
    ``is_error=True`` while leaving the internal envelope untouched.
    """
    if isinstance(data, dict):
        inner = data.get("result")
        if isinstance(inner, dict) and inner.get("error"):
            raise ToolError(str(inner["error"]))
    return data


def _is_callable_annotation(ann: Any) -> bool:
    """True if ``ann`` is (subscripted) ``Callable`` — not JSON-schema-able.

    Also detects ``Callable`` nested in ``Optional``/``Union`` (e.g.
    ``SearchBackend | None``), since ``typing.get_origin`` on a Union returns
    ``Union``, not ``Callable``.
    """
    import collections.abc as cabc
    import types

    def _is_call(a: Any) -> bool:
        if a in (typing.Callable, cabc.Callable):
            return True
        return typing.get_origin(a) is cabc.Callable

    if _is_call(ann):
        return True
    origin = typing.get_origin(ann)
    if origin is typing.Union or origin is types.UnionType:
        return any(_is_call(a) for a in typing.get_args(ann) if a is not type(None))
    return False


def _domain_params(func: Any) -> list[inspect.Parameter]:
    """Return the tool func's MCP-exposable parameters.

    Drops ``client``/``portal_id`` (injected by ``invoke_tool``) and any
    ``Callable``-annotated param (e.g. ``hubspot_docs_search.search_backend``)
    — an MCP client cannot supply a callable, so the tool falls back to its
    default backend and pydantic never has to schema a ``Callable``.

    Annotations are resolved to real types via :func:`typing.get_type_hints`
    (which evals string forward-refs against the tool func's own ``__globals__``)
    so the SDK doesn't try to eval them against the *wrapper's* globals
    (server.py) — that would fail for any type imported in the tool's module.
    """
    sig = inspect.signature(func)
    try:
        hints = typing.get_type_hints(func)
    except Exception:  # noqa: BLE001 — fall back to raw (possibly string) annotations
        hints = {}
    out: list[inspect.Parameter] = []
    for name, p in sig.parameters.items():
        if name in ("client", "portal_id"):
            continue
        ann = hints.get(name, p.annotation)
        if ann is inspect.Parameter.empty:
            ann = inspect.Parameter.empty
        if _is_callable_annotation(ann):
            continue
        out.append(p.replace(annotation=ann))
    return out



# --- Multi Round-Trip Requests (SEP-2322) ------------------------------------
#
# 2026-07-28 removes the server-initiated back-channel, so ``ctx.elicit`` raises
# NoBackChannelError. The replacement is MRTR: a tool returns
# ``resultType: "input_required"`` naming what it still needs, and the client
# retries the SAME call with ``inputResponses`` plus the opaque ``requestState``
# the server minted. That collapses the write gate from two tool calls
# (write -> hubspot_approve_write) into one round-tripped call.
#
# Strictly opt-in: a client that has not declared the form-elicitation
# capability gets ``MCPError: Elicitation not supported`` for the whole call, so
# every write would break. When the capability is absent we return the ordinary
# preview and the classic approve/reject tools remain the path -- which is also
# what handshake-era clients and cross-session approvals need.

_CONFIRM_KEY = "confirm"


def _supports_form_elicitation(ctx: Context) -> bool:
    caps = getattr(ctx, "client_capabilities", None)
    elicitation = getattr(caps, "elicitation", None) if caps else None
    return getattr(elicitation, "form", None) is not None


def _confirmation_request(data: dict[str, Any]) -> "T.InputRequiredResult":
    """Build the input_required result for a pending write preview."""
    action_id = data["action_id"]
    impact = data.get("impact_count", 1)
    requires_count = bool(data.get("requires_count"))
    tier = data.get("approval_tier")

    properties: dict[str, Any] = {
        "approve": {"type": "boolean", "description": "Apply this write."}
    }
    required = ["approve"]
    if requires_count:
        # FULL_GATE keeps the typed-count ceremony: the operator must state the
        # impact back, which is the whole point of the destructive gate.
        properties["confirm_count"] = {
            "type": "integer",
            "description": f"Type the exact number of affected records ({impact}) to confirm.",
        }
        required.append("confirm_count")

    lines = [
        f"Approve {data.get('tool')} on {impact} record(s)?",
        f"action_id: {action_id}  tier: {tier}",
    ]
    pattern = data.get("pattern")
    if pattern:
        lines.append(f"Pattern rule matches {pattern.get('count')} record(s).")
    if data.get("original_values"):
        lines.append(f"Captured {len(data['original_values'])} record(s) for undo.")
    else:
        lines.append("No values captured — this write will NOT be undoable.")

    return T.InputRequiredResult(
        input_requests={
            _CONFIRM_KEY: T.ElicitRequest(
                method="elicitation/create",
                params=T.ElicitRequestFormParams(
                    mode="form",
                    message="\n".join(lines),
                    requested_schema={
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                ),
            )
        },
        # The action_id is the server-minted handle the spec asks for; the
        # pending preview itself stays on disk, so the retry may land on any
        # process (SEP-2567).
        request_state=action_id,
    )


def _mrtr_answer(ctx: Context) -> tuple[str, bool, int | None] | None:
    """Return ``(action_id, approved, confirm_count)`` when resuming, else None."""
    responses = getattr(ctx, "input_responses", None)
    action_id = getattr(ctx, "request_state", None)
    if not responses or not action_id:
        return None
    answer = responses.get(_CONFIRM_KEY)
    if answer is None:
        return None
    if getattr(answer, "action", None) != "accept":
        return (action_id, False, None)
    content = getattr(answer, "content", None) or {}
    return (action_id, bool(content.get("approve")), content.get("confirm_count"))


def _make_domain_wrapper(tool_def: ToolDef):
    """Build an async MCP wrapper that delegates one tool to ``handle_tool``."""
    domain_params = _domain_params(tool_def.func)
    new_params = [
        inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context),
        *domain_params,
    ]
    name = tool_def.name

    async def wrapper(ctx: Context, **kwargs: Any) -> Any:
        lf = await _session(ctx)
        if lf.get("auth_error"):
            raise ToolError(lf["auth_error"])

        # Call-time entitlement check. Belt and braces: an inconclusive probe
        # leaves the tool advertised, so this is where the operator finds out
        # WHY it cannot run, instead of the tool silently missing.
        matrix = lf.get("capabilities")
        if matrix is not None:
            from hubspot_mcp.capabilities import (
                capability_explanation,
                missing_capabilities_for_tool,
            )

            missing = missing_capabilities_for_tool(name, matrix)
            if missing:
                raise ToolError(
                    f"{name} is unavailable on this HubSpot portal: "
                    + "; ".join(capability_explanation(f) for f in missing)
                )

        # MRTR resume: this retry carries the operator's answer plus the
        # action_id minted on the first round. Resolve the pending preview
        # instead of re-running the tool, which would mint a second one.
        answer = _mrtr_answer(ctx)
        if answer is not None:
            action_id, approved, confirm_count = answer
            handler, params = (
                (handle_approve, {"action_id": action_id, "confirm_count": confirm_count})
                if approved
                else (handle_reject, {"action_id": action_id})
            )
            try:
                result = await handler(
                    lf["client"], lf["cache"], lf["portal_config"], params
                )
            except HandlerError as exc:
                raise ToolError(exc.error["message"]) from exc
            return _raise_if_error(result["data"])

        try:
            result = await handle_tool(
                lf["client"], lf["cache"], lf["portal_config"], {"tool_name": name, "input": kwargs}
            )
        except HandlerError as exc:
            raise ToolError(exc.error["message"]) from exc
        data = _raise_if_error(result["data"])

        # A write that needs a human decision asks for it inline, when the
        # client can answer. AUTO-tier writes already applied and never reach
        # here with status "preview".
        if (
            isinstance(data, dict)
            and data.get("status") == "preview"
            and data.get("action_id")
            and _supports_form_elicitation(ctx)
        ):
            return _confirmation_request(data)
        return data

    wrapper.__name__ = name
    wrapper.__qualname__ = name
    wrapper.__doc__ = tool_def.description
    wrapper.__signature__ = inspect.Signature(new_params)  # type: ignore[attr-defined]
    # The SDK resolves the context parameter from ``__annotations__`` (via
    # ``typing.get_type_hints``) but builds the schema from ``__signature__``.
    # Both must agree or ``ctx`` leaks into the schema and is never injected.
    wrapper.__annotations__ = {
        p.name: p.annotation for p in new_params if p.annotation is not inspect.Parameter.empty
    }
    return wrapper


def _domain_tool_registrations() -> list[tuple[str, Any, str]]:
    return [
        (td.name, _make_domain_wrapper(td), td.description) for td in list_tools()
    ]


# --- Safety stateful tools (approve / reject / pending / audit / undo) --------

async def _safety_ctx(ctx: Context) -> dict[str, Any]:
    lf = await _session(ctx)
    if lf.get("auth_error"):
        raise ToolError(lf["auth_error"])
    return lf


async def hubspot_approve_write(ctx: Context, action_id: str, confirm_count: int | None = None) -> Any:
    """Approve and execute a pending write preview (destructive actions require confirm_count == impact)."""
    lf = await _safety_ctx(ctx)
    try:
        result = await handle_approve(
            lf["client"], lf["cache"], lf["portal_config"],
            {"action_id": action_id, "confirm_count": confirm_count},
        )
    except HandlerError as exc:
        raise ToolError(exc.error["message"]) from exc
    return result["data"]


async def hubspot_reject_write(ctx: Context, action_id: str) -> Any:
    """Reject and discard a pending write preview."""
    lf = await _safety_ctx(ctx)
    try:
        result = await handle_reject(
            lf["client"], lf["cache"], lf["portal_config"], {"action_id": action_id}
        )
    except HandlerError as exc:
        raise ToolError(exc.error["message"]) from exc
    return result["data"]


async def hubspot_list_pending_writes(ctx: Context) -> Any:
    """List pending (not-yet-approved) write previews for the active portal."""
    lf = await _safety_ctx(ctx)
    from hubspot_mcp.state import get_store

    return {"pending": await get_store().list_pending(lf["portal_id"])}


async def hubspot_list_recent_audit(ctx: Context, limit: int = 20) -> Any:
    """List recent audit-log entries for the active portal."""
    lf = await _safety_ctx(ctx)
    from hubspot_mcp.state import get_store

    return {"audit": await get_store().get_recent_audits(lf["portal_id"], limit=limit)}


async def hubspot_undo_write(ctx: Context, action_id: str) -> Any:
    """Undo a previously approved write using its saved snapshot (best-effort).

    Delegates to :func:`handlers.undo_action`, which owns the restore semantics:
    updates replay only writable properties (replaying read-only system fields
    makes HubSpot 400 the whole update), every per-record error envelope is
    checked so a failed restore is never reported as success, creates delete
    their captured ``created_ids`` tolerating 404s so a retry converges, and
    deletes/merges are refused outright.
    """
    lf = await _safety_ctx(ctx)
    from hubspot_mcp.handlers import undo_action
    from hubspot_mcp.state import get_store

    portal_id = lf["portal_id"]
    store = get_store()
    snapshot = await store.load_undo_snapshot(portal_id, action_id)
    if snapshot is None:
        raise ToolError(f"No undo snapshot for action {action_id}.")

    succeeded, message = await undo_action(
        snapshot, portal_id, lf["portal_config"], client=lf["client"]
    )
    if not succeeded:
        # A failed undo KEEPS the snapshot -- it is the only reconciliation
        # artifact -- and writes no audit entry, so the log never records an
        # undo that did not happen.
        raise ToolError(message)

    await store.delete_undo_snapshot(portal_id, action_id)
    try:
        await store.log_write(
            portal_id=portal_id,
            action=f"undo:{action_id}",
            agent=(snapshot.get("metadata") or {}).get("intent_type") or "unknown",
            result_summary={"message": message},
        )
    except Exception:  # noqa: BLE001 — audit failure must not mask a successful undo
        pass
    return {"undo": action_id, "result": message}


async def hubspot_status(ctx: Context, window_hours: int = 24) -> Any:
    """Portal status: entitlements plus request/error/cost aggregates from the trace log."""
    lf = await _safety_ctx(ctx)
    from hubspot_mcp.trace import compute_status_aggregates

    matrix = lf.get("capabilities")
    entitlements: dict[str, Any] = {}
    if matrix is not None:
        entitlements = {
            "tier": matrix.tier,
            "unavailable_tools": sorted(
                t for t in _capability_gated_tools() if _tool_blocked(t, matrix)
            ),
        }
    return {
        "portal_id": lf["portal_id"],
        "entitlements": entitlements,
        "activity": compute_status_aggregates(lf["portal_id"], window_hours=window_hours),
    }


def _capability_gated_tools() -> list[str]:
    from hubspot_mcp.capabilities import tool_capability_requirements

    return list(tool_capability_requirements())


def _tool_blocked(tool_name: str, matrix) -> bool:
    from hubspot_mcp.capabilities import missing_capabilities_for_tool

    return bool(missing_capabilities_for_tool(tool_name, matrix))


async def hubspot_route(ctx: Context, request_text: str) -> Any:
    """Route a natural-language HubSpot request to the specialist charter(s) that handle it.

    Returns the matching agent key(s) plus the prompt name and tool list for
    each, so a client can fetch the charter via ``prompts/get`` and know which
    tools it may use. This is the server-side half of the routing decision --
    upstream exposes it as a ``hubspot route`` CLI subcommand; the frozen
    ``{agents, rationale}`` contract is preserved so the shared routing corpus
    gates both.
    """
    lf = await _safety_ctx(ctx)
    from hubspot_mcp.agent_routing import route_request

    if not request_text:
        return {"agents": [], "rationale": "empty request; no agents routed", "charters": []}

    agents = route_request(request_text, portal_id=lf.get("portal_id"))
    if not agents:
        rationale = "no keyword match; no agents routed"
    elif len(agents) == 1:
        rationale = f"keyword routing selected agent: {agents[0]}"
    else:
        rationale = (
            f"keyword routing selected {len(agents)} agents as sorted candidates: "
            + ", ".join(agents)
        )

    from hubspot_mcp.agents import _AGENT_REGISTRY

    charters = []
    for key in agents:
        builder = _AGENT_REGISTRY.get(key)
        if builder is None:
            continue
        built = builder(lf.get("portal_config"))
        charters.append(
            {
                "agent": key,
                "prompt": f"hubspot_{key}",
                "domain": built.domain_description,
                "tools": built.tool_names,
            }
        )
    return {"agents": agents, "rationale": rationale, "charters": charters}


def _safety_tool_registrations() -> list[tuple[str, Any, str]]:
    return [
        (fn.__name__, fn, (fn.__doc__ or "").strip().split("\n")[0])
        for fn in (
            hubspot_approve_write,
            hubspot_reject_write,
            hubspot_list_pending_writes,
            hubspot_list_recent_audit,
            hubspot_undo_write,
            hubspot_status,
            hubspot_route,
        )
    ]



# --- Agent charters as MCP prompts -------------------------------------------
#
# hubspot-claude ships 44 "agents" that are prompt BUILDERS, not orchestration:
# each returns an AgentPrompt(agent_name, system_prompt, tool_names,
# domain_description) assembled from shared blocks plus a per-domain tool list.
# That is an MCP prompt almost exactly, which resolves Task 10 of the Phase 1
# build plan without converting anything to Claude Code sub-agent markdown --
# and it works for every MCP client, not just Claude Code.
#
# Charters are portal-sensitive: _build_domain appends the portal's custom
# object types from SchemaCache, so prompts/list is cacheScope "private" for the
# same reason tools/list is.


def _make_agent_prompt(agent_key: str):
    """Build the prompt handler for one agent charter."""

    async def charter(ctx: Context) -> str:
        from hubspot_mcp.agents import _AGENT_REGISTRY

        lf = await _session(ctx)
        if lf.get("auth_error"):
            raise ToolError(lf["auth_error"])
        builder = _AGENT_REGISTRY[agent_key]
        # The builder takes the portal so it can name custom object types; it
        # tolerates None, which is what an unauthenticated portal yields.
        return builder(lf.get("portal_config")).system_prompt

    charter.__name__ = f"hubspot_{agent_key}"
    charter.__annotations__ = {"ctx": Context, "return": str}
    return charter


def _register_agent_prompts() -> None:
    from mcp.server.mcpserver.prompts import Prompt

    from hubspot_mcp.agents import _AGENT_REGISTRY

    for agent_key in sorted(_AGENT_REGISTRY):
        fn = _make_agent_prompt(agent_key)
        description = (
            f"Operating charter for the {agent_key.replace('_', ' ')} domain: "
            "scope, its HubSpot tools, self-correction and write-verification rules."
        )
        mcp.add_prompt(
            Prompt.from_function(
                fn,
                name=f"hubspot_{agent_key}",
                description=description,
                context_kwarg="ctx",
            )
        )


def _register_all_tools() -> None:
    """Register all 81 tools in one name-sorted pass.

    2026-07-28 SHOULDs a deterministic ``tools/list`` order so clients can cache
    the listing and LLM prompt caches stay warm. Domain and safety tools must be
    sorted *together* — sorting each group separately still yields two
    concatenated runs, which is stable but not sorted. Registry order otherwise
    follows pkgutil module-walk order, which is incidental.
    """
    for name, fn, description in sorted(
        _domain_tool_registrations() + _safety_tool_registrations(), key=lambda r: r[0]
    ):
        mcp.add_tool(fn, name=name, description=description)


_register_all_tools()
_register_agent_prompts()


@mcp.custom_route("/connect/hubspot", methods=["GET"])
async def connect_hubspot(request: Any) -> Any:
    """Redeem a connect ticket and send the browser to HubSpot's consent screen.

    Public by necessity — a browser carries no MCP token. The ticket in the query
    string is the credential, which is why it is single-use and short-lived.
    """
    from starlette.responses import RedirectResponse

    from hubspot_mcp.auth.connect import ConnectError, ConnectFlow

    try:
        authorize_url = await ConnectFlow.from_env().begin(request.query_params.get("ticket", ""))
    except ConnectError as exc:
        return _connect_page("Could not start the connection", str(exc), ok=False)
    return RedirectResponse(authorize_url, status_code=302)


@mcp.custom_route("/connect/hubspot/callback", methods=["GET"])
async def connect_hubspot_callback(request: Any) -> Any:
    """Exchange HubSpot's authorization code and store the user's connection."""
    from hubspot_mcp.auth.connect import ConnectError, ConnectFlow

    params = request.query_params
    if params.get("error"):
        # HubSpot's own refusal (user declined, app misconfigured). Its text is
        # attacker-influencable, so it is escaped like everything else.
        return _connect_page(
            "HubSpot did not complete the connection",
            params.get("error_description") or params.get("error"),
            ok=False,
        )
    try:
        connection = await ConnectFlow.from_env().complete(
            params.get("code", ""), params.get("state", "")
        )
    except ConnectError as exc:
        return _connect_page("Could not complete the connection", str(exc), ok=False)
    except Exception as exc:  # noqa: BLE001 — a stack trace in a browser helps nobody
        print(f"hubspot_mcp: connect callback failed: {exc}", file=sys.stderr)
        return _connect_page(
            "Could not complete the connection",
            "Something went wrong exchanging the authorisation with HubSpot. Try again.",
            ok=False,
        )
    return _connect_page(
        "HubSpot connected",
        f"Portal {connection.portal_id} is now connected. You can close this tab "
        "and return to your assistant.",
        ok=True,
    )


def _connect_page(heading: str, detail: str, *, ok: bool) -> Any:
    """Render the browser-facing result of a connect attempt.

    Everything interpolated is escaped: `detail` can carry HubSpot's error text,
    which is outside our control.
    """
    import html

    from starlette.responses import HTMLResponse

    return HTMLResponse(
        "<!doctype html><meta charset=utf-8>"
        "<title>HubSpot connection</title>"
        "<body style=\"font:16px system-ui;max-width:34rem;margin:4rem auto;padding:0 1rem\">"
        f"<h1 style=\"font-size:1.25rem\">{html.escape(heading)}</h1>"
        f"<p>{html.escape(detail)}</p></body>",
        status_code=200 if ok else 400,
    )


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Any) -> Any:
    """Liveness probe. Public by design — reports nothing about the portal.

    Deliberately does not touch HubSpot or the state store: a health check that
    depends on a third party takes the deployment down when that third party
    has a bad minute.
    """
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok", "version": __version__})


def build_http_app(host: str = "127.0.0.1") -> Any:
    """Return the Streamable HTTP ASGI app, guarded by per-request bearer auth.

    This is also the Vercel entrypoint: the platform imports the ASGI app rather
    than calling :func:`run`, so the auth wrapper has to live here and not in
    the uvicorn branch below, or the hosted deployment would serve unguarded.
    """
    from hubspot_mcp.auth.bearer_middleware import BearerAuthMiddleware, resolve_server_secret
    from hubspot_mcp.tenancy import (
        enforce_durable_state,
        enforce_no_ambient_portal,
        enforce_single_tenant,
    )

    # Checks run before the app is built, so a misconfigured deployment fails at
    # startup rather than on its first request.
    if _TOKEN_VERIFIER is None:
        # Single-portal deployment: refuse to serve an ambiguous configuration.
        portal_id, source = _resolve_portal_source()
        enforce_single_tenant(host, portal_id, source)
    else:
        # Hosted: serving many portals is the point, so the single-tenant guard
        # inverts. The portal is a property of the caller's token, and
        # `hosted_session` resolves it per request — nothing may fall back to a
        # process-wide portal, which would be somebody else's CRM.
        enforce_no_ambient_portal()
        enforce_durable_state()

    app = mcp.streamable_http_app(streamable_http_path=MCP_PATH, host=host)

    if _TOKEN_VERIFIER is not None:
        # Per-request OAuth replaces the shared secret rather than stacking with
        # it. The SDK's bearer backend already rejects unauthenticated requests
        # and publishes the protected-resource metadata that tells a client
        # where to authenticate; wrapping that in a second, shared credential
        # would mean every user needed a secret nobody should be sharing.
        return app

    secret = resolve_server_secret(host)
    if secret is None:
        return app
    return BearerAuthMiddleware(app, secret=secret)


def run(transport: str = "stdio", *, host: str | None = None, port: int | None = None) -> None:
    """Run the MCP server over ``stdio`` (default) or Streamable HTTP.

    ``"http"`` is accepted as an alias for the spec's ``"streamable-http"``.
    The legacy HTTP+SSE transport is deprecated as of 2026-07-28 and is not
    offered here.
    """
    if transport in ("http", "streamable-http"):
        import uvicorn

        bind = host or "127.0.0.1"
        # Not ``mcp.run(transport="streamable-http")``: that builds and serves
        # the app in one call, leaving no seam to wrap it in auth.
        uvicorn.run(
            build_http_app(bind),
            host=bind,
            port=port or 8000,
            log_level=mcp.settings.log_level.lower(),
        )
    else:
        mcp.run(transport="stdio")