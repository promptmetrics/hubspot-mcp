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
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class StateStore(ABC):
    """Pending-preview / undo-snapshot / audit storage for one portal."""

    # --- pending previews -------------------------------------------------

    @abstractmethod
    def store_pending(self, portal_id: str, action_id: str, data: dict[str, Any]) -> None:
        """Persist a pending write preview under ``action_id``."""

    @abstractmethod
    def load_pending(self, portal_id: str, action_id: str) -> dict[str, Any] | None:
        """Return a pending preview, or ``None`` if not found / expired."""

    @abstractmethod
    def confirm_pending(self, portal_id: str, action_id: str, count: int) -> bool:
        """Record a confirmation count; return ``True`` if it matches the impact."""

    @abstractmethod
    def clear_pending(self, portal_id: str, action_id: str) -> None:
        """Remove a pending preview (after approve/reject/expire)."""

    @abstractmethod
    def list_pending(self, portal_id: str) -> list[Path]:
        """List pending-preview files for the portal."""

    # --- undo snapshots ---------------------------------------------------

    @abstractmethod
    def save_undo_snapshot_for_action(self, portal_id: str, action_id: str, preview_data: dict[str, Any]) -> None:
        """Capture an undo snapshot for a pending write (FR-17/18)."""

    @abstractmethod
    def load_undo_snapshot(self, portal_id: str, action_id: str) -> dict[str, Any] | None:
        """Return a saved undo snapshot, or ``None``."""

    @abstractmethod
    def update_undo_snapshot(self, portal_id: str, action_id: str, *, metadata: dict[str, Any] | None = None) -> None:
        """Update snapshot metadata (e.g. record ``created_ids`` post-create)."""

    @abstractmethod
    def delete_undo_snapshot(self, portal_id: str, action_id: str) -> None:
        """Remove an undo snapshot."""

    # --- audit log --------------------------------------------------------

    @abstractmethod
    def log_write(self, portal_id: str, *, action: str, agent: str, result_summary: dict[str, Any], informing_sources: list[dict[str, Any]] | None = None) -> None:
        """Append a write-audit record (FR-17)."""

    @abstractmethod
    def get_recent_audits(self, portal_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent ``limit`` audit entries (newest first)."""