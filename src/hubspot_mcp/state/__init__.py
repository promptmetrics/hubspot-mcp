"""State stores for the write-safety state machine.

`StateStore` (base) abstracts pending-preview / undo-snapshot / audit storage so
Phase 2 can swap in `RedisStateStore` without touching tool bodies. Phase 1
ships `FileStateStore` over the ported persistence/snapshot/audit modules.
"""
from __future__ import annotations

from hubspot_mcp.state.base import StateStore
from hubspot_mcp.state.file_store import FileStateStore

__all__ = ["StateStore", "FileStateStore"]