"""Async handlers for the HubSpot MCP server's tool + safety dispatch.

Each handler takes ``(client, cache, portal_config, params)`` and returns a
result dict ``{"ok": True, "data": ...}`` or raises :class:`HandlerError`
carrying an error shape ``{"kind", "message", "retryable", "guidance?"}``.

This is a tools-only port (approach C) of the plugin's handler set: the loop
handlers and the agent-execution branch (which depended on the not-ported
orchestrator) have been removed. ``handle_tool`` (read → invoke, write →
preview), ``handle_approve`` (execute pending), and ``handle_reject`` remain.

Handlers never own the client lifecycle — the caller (the server lifespan
pool, or a fresh ``build_fresh_client_cache``) does.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Any

from hubspot_mcp.cache import SchemaCache, ensure_custom_schema_cached, warm_standard_schemas
from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.models import BatchApprovalMode, RiskLevel, TaskIntent
from hubspot_mcp.policy import AUTO, FULL_GATE
from hubspot_mcp.safety import apply_write
from hubspot_mcp.scope_registry import (
    RAW_API_WRITE_METHODS,
    WRITE_TOOLS,
    get_required_scopes,
)
from hubspot_mcp.state import get_store
from hubspot_mcp.tools import invoke_tool
from hubspot_mcp.validation import filter_writable_properties

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


def _raw_api_method(tool_input: dict[str, Any] | None) -> str:
    return str((tool_input or {}).get("method", "")).upper()


def _is_write_tool(
    required_scopes: set[str],
    tool_name: str | None = None,
    tool_input: dict[str, Any] | None = None,
) -> bool:
    # ``hubspot_raw_api`` is a write only for mutating HTTP verbs; a GET is a
    # read. Classify by the request method so raw_api reads stay on the fast path
    # and raw_api writes hit the HITL gate.
    if tool_name == "hubspot_raw_api":
        return _raw_api_method(tool_input) in RAW_API_WRITE_METHODS
    # Scope-suffix covers crm.objects.*.write/.delete etc. Tools whose scope set
    # has no write/delete suffix (workflows' bare ``automation``, single-scope
    # ``forms``/``reports``, and set()-registered refund/import/export) need an
    # explicit name match against scope_registry.WRITE_TOOLS to hit the gate.
    if tool_name is not None and tool_name in WRITE_TOOLS:
        return True
    return any(s.endswith(suffix) for s in required_scopes for suffix in _WRITE_SCOPE_SUFFIXES)


def _tool_risk_level(
    required_scopes: set[str],
    tool_name: str | None = None,
    tool_input: dict[str, Any] | None = None,
) -> RiskLevel:
    # A raw_api DELETE is destructive even though its registry scope set is empty,
    # so the destructive-count gate must fire for it.
    if tool_name == "hubspot_raw_api" and _raw_api_method(tool_input) == "DELETE":
        return RiskLevel.DESTRUCTIVE
    if any(s.endswith(".delete") for s in required_scopes):
        return RiskLevel.DESTRUCTIVE
    return RiskLevel.MEDIUM


# Pattern approval is reversible-property-update-only: the two object-update
# tools qualify; creates/deletes/merges/workflow/side-effect tools never do.
_PATTERN_ELIGIBLE_TOOLS = frozenset({"hubspot_update_object", "hubspot_bulk_update_objects"})


def _pattern_eligibility(
    tool_name: str,
    tool_input: dict[str, Any],
    required_scopes: set[str],
    portal_id: str,
) -> tuple[bool, str]:
    """Decide once, up front, whether a ``--pattern`` request may use pattern mode.

    Pattern mode is allowed ONLY for reversible, non-sensitive property updates
    (spec §4).  Returns ``(eligible, reason)``; on reject the caller falls back to
    the normal per-op gate and surfaces ``reason``.  Rejects when the op is
    destructive (delete/merge), the tool is not a reversible object-update, or the
    proposed change touches any configured sensitive property.
    """
    from hubspot_mcp.policy import _iter_property_keys, load_approval_policy

    if _tool_risk_level(required_scopes, tool_name, tool_input) == RiskLevel.DESTRUCTIVE:
        return False, "destructive operations (delete/merge) are always individually gated"
    if tool_name not in _PATTERN_ELIGIBLE_TOOLS:
        return False, f"{tool_name} is not a reversible property update"
    policy = load_approval_policy(portal_id)
    touched = _iter_property_keys(tool_input)
    sensitive = sorted(touched & set(policy.sensitive_properties))
    if sensitive:
        return False, "the change touches sensitive field(s): " + ", ".join(sensitive)
    return True, ""


def _pattern_value_eq(a: Any, b: Any) -> bool:
    """Compare a re-read value against a captured pre-image value for
    compare-and-set.  HubSpot returns property values as strings; normalize both
    (None stays None) so ``"5"`` == ``5`` but a real drift is caught."""
    na = None if a is None else str(a)
    nb = None if b is None else str(b)
    return na == nb


def _tool_intent_type(tool_name: str, tool_input: dict[str, Any] | None = None) -> str:
    if tool_name == "hubspot_raw_api":
        method = _raw_api_method(tool_input)
        if method == "DELETE":
            return "delete"
        return "write"
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

    risk = _tool_risk_level(required_scopes, tool_name, tool_input)
    original_values: dict[str, Any] = {}
    # Count snapshot GETs attempted (vs. succeeded) so an UPDATE that captured
    # no originals — every per-record GET raised at preview time — can surface a
    # warning NOW, not at undo time.  Without this, the preview silently persists
    # a hollow snapshot and the operator only learns undo is unavailable after
    # approving (see snapshot.save_undo_snapshot_for_action's fail-closed flag).
    fetches_attempted = 0
    if tool_name in ("hubspot_update_object", "hubspot_delete_object"):
        object_id = tool_input.get("object_id")
        object_type = tool_input.get("object_type")
        # Bug B (0.2.4): scope an UPDATE's snapshot fetch to the properties
        # being changed — an unscoped GET returns HubSpot's default property
        # set including read-only system fields (hs_lastmodifieddate, ...),
        # which a later undo replays and HubSpot 400s on.  Deletes keep the
        # full fetch: their snapshot is a reconciliation artifact and wants
        # everything.
        changed_props: list[str] | None = None
        if tool_name == "hubspot_update_object":
            props = tool_input.get("properties")
            if isinstance(props, dict) and props:
                changed_props = list(props.keys())
        if object_id and object_type:
            try:
                fetches_attempted += 1
                result = await invoke_tool(
                    "hubspot_get_object",
                    portal_id,
                    object_id=str(object_id),
                    object_type=str(object_type),
                    client=client,
                    properties=changed_props,
                )
                if isinstance(result, dict) and not result.get("error") and "id" in result:
                    original_values = {str(result["id"]): result.get("properties", {})}
            except Exception:
                original_values = {}
    elif tool_name == "hubspot_merge_objects":
        # A merge absorbs the secondary record destructively and HubSpot has no
        # unmerge API, so capture BOTH records pre-merge — the snapshot is the
        # only artifact for manual reconciliation.
        object_type = tool_input.get("object_type", "contacts")
        for oid in (tool_input.get("primary_object_id"), tool_input.get("object_id_to_merge")):
            if not oid:
                continue
            try:
                result = await invoke_tool(
                    "hubspot_get_object",
                    portal_id,
                    object_id=str(oid),
                    object_type=str(object_type),
                    client=client,
                )
                if isinstance(result, dict) and not result.get("error") and "id" in result:
                    original_values[str(result["id"])] = result.get("properties", {})
            except Exception:
                continue
    elif tool_name == "hubspot_bulk_update_objects":
        # Bug 5b: bulk update is MEDIUM risk (``.write`` scope, not destructive),
        # so it never hit the destructive-count gate and the snapshot pre-fetch
        # above skipped it — a snapshot was saved with ``undoable=True`` but
        # empty ``original_values``, so ``undo`` failed with "No original values
        # recorded".  The records are enumerated in the payload anyway; fetch
        # each one's current values so the snapshot can restore all of them.
        object_type = tool_input.get("object_type")
        for rec in tool_input.get("records", []) or []:
            if not isinstance(rec, dict):
                continue
            object_id = rec.get("id")
            if not object_id or not object_type:
                continue
            # Scope each record's snapshot fetch to the properties being
            # changed (same rationale as the single-update branch above); the
            # handle_tool shape gate guarantees a non-empty ``properties``.
            rec_props = rec.get("properties")
            changed = list(rec_props.keys()) if isinstance(rec_props, dict) and rec_props else None
            try:
                fetches_attempted += 1
                result = await invoke_tool(
                    "hubspot_get_object",
                    portal_id,
                    object_id=str(object_id),
                    object_type=str(object_type),
                    client=client,
                    properties=changed,
                )
                if isinstance(result, dict) and not result.get("error") and "id" in result:
                    original_values[str(result["id"])] = result.get("properties", {})
            except Exception:
                continue

    preview: dict[str, Any] = {
        "tool": tool_name,
        "input": tool_input,
        "message": f"Preview of {tool_name}",
    }
    # An UPDATE that attempted snapshot fetches but captured nothing cannot be
    # undone — tell the operator at preview time so approval is informed.  CREATE
    # (and DELETE, whose snapshot is a reconciliation artifact) stay quiet: their
    # undo paths don't replay original_values.
    if (
        tool_name in ("hubspot_update_object", "hubspot_bulk_update_objects")
        and fetches_attempted
        and not original_values
    ):
        preview["warning"] = (
            "Unable to capture pre-change values for undo: every snapshot read failed. "
            "This action can be approved and executed, but it will NOT be undoable."
        )

    return PreviewResult(
        preview=preview,
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

    # No durable loop in the MCP server (approach C), so this is always None --
    # kept so the auto-apply and pattern branches read identically to upstream.
    loop_step_number = params.get("loop_step_number")
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

    if not _is_write_tool(required_scopes, tool_name, tool_input):
        result = await invoke_tool(tool_name, portal_id, client=client, **_tool_kwargs(tool_input))
        return _ok({"tool": tool_name, "result": result})

    risk = _tool_risk_level(required_scopes, tool_name, tool_input)
    intent = TaskIntent(
        intent_type=_tool_intent_type(tool_name, tool_input),
        target_object=target_object,
        description=f"tool {tool_name}",
        risk_level=risk,
    )
    batch_mode = BatchApprovalMode(params.get("batch_mode", "single"))
    # Pattern approval (§4): decide eligibility once, up front.  A --pattern
    # request that isn't a reversible, non-sensitive object-update falls back to
    # the normal per-op gate (batch_mode → single) with a surfaced reason.  Loop
    # steps never use pattern (it's an interactive flow — the loop pauses at
    # every write regardless).
    use_pattern = False
    pattern_threshold: int | None = None
    pattern_fallback: str | None = None
    if batch_mode == BatchApprovalMode.PATTERN and loop_step_number is None:
        eligible, reason = _pattern_eligibility(tool_name, tool_input, required_scopes, portal_id)
        if eligible:
            from hubspot_mcp.policy import load_approval_policy

            use_pattern = True
            pattern_threshold = load_approval_policy(portal_id).pattern_confirm_threshold
        else:
            batch_mode = BatchApprovalMode.SINGLE
            pattern_fallback = reason
    aw = await apply_write(
        client=client,
        portal_config=portal_config,
        preview_builder=lambda c: _build_tool_preview(tool_name, tool_input, required_scopes, c, portal_id),
        agent_name=None,
        tool_name=tool_name,
        intent=intent,
        request_text=f"tool {tool_name}",
        proposed_payload=tool_input,
        batch_mode=batch_mode,
        trace_id=params.get("trace_id"),
        loop_step_number=loop_step_number,
        pattern=use_pattern,
        pattern_confirm_threshold=pattern_threshold,
        filter_summary=str(tool_input.get("filter_summary", "")) if isinstance(tool_input, dict) else "",
    )
    tier = aw.preview_data.get("approval_tier")
    # Bounded Autonomy (Phase 2): a provably-safe interactive write auto-applies
    # (act-and-notify) — execute now, report the result + an undo command.
    # Loop-originated writes (loop_step_number set) NEVER auto-apply; the durable
    # loop still pauses at every write.
    if tier == AUTO and loop_step_number is None:
        try:
            result = await execute_pending_write(portal_config, aw.action_id, client=client)
        except ExecuteError as exc:
            # Auto-apply failed mid-execute (e.g. transient HubSpot 401/429). The
            # pending record is retained on disk in a completable state, so tell
            # the operator how to finish or discard it rather than leaking a
            # generic error and an invisible pending write.
            raise HandlerError(
                exc.kind,
                f"Auto-apply of {tool_name} failed: {exc.message}. The change is staged as "
                f"pending action {aw.action_id} — run `hubspot approve {aw.action_id}` to complete "
                f"it or `hubspot reject {aw.action_id}` to discard.",
                retryable=exc.retryable,
                guidance=exc.guidance,
            ) from exc
        applied = _applied_envelope(tool_name, aw, result)
        # A --pattern request that was rejected to the normal gate still surfaces
        # WHY, even when the fallback write auto-applied (act-and-notify).
        if pattern_fallback is not None:
            applied["pattern_fallback"] = pattern_fallback
        return _ok(applied)
    response = {
        "status": "preview",
        "tool": tool_name,
        "action_id": aw.action_id,
        "preview": aw.preview.preview,
        "risk_level": aw.preview.risk_level.value,
        "impact_count": aw.preview.impact_count,
        "original_values": aw.preview.original_values,
        "required_confirmation": aw.preview.impact_count,
        "approval_tier": tier,
        "requires_count": tier == FULL_GATE,
    }
    # Pattern mode surfaces ONE rule + matched count + a before/after sample
    # (first pattern_sample_size records), not N previews.  A fallback reason is
    # surfaced when a --pattern request was rejected to the normal gate.
    pat = aw.preview_data.get("pattern")
    if pat:
        response["pattern_eligible"] = True
        response["required_confirmation"] = aw.preview_data.get("required_confirmation", 0)
        response["pattern"] = {
            "rule": pat.get("rule"),
            "count": pat.get("count"),
            "sample": (pat.get("matched") or [])[: aw.preview.pattern_sample_size],
        }
    if pattern_fallback is not None:
        response["pattern_fallback"] = pattern_fallback
    return _ok(response)


def _applied_envelope(tool_name: str, aw, result: ExecuteResult) -> dict[str, Any]:
    """Envelope for an auto-applied (AUTO-tier) write: the executed result plus
    an undo affordance.  This is a *final result*, not step narration, so it is
    surfaced even under quiet/terse output."""
    n = aw.preview.impact_count
    obj = (aw.preview_data.get("intent") or {}).get("target_object") or "record(s)"
    payload = aw.preview.proposed_payload if isinstance(aw.preview.proposed_payload, dict) else {}
    props = payload.get("properties")
    p = len(props) if isinstance(props, dict) else None
    summary = f"✓ Applied: updated {n} record(s)"
    if p:
        summary += f", {p} propert{'y' if p == 1 else 'ies'}"
    summary += f" on {obj}. Undo: hubspot undo {aw.action_id}"
    return {
        "status": "applied",
        "tool": tool_name,
        "action_id": aw.action_id,
        "impact_count": n,
        "created_ids": result.created_ids,
        "audit_failed": result.audit_failed,
        "undo_command": f"hubspot undo {aw.action_id}",
        "message": summary,
        "result": result.data,
    }


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
    preview_data = get_store().load_pending(portal_id, action_id)
    if preview_data is None:
        raise ExecuteError("not_found", f"No pending preview found with ID {action_id}.")

    required = preview_data.get("required_confirmation") or 0
    # The exact-count gate is reserved for FULL_GATE-tier writes (destructive,
    # non-reversible, or sensitive-field under a full_gate policy).  CONFIRM-tier
    # writes approve count-free; AUTO never reaches here via approve.  Records
    # persisted before ``approval_tier`` existed fall back to the legacy rule
    # (destructive OR multi-record) so in-flight previews stay gated (Bug 5a).
    tier = preview_data.get("approval_tier")
    if tier is not None:
        needs_count = tier == FULL_GATE
    else:
        needs_count = _is_destructive(preview_data) or required > 1
    if needs_count:
        already_confirmed = preview_data.get("confirmed_count") == required
        if confirm_count is None:
            if not already_confirmed:
                raise ExecuteError(
                    "validation",
                    (
                        "Destructive actions require an exact impact count."
                        if _is_destructive(preview_data)
                        else f"Multi-record actions require an exact impact count ({required} records)."
                    ),
                    retryable=False,
                    guidance=f"Re-run as `approve {action_id} {required}` — the count must equal the impact ({required}).",
                )
        elif not await asyncio.to_thread(get_store().confirm_pending, portal_id, action_id, confirm_count):
            raise ExecuteError(
                "validation",
                f"Wrong confirmation count: {confirm_count} (impact is {required}).",
                retryable=False,
                guidance=f"Re-run as `approve {action_id} {required}` — the count must equal the impact ({required}).",
            )

    # Pattern approval: the approved rule scales here with per-record
    # compare-and-set.  Branch AFTER the count gate (so the over-threshold typed
    # count is enforced) and BEFORE the single-write snapshot below (the pattern
    # executor owns its own per-applied-record snapshot + audit + clear).
    if (
        preview_data.get("batch_mode") == BatchApprovalMode.PATTERN.value
        and preview_data.get("pattern")
    ):
        return await _execute_pattern_write(portal_config, action_id, preview_data, client=client)

    intent = preview_data.get("intent") or {}
    intent_type = intent.get("intent_type")
    snap_saved = False
    # The snapshot save itself can fail (disk full, permissions); translate
    # that into an ExecuteError so it never escapes raw and the caller sees a
    # structured "snapshot" failure rather than a traceback.  Nothing has been
    # written yet, so no cleanup is needed on this path.
    if intent_type in ("create", "update", "delete"):
        try:
            get_store().save_undo_snapshot_for_action(portal_id, action_id, preview_data)
        except Exception as exc:
            raise ExecuteError(
                "snapshot",
                f"Failed to save undo snapshot: {exc}",
                retryable=True,
            ) from exc
        snap_saved = True

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
            if owns_client and client is not None:
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
                get_store().delete_undo_snapshot(portal_id, action_id)
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
                get_store().delete_undo_snapshot(portal_id, action_id)
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
                    get_store().update_undo_snapshot(
                        portal_id,
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

    get_store().clear_pending(portal_id, action_id)
    audit_failed = False
    try:
        get_store().log_write(
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
        print(f"hubspot_mcp: audit log_write failed: {audit_exc}", file=sys.stderr)
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
        except (TypeError, ValueError) as exc:
            raise HandlerError("validation", "'confirm_count' must be an integer.") from exc
    try:
        result = await execute_pending_write(
            portal_config, action_id, confirm_count=confirm_count, client=client
        )
    except ExecuteError as exc:
        raise HandlerError(
            exc.kind, exc.message, retryable=exc.retryable, guidance=exc.guidance
        ) from exc
    return _ok({**result.data, "audit_failed": result.audit_failed})


async def handle_reject(client, cache, portal_config: PortalConfig, params: dict[str, Any]) -> dict[str, Any]:
    action_id = params.get("action_id")
    if not action_id:
        raise HandlerError("validation", "Missing 'action_id' in params.")
    portal_id = portal_config.portal_id
    preview_data = get_store().load_pending(portal_id, action_id)
    if preview_data is None:
        raise HandlerError("not_found", f"No pending preview found with ID {action_id}.")
    who = preview_data.get("agent_name") or preview_data.get("tool_name") or "tool"
    get_store().clear_pending(portal_id, action_id)
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


async def _execute_pattern_write(
    portal_config: PortalConfig,
    action_id: str,
    preview_data: dict[str, Any],
    *,
    client: HubSpotClient | None = None,
) -> ExecuteResult:
    """Scale an approved pattern rule with per-record compare-and-set (§3/§8).

    For each matched record: re-GET its current target-field values and apply the
    change ONLY IF they still equal the captured pre-image (optimistic
    concurrency).  Drift → skip (never overwrite); a re-read/write hard error →
    record and CONTINUE (a bad record never aborts the batch).  The applied set's
    pre-images are written to ONE batch undo snapshot (per-record entries; a
    single ``hubspot undo <id>`` restores exactly what changed) and each applied
    record gets its own audit entry.  Returns a continue-through report enumerating
    applied / skipped_drifted / failed so a partial is never hidden.
    """
    portal_id = portal_config.portal_id
    pattern = preview_data.get("pattern") or {}
    rule = pattern.get("rule") or {}
    object_type = rule.get("object_type")
    matched = pattern.get("matched") or []
    tool_name = preview_data.get("tool_name") or "hubspot_bulk_update_objects"

    applied: list[str] = []
    applied_originals: dict[str, Any] = {}
    skipped_drifted: list[str] = []
    failed: list[dict[str, Any]] = []

    owns_client = client is None
    if owns_client:
        client = HubSpotClient(portal_config)
    try:
        for entry in matched:
            rid = str(entry.get("id") or "")
            pre_image = entry.get("pre_image") or {}
            changes = entry.get("changes") or {}
            if not rid or not changes:
                failed.append({"id": rid, "error": "empty change set"})
                continue
            # (1) Re-read the current values of exactly the target fields.
            try:
                current = await invoke_tool(
                    "hubspot_get_object",
                    portal_id,
                    object_id=rid,
                    object_type=str(object_type),
                    client=client,
                    properties=list(changes.keys()),
                )
            except Exception as exc:  # noqa: BLE001 — continue-through, never abort
                failed.append({"id": rid, "error": f"re-read failed: {exc}"})
                continue
            if not isinstance(current, dict) or current.get("error"):
                err = current.get("error") if isinstance(current, dict) else current
                failed.append({"id": rid, "error": f"re-read failed: {err}"})
                continue
            _props = current.get("properties")
            cur_props = _props if isinstance(_props, dict) else {}
            # (2) Compare-and-set: apply ONLY if every target field still equals
            # the approved pre-image.  Any drift → skip, never overwrite.
            if any(not _pattern_value_eq(cur_props.get(k), pre_image.get(k)) for k in changes):
                skipped_drifted.append(rid)
                continue
            # (3) Apply this record's change.
            try:
                res = await invoke_tool(
                    "hubspot_update_object",
                    portal_id,
                    object_id=rid,
                    object_type=str(object_type),
                    properties=changes,
                    client=client,
                )
            except Exception as exc:  # noqa: BLE001 — continue-through, never abort
                failed.append({"id": rid, "error": f"write failed: {exc}"})
                continue
            if isinstance(res, dict) and res.get("error"):
                failed.append({"id": rid, "error": f"write failed: {res['error']}"})
                continue
            applied.append(rid)
            applied_originals[rid] = pre_image
    finally:
        if owns_client and client is not None:
            try:
                await client.close()
            except Exception as close_exc:  # noqa: BLE001 — never mask a write result
                print(f"hubspot_mcp: client.close() failed: {close_exc}", file=sys.stderr)

    # (4) One batch undo snapshot holding a per-record entry for each APPLIED
    # record only (drifted/failed excluded), so `hubspot undo <id>` restores
    # exactly the set that changed via the shared update-undo path.
    if applied_originals:
        try:
            get_store().save_undo_snapshot(
                portal_id,
                action_id,
                applied_originals,
                metadata={"intent_type": "update", "target_object": object_type, "undoable": True},
            )
        except Exception as snap_exc:  # noqa: BLE001 — write already committed
            print(f"hubspot_mcp: pattern batch snapshot failed: {snap_exc}", file=sys.stderr)

    await asyncio.to_thread(get_store().clear_pending, portal_id, action_id)

    # (5) Per-record audit entry for each applied record (FR-17; own audit entry).
    audit_failed = False
    for rid in applied:
        try:
            get_store().log_write(
                portal_id=portal_id,
                action=f"approve:{action_id}:{rid}",
                agent=tool_name,
                result_summary={
                    "request": preview_data.get("request_text", ""),
                    "status": "success",
                    "record_id": rid,
                    "pattern": True,
                },
                informing_sources=preview_data.get("informing_sources"),
            )
        except Exception as audit_exc:  # noqa: BLE001 — FR-17 fallback
            print(f"hubspot_mcp: pattern audit log_write failed for {rid}: {audit_exc}", file=sys.stderr)
            audit_failed = True

    report = {
        "applied": applied,
        "skipped_drifted": skipped_drifted,
        "failed": failed,
        "counts": {
            "matched": len(matched),
            "applied": len(applied),
            "skipped_drifted": len(skipped_drifted),
            "failed": len(failed),
        },
        "undo_command": f"hubspot undo {action_id}" if applied else None,
    }
    return ExecuteResult(
        status="success",
        agent_name=None,
        tool_name=tool_name,
        data={"tool": tool_name, "status": "success", "pattern_report": report},
        created_ids=[],
        audit_failed=audit_failed,
    )


async def undo_action(
    snapshot: dict[str, Any],
    portal_id: str,
    portal_config,
    *,
    client: HubSpotClient | None = None,
) -> tuple[bool, str]:
    """Attempt the undo; return ``(succeeded, message)``.

    Lives here rather than in the server layer so undo shares the module that
    owns every other write-safety decision.

    ``succeeded`` is False whenever nothing was changed in HubSpot — the caller
    must then keep the snapshot (it's the only reconciliation artifact).
    """
    metadata = snapshot.get("metadata", {})
    intent_type = metadata.get("intent_type")
    object_type = metadata.get("target_object")

    if intent_type == "delete":
        return False, "❌ Deletes are not undoable through HubSpot."

    if not metadata.get("undoable", False):
        return False, "❌ This action is not undoable."

    # Reuse the caller's warm client when given one (the MCP lifespan pool);
    # only close a client we created ourselves.
    owns_client = client is None
    if owns_client:
        client = HubSpotClient(portal_config)
    try:
        if intent_type == "update":
            original_values = snapshot.get("original_values", {})
            if not original_values:
                return False, "❌ No original values recorded; cannot undo update."
            # Bug B (0.2.4): the restore previously replayed the ENTIRE
            # snapshot dict (incl. read-only fields HubSpot 400s on) and
            # discarded the tool's error envelope, so a failed restore was
            # reported as "Restored".  Attempt every record (maximize
            # restoration), check each envelope, and fail closed: any failure
            # returns False so the caller keeps the snapshot.
            restored = 0
            failures: list[str] = []
            stripped_any: set[str] = set()
            for object_id, properties in original_values.items():
                writable, stripped = filter_writable_properties(
                    str(object_type), properties, portal_id
                )
                stripped_any.update(stripped)
                if not writable:
                    # Nothing writable to restore (e.g. a snapshot of only
                    # system fields) — a per-record no-op, not a failure.
                    restored += 1
                    continue
                result = await invoke_tool(
                    "hubspot_update_object",
                    portal_id,
                    object_id=str(object_id),
                    object_type=str(object_type),
                    properties=writable,
                    client=client,
                )
                if isinstance(result, dict) and result.get("error"):
                    failures.append(f"{object_id}: {result['error']}")
                else:
                    restored += 1
            note = (
                f" (skipped read-only: {', '.join(sorted(stripped_any))})" if stripped_any else ""
            )
            if failures:
                detail = "; ".join(failures)
                return False, (
                    f"❌ Restored {restored} of {len(original_values)} "
                    f"{object_type or 'record(s)'}; {len(failures)} failed — {detail}{note}"
                )
            return True, (
                f"✅ Restored {restored} {object_type or 'record(s)'} to their original values.{note}"
            )

        if intent_type == "create":
            created_ids = metadata.get("created_ids", [])
            if not created_ids:
                return False, "❌ No created IDs recorded; cannot undo create."
            deleted = 0
            failures = []
            for object_id in created_ids:
                result = await invoke_tool(
                    "hubspot_delete_object",
                    portal_id,
                    object_id=str(object_id),
                    object_type=str(object_type),
                    client=client,
                )
                if isinstance(result, dict) and result.get("error"):
                    error_text = str(result["error"])
                    # An already-deleted record means the undo goal is met for
                    # it — tolerate 404s so a retry after partial failure
                    # converges instead of failing forever.
                    if "404" in error_text or "not found" in error_text.lower():
                        deleted += 1
                    else:
                        failures.append(f"{object_id}: {error_text}")
                else:
                    deleted += 1
            if failures:
                detail = "; ".join(failures)
                return False, (
                    f"❌ Deleted {deleted} of {len(created_ids)} created "
                    f"{object_type or 'record(s)'}; {len(failures)} failed — {detail}"
                )
            return True, f"✅ Deleted {deleted} created {object_type or 'record(s)'} to undo the create."

        return False, "❌ Unknown action type; cannot undo."
    finally:
        if owns_client and client is not None:
            await client.close()


HANDLERS: dict[str, Any] = {
    "tool": handle_tool,
    "approve": handle_approve,
    "reject": handle_reject,
}