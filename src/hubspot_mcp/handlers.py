"""Async handlers for the HubSpot MCP server's tool + safety dispatch.

Each handler takes ``(client, cache, portal_config, params)`` and returns a
result dict ``{"ok": True, "data": ...}`` or raises :class:`HandlerError`
carrying an error shape ``{"kind", "message", "retryable", "guidance?"}``.

This is a tools-only port (approach C) of the plugin's handler set: the loop
handlers and the agent-execution branch (which depended on the not-ported
orchestrator) have been removed. ``handle_tool`` (read → invoke, write →
preview), ``handle_approve`` (execute pending), and ``handle_reject`` remain.

Handlers never own the client lifecycle — the caller (the FastMCP lifespan
pool, or a fresh ``build_fresh_client_cache``) does.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from hubspot_mcp import audit
from hubspot_mcp.cache import SchemaCache, ensure_custom_schema_cached, warm_standard_schemas
from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.models import BatchApprovalMode, RiskLevel, TaskIntent
from hubspot_mcp.persistence import clear as _clear_pending
from hubspot_mcp.persistence import confirm as _confirm_pending
from hubspot_mcp.persistence import load as _load_pending
from hubspot_mcp.safety import apply_write
from hubspot_mcp.scope_registry import get_required_scopes
from hubspot_mcp.snapshot import (
    delete_undo_snapshot,
    save_undo_snapshot_for_action,
    snapshot_dir_for_portal,
    update_undo_snapshot,
)
from hubspot_mcp.tools import invoke_tool

_WRITE_SCOPE_SUFFIXES = (".write", ".delete")


class HandlerError(Exception):
    """Raised by a handler to produce an NFR-15 error response."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
        guidance: str | None = None,
    ) -> None:
        super().__init__(message)
        payload: dict[str, Any] = {"kind": kind, "message": message, "retryable": retryable}
        if retry_after is not None:
            payload["retry_after"] = retry_after
        if guidance is not None:
            payload["guidance"] = guidance
        self.error = payload


def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _is_write_tool(required_scopes: set[str]) -> bool:
    return any(s.endswith(suffix) for s in required_scopes for suffix in _WRITE_SCOPE_SUFFIXES)


def _tool_risk_level(required_scopes: set[str]) -> RiskLevel:
    if any(s.endswith(".delete") for s in required_scopes):
        return RiskLevel.DESTRUCTIVE
    return RiskLevel.MEDIUM


def _tool_intent_type(tool_name: str) -> str:
    if "delete" in tool_name:
        return "delete"
    if "create" in tool_name:
        return "create"
    if "update" in tool_name or "upsert" in tool_name or "bulk" in tool_name:
        return "update"
    if "merge" in tool_name:
        return "merge"
    return "write"


def _tool_impact_count(tool_name: str, tool_input: dict[str, Any]) -> int:
    for key in ("records", "inputs", "members", "object_ids", "ids"):
        val = tool_input.get(key)
        if isinstance(val, list):
            return len(val)
    return 1


def _tool_kwargs(tool_input: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in tool_input.items() if k not in ("client", "portal_id")}


async def _build_tool_preview(
    tool_name: str,
    tool_input: dict[str, Any],
    required_scopes: set[str],
    client,
    portal_id: str,
):
    from hubspot_mcp.models import PreviewResult

    risk = _tool_risk_level(required_scopes)
    original_values: dict[str, Any] = {}
    if tool_name in ("hubspot_update_object", "hubspot_delete_object"):
        object_id = tool_input.get("object_id")
        object_type = tool_input.get("object_type")
        if object_id and object_type:
            try:
                result = await invoke_tool(
                    "hubspot_get_object",
                    portal_id,
                    object_id=str(object_id),
                    object_type=str(object_type),
                    client=client,
                )
                if isinstance(result, dict) and not result.get("error") and "id" in result:
                    original_values = {str(result["id"]): result.get("properties", {})}
            except Exception:
                original_values = {}

    return PreviewResult(
        preview={"tool": tool_name, "input": tool_input, "message": f"Preview of {tool_name}"},
        impact_count=_tool_impact_count(tool_name, tool_input),
        risk_level=risk,
        original_values=original_values,
        informing_sources=[],
    )


def _check_tool_scope(tool_name: str, portal_config: PortalConfig, target_object: str | None) -> None:
    if not portal_config.scopes_granted:
        return
    required = get_required_scopes([tool_name], target_object=target_object)
    missing = sorted(required - set(portal_config.scopes_granted))
    if missing:
        raise HandlerError(
            "scope",
            f"Missing HubSpot OAuth scopes for tool {tool_name}: " + ", ".join(missing),
            retryable=False,
            guidance="Grant the missing HubSpot OAuth scopes and retry.",
        )


