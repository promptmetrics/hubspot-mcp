"""File-backed :class:`StateStore` — Phase 1 default.

Thin adapter over the ported ``persistence``, ``snapshot``, and ``audit``
modules, presenting their filesystem operations as the ``portal_id``-based
:class:`StateStore` interface. The translation that matters is for snapshots:
the ported ``snapshot`` module takes a ``snapshot_dir`` argument, so this
adapter resolves it via :func:`snapshot_dir_for_portal` and hides it from
callers.

This is the seam for Phase 2: swapping in ``RedisStateStore`` means
implementing the same methods against Redis, with no handler or tool-body
changes (they depend on :class:`StateStore`).
"""
from __future__ import annotations

from typing import Any

from hubspot_mcp import audit, persistence, snapshot
from hubspot_mcp.state.base import StateStore


class FileStateStore(StateStore):
    """Portal-keyed file-backed state store (delegates to ported modules)."""

    # --- pending previews -------------------------------------------------

    def store_pending(self, portal_id: str, action_id: str, data: dict[str, Any]) -> None:
        persistence.store(portal_id, action_id, data)

    def load_pending(self, portal_id: str, action_id: str) -> dict[str, Any] | None:
        return persistence.load(portal_id, action_id)

    def confirm_pending(self, portal_id: str, action_id: str, count: int) -> bool:
        return persistence.confirm(portal_id, action_id, count)

    def clear_pending(self, portal_id: str, action_id: str) -> None:
        persistence.clear(portal_id, action_id)

    def list_pending(self, portal_id: str) -> list[str]:
        # ``persistence`` is filesystem-shaped and returns paths; the action id
        # is the filename stem.  Ordering (newest first) is preserved.
        return [p.stem for p in persistence.list_pending(portal_id)]

    # --- undo snapshots ---------------------------------------------------

    def save_undo_snapshot_for_action(self, portal_id: str, action_id: str, preview_data: dict[str, Any]) -> None:
        snapshot.save_undo_snapshot_for_action(portal_id, action_id, preview_data)

    def save_undo_snapshot(
        self,
        portal_id: str,
        action_id: str,
        original_values: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        snapshot.save_undo_snapshot(
            snapshot.snapshot_dir_for_portal(portal_id), action_id, original_values, metadata=metadata
        )

    def load_undo_snapshot(self, portal_id: str, action_id: str) -> dict[str, Any] | None:
        return snapshot.load_undo_snapshot(snapshot.snapshot_dir_for_portal(portal_id), action_id)

    def update_undo_snapshot(self, portal_id: str, action_id: str, *, metadata: dict[str, Any] | None = None) -> None:
        snapshot.update_undo_snapshot(snapshot.snapshot_dir_for_portal(portal_id), action_id, metadata=metadata)

    def delete_undo_snapshot(self, portal_id: str, action_id: str) -> None:
        snapshot.delete_undo_snapshot(snapshot.snapshot_dir_for_portal(portal_id), action_id)

    # --- audit log --------------------------------------------------------

    def log_write(self, portal_id: str, *, action: str, agent: str, result_summary: dict[str, Any], informing_sources: list[dict[str, Any]] | None = None) -> None:
        audit.log_write(
            portal_id=portal_id,
            action=action,
            agent=agent,
            result_summary=result_summary,
            informing_sources=informing_sources,
        )

    def get_recent_audits(self, portal_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return audit.get_recent_audits(portal_id, limit=limit)