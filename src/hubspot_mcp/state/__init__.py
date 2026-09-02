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

import os

from hubspot_mcp.state.base import StateStore
from hubspot_mcp.state.file_store import FileStateStore

__all__ = ["StateStore", "FileStateStore", "get_store", "set_store", "BACKEND_ENV"]

BACKEND_ENV = "HUBSPOT_MCP_STATE_BACKEND"

_store: StateStore | None = None


def _build_default_store() -> StateStore:
    """Pick a store from the environment.

    `REDIS_URL` alone selects Redis: it is what every Vercel Marketplace Redis
    integration injects, so the hosted deployment configures itself and there is
    no second variable to forget — forgetting it would mean serving from local
    disk on a multi-instance host, where a preview minted on one instance is
    invisible to the approve that follows. `HUBSPOT_MCP_STATE_BACKEND` forces
    either backend when the inference is wrong.
    """
    backend = os.environ.get(BACKEND_ENV, "").strip().lower()
    if backend == "file":
        return FileStateStore()
    if backend == "redis" or (not backend and os.environ.get("REDIS_URL", "").strip()):
        # Imported here rather than at module scope so the stdio path does not
        # pay redis's import cost for a backend it never selects.
        from hubspot_mcp.state.redis_store import RedisStateStore

        return RedisStateStore.from_url()
    if backend:
        raise ValueError(f"{BACKEND_ENV}={backend!r} is not a known backend (expected 'file' or 'redis').")
    return FileStateStore()


def get_store() -> StateStore:
    """Return the process-wide state store, constructing the default on first use.

    Lazy rather than eager because the file store resolves paths relative to
    ``Path.home()`` at call time, which the test suite redirects per-test.
    """
    global _store
    if _store is None:
        _store = _build_default_store()
    return _store


def set_store(store: StateStore | None) -> None:
    """Install a state store, or reset to the default with ``None``.

    Phase 2 calls this once at startup to install the remote store. Tests use
    it to run the safety path against an in-memory double.
    """
    global _store
    _store = store
