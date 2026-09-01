"""Abstract state-store interface for the write-safety state machine.

The safety path persists three kinds of state per portal: pending write
previews, undo snapshots, and audit-log entries. Phase 1 keeps all of that on
local disk (see :class:`~hubspot_mcp.state.file_store.FileStateStore`); this
interface exists so Phase 2 can drop in a :class:`RedisStateStore` (or any
remote store) without touching the handler or tool bodies — they depend on the
interface, not the filesystem layout.

The interface is ``portal_id``-based throughout; the file-store adapter
translates to the ``snapshot_dir``-based signatures of the ported
``snapshot`` module so callers never deal with paths.

**Every method is a coroutine**, including on the file store. All 17 call
sites live inside ``async def`` handlers, so a synchronous interface would put
a network round trip on the event loop — ``execute_pending_write`` alone makes
up to six store calls per approve. Awaiting them costs the file store one
thread hop and buys the remote store the right to be slow.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StateStore(ABC):
    """Pending-preview / undo-snapshot / audit storage for one portal."""

    # --- pending previews -------------------------------------------------

    @abstractmethod
    async def store_pending(self, portal_id: str, action_id: str, data: dict[str, Any]) -> None:
        """Persist a pending write preview under ``action_id``."""

    @abstractmethod
    async def load_pending(self, portal_id: str, action_id: str) -> dict[str, Any] | None:
        """Return a pending preview, or ``None`` if not found / expired."""

    @abstractmethod
    async def confirm_pending(self, portal_id: str, action_id: str, count: int) -> bool:
        """Record a confirmation count; return ``True`` if it matches the impact."""

    @abstractmethod
    async def clear_pending(self, portal_id: str, action_id: str) -> None:
        """Remove a pending preview (after approve/reject/expire)."""

    @abstractmethod
    async def list_pending(self, portal_id: str) -> list[str]:
        """Return the portal's pending ``action_id``s, newest first.

        Action ids, not paths: a path is meaningless to a remote store and
        useless to an MCP client, which needs the id to approve or reject.
        """

    # --- undo snapshots ---------------------------------------------------

    @abstractmethod
    async def save_undo_snapshot_for_action(self, portal_id: str, action_id: str, preview_data: dict[str, Any]) -> None:
        """Capture an undo snapshot for a pending write (FR-17/18)."""

    @abstractmethod
    async def save_undo_snapshot(
        self,
        portal_id: str,
        action_id: str,
        original_values: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Write an undo snapshot from already-captured originals.

        Distinct from :meth:`save_undo_snapshot_for_action`, which derives the
        originals from a pending preview *before* the write. The pattern-write
        executor only knows which records actually applied once the batch has
        run, so it captures its own originals and writes them here.
        """

    @abstractmethod
    async def load_undo_snapshot(self, portal_id: str, action_id: str) -> dict[str, Any] | None:
        """Return a saved undo snapshot, or ``None``."""

    @abstractmethod
    async def update_undo_snapshot(self, portal_id: str, action_id: str, *, metadata: dict[str, Any] | None = None) -> None:
        """Update snapshot metadata (e.g. record ``created_ids`` post-create)."""

    @abstractmethod
    async def delete_undo_snapshot(self, portal_id: str, action_id: str) -> None:
        """Remove an undo snapshot."""

    # --- audit log --------------------------------------------------------

    @abstractmethod
    async def log_write(self, portal_id: str, *, action: str, agent: str, result_summary: dict[str, Any], informing_sources: list[dict[str, Any]] | None = None) -> None:
        """Append a write-audit record (FR-17)."""

    @abstractmethod
    async def get_recent_audits(self, portal_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent ``limit`` audit entries (newest first)."""