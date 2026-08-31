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
import typing
from contextlib import asynccontextmanager
from typing import Any

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


def _resolve_portal_id() -> str | None:
    if _SERVER_CONFIG["portal_id"]:
        return _SERVER_CONFIG["portal_id"]
    env = os.getenv("HUBSPOT_PORTAL")
    if env:
        return env
    return detect_default_portal(os.getcwd())


def _make_provider(mode: str) -> TokenProvider:
    return OAuthProvider() if mode == "oauth" else EnvTokenProvider()


@asynccontextmanager
async def app_lifespan(server: MCPServer):
    portal_id = _resolve_portal_id()
    provider = _make_provider(_SERVER_CONFIG["mode"])

    client: HubSpotClient | None = None
    cache: SchemaCache | None = None
    portal_config: PortalConfig | None = None
    auth_error: str | None = None

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
        except NotAuthenticatedError as exc:
            auth_error = str(exc)
        except Exception as exc:  # noqa: BLE001 — surface any init failure as guidance
            auth_error = f"Failed to initialize HubSpot client for portal {portal_id}: {exc}"

    try:
        yield {
            "client": client,
            "cache": cache,
            "portal_config": portal_config,
            "portal_id": portal_id,
            "auth_error": auth_error,
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
mcp = MCPServer(
    "hubspot-mcp",
    version=__version__,
    lifespan=app_lifespan,
    cache_hints={
        "tools/list": CacheHint(ttl_ms=300_000, scope="private"),
        "server/discover": CacheHint(ttl_ms=300_000, scope="private"),
    },
)


def _lifespan(ctx: Context) -> dict[str, Any]:
    """Return the lifespan-context dict."""
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


def _make_domain_wrapper(tool_def: ToolDef):
    """Build an async MCP wrapper that delegates one tool to ``handle_tool``."""
    domain_params = _domain_params(tool_def.func)
    new_params = [
        inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context),
        *domain_params,
    ]
    name = tool_def.name

    async def wrapper(ctx: Context, **kwargs: Any) -> Any:
        lf = _lifespan(ctx)
        if lf.get("auth_error"):
            raise ToolError(lf["auth_error"])
        try:
            result = await handle_tool(
                lf["client"], lf["cache"], lf["portal_config"], {"tool_name": name, "input": kwargs}
            )
        except HandlerError as exc:
            raise ToolError(exc.error["message"]) from exc
        return _raise_if_error(result["data"])

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
    lf = _lifespan(ctx)
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
    from hubspot_mcp.persistence import list_pending

    return {"pending": list_pending(lf["portal_id"])}


async def hubspot_list_recent_audit(ctx: Context, limit: int = 20) -> Any:
    """List recent audit-log entries for the active portal."""
    lf = await _safety_ctx(ctx)
    from hubspot_mcp import audit

    return {"audit": audit.get_recent_audits(lf["portal_id"], limit=limit)}


async def hubspot_undo_write(ctx: Context, action_id: str) -> Any:
    """Undo a previously approved write using its saved snapshot (best-effort).

    Updates restore ``original_values`` via ``hubspot_update_object``; creates
    delete the recorded ``created_ids`` via ``hubspot_delete_object``; deletes
    are not undoable. Uses the warm lifespan client. Ports the plugin's
    ``cli._undo_action`` logic (the orchestrator/CLI path was not ported).
    """
    lf = await _safety_ctx(ctx)
    from hubspot_mcp import audit
    from hubspot_mcp.snapshot import delete_undo_snapshot, load_undo_snapshot, snapshot_dir_for_portal
    from hubspot_mcp.tools import invoke_tool

    portal_id = lf["portal_id"]
    snap_dir = snapshot_dir_for_portal(portal_id)
    snapshot = load_undo_snapshot(snap_dir, action_id)
    if snapshot is None:
        raise ToolError(f"No undo snapshot for action {action_id}.")

    metadata = snapshot.get("metadata", {})
    intent_type = metadata.get("intent_type")
    object_type = metadata.get("target_object")
    client = lf["client"]

    if intent_type == "delete":
        raise ToolError("Deletes are not undoable through HubSpot.")
    if not metadata.get("undoable", False):
        raise ToolError("This action is not marked undoable.")

    try:
        if intent_type == "update":
            original_values = snapshot.get("original_values", {})
            if not original_values:
                raise ToolError("No original values recorded; cannot undo update.")
            for object_id, properties in original_values.items():
                await invoke_tool(
                    "hubspot_update_object", portal_id,
                    object_id=str(object_id), object_type=str(object_type), properties=properties, client=client,
                )
            outcome = f"Restored {len(original_values)} {object_type or 'record(s)'} to original values."
        elif intent_type == "create":
            created_ids = metadata.get("created_ids", [])
            if not created_ids:
                raise ToolError("No created IDs recorded; cannot undo create.")
            for object_id in created_ids:
                await invoke_tool(
                    "hubspot_delete_object", portal_id,
                    object_id=str(object_id), object_type=str(object_type), client=client,
                )
            outcome = f"Deleted {len(created_ids)} created {object_type or 'record(s)'} to undo the create."
        else:
            raise ToolError(f"Unknown action type {intent_type!r}; cannot undo.")
    except Exception as exc:  # noqa: BLE001 — undo is best-effort; surface structured error
        raise ToolError(str(exc))

    delete_undo_snapshot(snap_dir, action_id)
    try:
        audit.log_write(
            portal_id=portal_id, action=f"undo:{action_id}",
            agent=intent_type or "unknown", result_summary={"message": outcome},
        )
    except Exception:  # noqa: BLE001 — audit failure must not mask a successful undo
        pass
    return {"undo": action_id, "result": outcome}


def _safety_tool_registrations() -> list[tuple[str, Any, str]]:
    return [
        (fn.__name__, fn, (fn.__doc__ or "").strip().split("\n")[0])
        for fn in (
            hubspot_approve_write,
            hubspot_reject_write,
            hubspot_list_pending_writes,
            hubspot_list_recent_audit,
            hubspot_undo_write,
        )
    ]


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


def run(transport: str = "stdio", *, host: str | None = None, port: int | None = None) -> None:
    """Run the MCP server over ``stdio`` (default) or Streamable HTTP.

    ``"http"`` is accepted as an alias for the spec's ``"streamable-http"``.
    The legacy HTTP+SSE transport is deprecated as of 2026-07-28 and is not
    offered here.
    """
    if transport in ("http", "streamable-http"):
        mcp.run(transport="streamable-http", host=host or "127.0.0.1", port=port or 8000)
    else:
        mcp.run(transport="stdio")