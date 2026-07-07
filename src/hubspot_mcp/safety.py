"""Shared write-safety path: scope validation + preview + pending-action persistence.

Both ``dispatch_agent`` (agent path) and the ``hubspot tool`` dispatcher (tool
path) route writes through :func:`apply_write` so that scope validation, preview
generation, and the persisted pending-preview record stay in lockstep.  The
preview *text* rendering and ``AgentResult`` construction remain in
``orchestrator.dispatch_agent`` — this module only owns the safety gate and the
on-disk pending record.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from hubspot_mcp.models import BatchApprovalMode
from hubspot_mcp.validation import format_scope_error, validate_scopes


def classify_url(url: str) -> tuple[str, str]:
    """Local stub for the not-ported ``research.classify_url``.

    The tools-only MCP server (approach C) never passes ``informing_sources``
    from a sub-agent — ``_build_tool_preview`` always sets it to ``[]`` — so
    :func:`normalize_informing_sources` returns before this is called. The stub
    keeps the module import-clean and returns a safe default if a non-empty
    source list ever reaches it.
    """
    return ("unknown", "community-unverified")


class ScopeBlocked(Exception):
    """Raised by :func:`apply_write` when the portal lacks a required scope.

    ``blocked`` carries the structured scope-violation dict produced by
    ``validation.validate_scopes`` so the caller can render it via
    :func:`format_scope_error` exactly as the inline check did.
    """

    def __init__(self, blocked: dict[str, Any]) -> None:
        super().__init__(format_scope_error(blocked))
        self.blocked = blocked


def normalize_informing_sources(
    sources: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Validate and correct trust-tier assignments from sub-agents.

    Sub-agents are expected to assign trust_tier using richer context
    (accepted-answer flag, employee badge, post age). This function
    catches obvious URL-vs-tier mismatches so the orchestrator can
    override misreported tiers before they reach the user or audit log.
    """
    if not sources:
        return []
    normalized: list[dict[str, Any]] = []
    for src in sources:
        url = src.get("url", "")
        inferred_source, inferred_tier = classify_url(url)
        reported_tier = src.get("trust_tier", inferred_tier)
        # If the reported tier contradicts what the URL alone supports,
        # downgrade to the safest tier the URL can justify.
        if inferred_source == "official" and reported_tier != "official":
            # URL is official but agent said otherwise — fix it
            corrected_tier = "official"
        elif inferred_source == "community" and reported_tier == "official":
            # Agent claimed official but URL is community — downgrade
            corrected_tier = "community-unverified"
        else:
            corrected_tier = reported_tier
        normalized.append(
            {
                "source": inferred_source,
                "trust_tier": corrected_tier,
                "title": src.get("title", ""),
                "url": url,
                "last_updated": src.get("last_updated"),
            }
        )
    return normalized


def check_write_scope(portal_config, agent_name: str, intent) -> dict[str, Any] | None:
    """Return the scope-violation dict for an agent write, or ``None`` if allowed.

    Skipped when no scope list is recorded on the portal (matches the original
    inline guard in ``dispatch_agent``).  The returned dict is the exact output
    of ``validation.validate_scopes``.
    """
    if portal_config.scopes_granted:
        return validate_scopes(
            [agent_name], portal_config.scopes_granted, target_object=intent.target_object
        )
    return None


@dataclass
class ApplyWriteResult:
    """Return value of :func:`apply_write`."""

    preview: Any
    action_id: str
    normalized_sources: list[dict[str, Any]]
    preview_data: dict[str, Any]


async def apply_write(
    *,
    client,
    portal_config,
    preview_builder: Callable[[Any], Awaitable[Any]],
    agent_name: str | None = None,
    tool_name: str | None = None,
    intent=None,
    request_text: str = "",
    trace_id: str | None = None,
    batch_mode: BatchApprovalMode = BatchApprovalMode.SINGLE,
    proposed_payload: dict[str, Any] | None = None,
) -> ApplyWriteResult:
    """Run the shared write-safety path and persist a pending preview.

    Steps (verbatim from ``dispatch_agent``'s preview branch):
    (a) scope validation via ``validate_scopes`` (raises :class:`ScopeBlocked`);
    (b) ``preview_builder(client)`` to build the preview;
    (c) mint ``action_id``, normalize informing sources, assemble ``preview_data``;
    (d) persist via ``persistence.store``.

    The caller owns the client lifecycle and the preview-text / AgentResult
    rendering.  ``agent_name`` is required for the agent-path scope check.
    """
    if agent_name is not None and intent is not None:
        blocked = check_write_scope(portal_config, agent_name, intent)
        if blocked:
            raise ScopeBlocked(blocked)

    preview = await preview_builder(client)

    action_id = str(uuid.uuid4())[:8]
    normalized_sources = normalize_informing_sources(preview.informing_sources)
    preview_data = {
        "agent_name": agent_name,
        "tool_name": tool_name,
        "request_text": request_text,
        "intent": intent.model_dump(mode="json") if intent is not None else {},
        "preview": preview.model_dump(mode="json"),
        "trace_id": trace_id,
        "batch_mode": batch_mode.value,
        "proposed_payload": proposed_payload or {},
        "informing_sources": normalized_sources,
        "required_confirmation": preview.impact_count,
        "confirmed_count": None,
    }
    # Persist the pending preview. The original plugin resolved this lazily via
    # ``orchestrator._store_pending_preview`` (a re-export of ``persistence.store``);
    # the tools-only MCP server ports ``persistence`` directly and drops the
    # orchestrator module, so we import ``store`` straight from persistence.
    from hubspot_mcp.persistence import store as _store_pending_preview

    # Offload the blocking flock+fsync to a worker thread so concurrent daemon
    # RPCs don't stall the event loop (#6).  The CLI sync path wraps this in
    # _run_async and is unaffected; the flock still serializes cross-process
    # writes — it just no longer runs on the asyncio loop.
    await asyncio.to_thread(_store_pending_preview, portal_config.portal_id, action_id, preview_data)

    return ApplyWriteResult(
        preview=preview,
        action_id=action_id,
        normalized_sources=normalized_sources,
        preview_data=preview_data,
    )