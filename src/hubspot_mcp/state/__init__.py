"""State stores for the write-safety state machine.

`StateStore` (base) abstracts pending-preview / undo-snapshot / audit storage.
Phase 1 ships `FileStateStore` over the ported persistence/snapshot/audit
modules; Phase 2 swaps in a remote store for multi-instance hosting.

Everything outside this package goes through :func:`get_store` — no handler,
tool body or server function imports `persistence`, `snapshot` or `audit`
directly. That is what makes the swap a one-line change here rather than a
rewrite of ~30 call sites, and `tests/test_state_store_seam.py` enforces it.
"""
from __future__ import annotations

from hubspot_mcp.state.base import StateStore
from hubspot_mcp.state.file_store import FileStateStore

__all__ = ["StateStore", "FileStateStore", "get_store", "set_store"]

_store: StateStore | None = None


def get_store() -> StateStore:
    """Return the process-wide state store, constructing the default on first use.

    Lazy rather than eager because the file store resolves paths relative to
    ``Path.home()`` at call time, which the test suite redirects per-test.
    """
    global _store
    if _store is None:
        _store = FileStateStore()
    return _store


def set_store(store: StateStore | None) -> None:
    """Install a state store, or reset to the default with ``None``.

    Phase 2 calls this once at startup to install the remote store. Tests use
    it to run the safety path against an in-memory double.
    """
    global _store
    _store = store