async def handle_tool(client, cache, portal_config: PortalConfig, params: dict[str, Any]) -> dict[str, Any]:
    """Read → ``invoke_tool`` JSON; write → ``apply_write`` preview (FR-5b, no agent)."""
    tool_name = params.get("tool_name")
    if not tool_name:
        raise HandlerError("validation", "Missing 'tool_name' in params.")
    from hubspot_mcp.tools import get_tool

    if get_tool(tool_name) is None:
        raise HandlerError("not_found", f"Unknown tool: {tool_name}")

    tool_input = params.get("input") or {}
    if not isinstance(tool_input, dict):
        raise HandlerError("validation", "'input' must be a JSON object.")
    target_object = tool_input.get("object_type") if isinstance(tool_input, dict) else None

    required_scopes = get_required_scopes([tool_name], target_object)
    _check_tool_scope(tool_name, portal_config, target_object)

    portal_id = portal_config.portal_id
    # Warm custom schemas for a custom target_object before the tool validates
    # it against the on-disk cache (FR-5b: tool path must work on a cold cache,
    # matching the agent path's initialize_session).  No-op for standard types.
    await ensure_custom_schema_cached(portal_config, target_object)

    if not _is_write_tool(required_scopes):
        result = await invoke_tool(tool_name, portal_id, client=client, **_tool_kwargs(tool_input))
        return _ok({"tool": tool_name, "result": result})

    risk = _tool_risk_level(required_scopes)
    intent = TaskIntent(
        intent_type=_tool_intent_type(tool_name),
        target_object=target_object,
        description=f"tool {tool_name}",
        risk_level=risk,
    )
    aw = await apply_write(
        client=client,
        portal_config=portal_config,
        preview_builder=lambda c: _build_tool_preview(tool_name, tool_input, required_scopes, c, portal_id),
        agent_name=None,
        tool_name=tool_name,
        intent=intent,
        request_text=f"tool {tool_name}",
        proposed_payload=tool_input,
        batch_mode=BatchApprovalMode(params.get("batch_mode", "single")),
    )
    return _ok(
        {
            "status": "preview",
            "tool": tool_name,
            "action_id": aw.action_id,
            "preview": aw.preview.preview,
            "risk_level": aw.preview.risk_level.value,
            "impact_count": aw.preview.impact_count,
            "original_values": aw.preview.original_values,
            "required_confirmation": aw.preview.impact_count,
        }
    )


def _is_destructive(preview_data: dict[str, Any]) -> bool:
    preview = preview_data.get("preview") or {}
    intent = preview_data.get("intent") or {}
    risk = preview.get("risk_level") or intent.get("risk_level")
    return risk == RiskLevel.DESTRUCTIVE.value


