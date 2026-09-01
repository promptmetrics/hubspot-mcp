"""File-backed :class:`StateStore` — the local/stdio default.

Thin adapter over the ported ``persistence``, ``snapshot``, and ``audit``
modules, presenting their filesystem operations as the ``portal_id``-based
:class:`StateStore` interface. The translation that matters is for snapshots:
the ported ``snapshot`` module takes a ``snapshot_dir`` argument, so this
adapter resolves it via :func:`snapshot_dir_for_portal` and hides it from
callers.

Every method runs its filesystem work through :func:`asyncio.to_thread`. The
ported modules take a directory ``flock`` and ``fsync`` on write, which is
exactly the kind of blocking call that should not sit on the event loop while
concurrent tool calls are in flight — Phase 1 offloaded two of these by hand
and left the rest inline.
"""
from __future__ import annotations

import asyncio
from typing import Any

from hubspot_mcp import audit, persistence, snapshot
from hubspot_mcp.state.base import StateStore


class FileStateStore(StateStore):
    """Portal-keyed file-backed state store (delegates to ported modules)."""

    # --- pending previews -------------------------------------------------

    async def store_pending(self, portal_id: str, action_id: str, data: dict[str, Any]) -> None:
        await asyncio.to_thread(persistence.store, portal_id, action_id, data)

    async def load_pending(self, portal_id: str, action_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(persistence.load, portal_id, action_id)

    async def confirm_pending(self, portal_id: str, action_id: str, count: int) -> bool:
        return await asyncio.to_thread(persistence.confirm, portal_id, action_id, count)

    async def clear_pending(self, portal_id: str, action_id: str) -> None:
        await asyncio.to_thread(persistence.clear, portal_id, action_id)

    async def list_pending(self, portal_id: str) -> list[str]:
        # ``persistence`` is filesystem-shaped and returns paths; the action id
        # is the filename stem.  Ordering (newest first) is preserved.
        paths = await asyncio.to_thread(persistence.list_pending, portal_id)
        return [p.stem for p in paths]

    # --- undo snapshots ---------------------------------------------------

    async def save_undo_snapshot_for_action(
        self, portal_id: str, action_id: str, preview_data: dict[str, Any]
    ) -> None:
        await asyncio.to_thread(snapshot.save_undo_snapshot_for_action, portal_id, action_id, preview_data)

    async def save_undo_snapshot(
        self,
        portal_id: str,
        action_id: str,
        original_values: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await asyncio.to_thread(
            snapshot.save_undo_snapshot,
            snapshot.snapshot_dir_for_portal(portal_id),
            action_id,
            original_values,
            metadata,
        )

    async def load_undo_snapshot(self, portal_id: str, action_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            snapshot.load_undo_snapshot, snapshot.snapshot_dir_for_portal(portal_id), action_id
        )

    async def update_undo_snapshot(
        self, portal_id: str, action_id: str, *, metadata: dict[str, Any] | None = None
    ) -> None:
        await asyncio.to_thread(
            snapshot.update_undo_snapshot,
            snapshot.snapshot_dir_for_portal(portal_id),
            action_id,
            None,
            metadata,
        )

    async def delete_undo_snapshot(self, portal_id: str, action_id: str) -> None:
        await asyncio.to_thread(
            snapshot.delete_undo_snapshot, snapshot.snapshot_dir_for_portal(portal_id), action_id
        )

    # --- audit log --------------------------------------------------------

    async def log_write(
        self,
        portal_id: str,
        *,
        action: str,
        agent: str,
        result_summary: dict[str, Any],
        informing_sources: list[dict[str, Any]] | None = None,
    ) -> None:
        await asyncio.to_thread(
            lambda: audit.log_write(
                portal_id=portal_id,
                action=action,
                agent=agent,
                result_summary=result_summary,
                informing_sources=informing_sources,
            )
        )

    async def get_recent_audits(self, portal_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return await asyncio.to_thread(lambda: audit.get_recent_audits(portal_id, limit=limit))