class ExecuteError(Exception):
    """Raised by :func:`execute_pending_write` for gate or execute failures.

    Translated to :class:`HandlerError` on the daemon path and to an error
    string on the CLI path, so the safety/execute logic lives in one place.
    """

    def __init__(self, kind: str, message: str, *, retryable: bool = False, guidance: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retryable = retryable
        self.guidance = guidance


@dataclass
class ExecuteResult:
    """Return value of :func:`execute_pending_write`."""

    status: str
    agent_name: str | None
    tool_name: str | None
    data: dict[str, Any]
    created_ids: list[str]
    # FR-17 audit logging runs AFTER the write has committed and pending has
    # been cleared, so a failure there cannot roll the write back.  We surface
    # it as a flag rather than raising — raising would invite a duplicate
    # re-approve of an already-applied write, and swallowing silently would
    # lose FR-17 visibility.  Callers can inspect this to warn/retry the audit.
    audit_failed: bool = False


async def execute_pending_write(
    portal_config: PortalConfig,
    action_id: str,
    *,
    confirm_count: int | None = None,
    client: HubSpotClient | None = None,
) -> ExecuteResult:
    """Execute a pending preview after the destructive-count gate (FR-19).

    One implementation of the approve→execute safety contract, shared by the
    daemon handler (warm ``client``) and the CLI (``client=None`` → a fresh
    ``HubSpotClient`` is built for the tool branch).  Captures an undo snapshot
    before the write (FR-17/FR-18) and writes an audit record after it (FR-17).
    A soft execution failure raises :class:`ExecuteError` and leaves the
    pending preview on disk so the caller can retry; the snapshot is dropped
    since nothing was changed to undo.
    """
    portal_id = portal_config.portal_id
    preview_data = _load_pending(portal_id, action_id)
    if preview_data is None:
        raise ExecuteError("not_found", f"No pending preview found with ID {action_id}.")

    required = preview_data.get("required_confirmation") or 0
    if _is_destructive(preview_data):
        already_confirmed = preview_data.get("confirmed_count") == required
        if confirm_count is None:
            if not already_confirmed:
                raise ExecuteError(
                    "validation",
                    "Destructive actions require an exact impact count.",
                    retryable=False,
                    guidance=f"Re-run as `approve {action_id} {required}` — the count must equal the impact ({required}).",
                )
        elif not _confirm_pending(portal_id, action_id, confirm_count):
            raise ExecuteError(
                "validation",
                f"Wrong confirmation count: {confirm_count} (impact is {required}).",
                retryable=False,
                guidance=f"Re-run as `approve {action_id} {required}` — the count must equal the impact ({required}).",
            )

    intent = preview_data.get("intent") or {}
    intent_type = intent.get("intent_type")
    snap_saved = False
    # The snapshot save itself can fail (disk full, permissions); translate
    # that into an ExecuteError so it never escapes raw and the caller sees a
    # structured "snapshot" failure rather than a traceback.  Nothing has been
    # written yet, so no cleanup is needed on this path.
    if intent_type in ("create", "update", "delete"):
        try:
            save_undo_snapshot_for_action(portal_id, action_id, preview_data)
        except Exception as exc:
            raise ExecuteError(
                "snapshot",
                f"Failed to save undo snapshot: {exc}",
                retryable=True,
            ) from exc
        snap_saved = True

    snap_dir = snapshot_dir_for_portal(portal_id)
    data: dict[str, Any] = {}
    created_ids: list[str] = []
    try:
        agent_name = preview_data.get("agent_name")
        if agent_name:
            # The tools-only MCP server (approach C) does not port the agent
            # orchestrator. A pending preview carrying an agent_name cannot be
            # executed here — reject rather than silently dropping it.
            raise ExecuteError(
                "validation",
                "Agent-path execution is not supported in the tools-only MCP server.",
                retryable=False,
            )
        tool_name = preview_data.get("tool_name")
        payload = preview_data.get("proposed_payload") or {}
        if not tool_name:
            raise ExecuteError("validation", "Pending preview has no tool name.")
        owns_client = client is None
        if owns_client:
            client = HubSpotClient(portal_config)
        try:
            executed = await invoke_tool(tool_name, portal_id, client=client, **_tool_kwargs(payload))
        finally:
            if owns_client:
                # A close() failure must never turn a successful write into
                # a failure (that would invite a duplicate re-approve) nor
                # mask a primary error from invoke_tool.  Log and swallow.
                try:
                    await client.close()
                except Exception as close_exc:  # noqa: BLE001 — see comment
                    print(f"hubspot_mcp: client.close() failed: {close_exc}", file=sys.stderr)
        if isinstance(executed, dict) and executed.get("error"):
            raise ExecuteError("server", str(executed["error"]), retryable=True)
        data = {"tool": tool_name, "status": "success", "data": executed}
    except ExecuteError:
        # Existing contract: drop the snapshot (nothing changed) and re-raise
        # WITHOUT clearing pending, so the caller can retry.  Guard the delete
        # so it cannot mask the original ExecuteError if it itself raises.
        if snap_saved:
            try:
                delete_undo_snapshot(snap_dir, action_id)
            except Exception as del_exc:
                print(f"hubspot_mcp: snapshot delete failed: {del_exc}", file=sys.stderr)
        raise
    except Exception as exc:
        # Any non-ExecuteError raise (httpx/network/auth, client construction,
        # update_undo_snapshot callers below, etc.) must not escape raw: drop
        # the snapshot if one was saved and surface a structured retryable
        # ExecuteError.  Pending is NOT cleared — the caller can retry.
        if snap_saved:
            try:
                delete_undo_snapshot(snap_dir, action_id)
            except Exception as del_exc:
                print(f"hubspot_mcp: snapshot delete failed: {del_exc}", file=sys.stderr)
        raise ExecuteError("server", str(exc), retryable=True) from exc

    if snap_saved and intent_type == "create":
        result_payload = data.get("data")
        inner = (
            result_payload.get("result")
            if isinstance(result_payload, dict) and isinstance(result_payload.get("result"), dict)
            else result_payload
        )
        if isinstance(inner, dict):
            # Assumption: HubSpot create responses carry a top-level "id"; a
            # missing id is treated as a loud warning (see below), not silent.
            created_id = inner.get("id")
            if created_id:
                # update_undo_snapshot persists created_ids for undo.  If it
                # raises, the write still succeeded — capture the id in-memory
                # so the ExecuteResult carries it even though the snapshot
                # metadata is stale.  The snapshot file itself remains so undo
                # is still attemptable for the original_values half.
                try:
                    update_undo_snapshot(
                        snap_dir,
                        action_id,
                        metadata={"created_ids": [str(created_id)]},
                    )
                except Exception as upd_exc:
                    print(
                        f"hubspot_mcp: undo snapshot metadata update failed: {upd_exc}",
                        file=sys.stderr,
                    )
                created_ids = [str(created_id)]
            else:
                # Create succeeded but the response carried no id.  The write
                # is already applied at HubSpot, so we must NOT raise a
                # retryable error (that would invite a duplicate re-approve and
                # a second create).  We also must NOT silently yield empty
                # created_ids — undo of a create is impossible without the id.
                # Chosen semantics: clear pending (write is done — re-running
                # would duplicate it), KEEP the snapshot (so the operator can
                # still inspect original_values / manually reconcile), and
                # surface the missing id loudly via stderr plus a non-empty
                # created_ids sentinel is NOT used.  The ExecuteResult returns
                # empty created_ids; callers that need undo must treat empty
                # created_ids on a create as a loud warning.  This trades a
                # broken undo for avoiding a duplicate write, which is the
                # lesser evil for an idempotency-sensitive create.
                print(
                    f"hubspot_mcp: create for action {action_id} succeeded but "
                    f"no created id found in response; undo is not possible. "
                    f"Snapshot retained for manual inspection.",
                    file=sys.stderr,
                )

    _clear_pending(portal_id, action_id)
    audit_failed = False
    try:
        audit.log_write(
            portal_id=portal_id,
            action=f"approve:{action_id}",
            agent=preview_data.get("agent_name") or preview_data.get("tool_name") or "tool",
            result_summary={"request": preview_data.get("request_text", ""), "status": "success"},
            informing_sources=preview_data.get("informing_sources"),
        )
    except Exception as audit_exc:
        # FR-17 audit runs after the write committed and pending was cleared,
        # so a failure here cannot be rolled back.  Log to stderr as the
        # minimum FR-17 fallback and surface via audit_failed so the caller
        # can retry the audit without re-running the write.
        print(f"hubspot_mcp: audit.log_write failed: {audit_exc}", file=sys.stderr)
        audit_failed = True
    return ExecuteResult(
        status="success",
        agent_name=preview_data.get("agent_name"),
        tool_name=preview_data.get("tool_name"),
        data=data,
        created_ids=created_ids,
        audit_failed=audit_failed,
    )


async def handle_approve(client, cache, portal_config: PortalConfig, params: dict[str, Any]) -> dict[str, Any]:
    """Execute a pending preview (FR-19 gate, FR-17/18 undo, FR-17 audit).

    Thin wrapper over :func:`execute_pending_write` (the single shared
    implementation of the approve→execute contract).  ``client`` is the warm
    lifespan client, passed through to the tool branch.
    """
    action_id = params.get("action_id")
    if not action_id:
        raise HandlerError("validation", "Missing 'action_id' in params.")
    confirm_count = params.get("confirm_count")
    if confirm_count is not None:
        try:
            confirm_count = int(confirm_count)
        except (TypeError, ValueError):
            raise HandlerError("validation", "'confirm_count' must be an integer.")
    try:
        result = await execute_pending_write(
            portal_config, action_id, confirm_count=confirm_count, client=client
        )
    except ExecuteError as exc:
        raise HandlerError(
            exc.kind, exc.message, retryable=exc.retryable, guidance=exc.guidance
        )
    return _ok({**result.data, "audit_failed": result.audit_failed})


async def handle_reject(client, cache, portal_config: PortalConfig, params: dict[str, Any]) -> dict[str, Any]:
    action_id = params.get("action_id")
    if not action_id:
        raise HandlerError("validation", "Missing 'action_id' in params.")
    portal_id = portal_config.portal_id
    preview_data = _load_pending(portal_id, action_id)
    if preview_data is None:
        raise HandlerError("not_found", f"No pending preview found with ID {action_id}.")
    who = preview_data.get("agent_name") or preview_data.get("tool_name") or "tool"
    _clear_pending(portal_id, action_id)
    return _ok({"rejected": action_id, "for": who})


# ---------------------------------------------------------------------------
# In-process fallback: build a fresh client + cache per call, then close.
# ---------------------------------------------------------------------------


async def build_fresh_client_cache(portal_config: PortalConfig) -> tuple[HubSpotClient, SchemaCache]:
    """Construct a fresh warm client + schema cache for the fallback path (FR-16).

    The caller is responsible for closing the client.
    """
    cache = await warm_standard_schemas(portal_config)
    client = HubSpotClient(portal_config)
    return client, cache


HANDLERS: dict[str, Any] = {
    "tool": handle_tool,
    "approve": handle_approve,
    "reject": handle_reject,
}