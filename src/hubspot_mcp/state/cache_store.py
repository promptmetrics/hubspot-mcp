"""Expiring cache storage, separate from :class:`~hubspot_mcp.state.base.StateStore`.

Two kinds of data outlive a single request, and they need opposite failure
behaviour:

* **State** — pending previews, undo snapshots, the audit log. Losing one breaks
  an approve or an undo. A backend failure there must surface, so `StateStore`
  lets its errors propagate.
* **Cache** — the portal capability matrix and the HubSpot docs index. Losing
  one costs a refetch. A backend failure here must read as a **miss**, never as
  an error, or a Redis blip would fail tool calls that could simply have gone to
  HubSpot instead.

That difference is why this is a second interface rather than four more methods
on `StateStore`.

The file implementation writes to exactly the paths the per-cache classes used
before this seam existed (``CONFIG_DIR/<portal>/capabilities.json``,
``CONFIG_DIR/docs_index.json``), so the local path is unchanged.
"""
from __future__ import annotations

import json
import os
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

__all__ = [
    "CacheStore",
    "FileCacheStore",
    "get_cache_store",
    "set_cache_store",
]


class CacheStore(ABC):
    """Expiring key/value storage for derived data.

    ``scope`` is a portal id for per-portal caches, or ``None`` for global ones
    (the docs index is the same for every portal). ``name`` identifies the
    cache within that scope.
    """

    @abstractmethod
    async def get(self, scope: str | None, name: str) -> dict[str, Any] | None:
        """Return the cached value, or ``None`` if absent, expired or unreadable."""

    @abstractmethod
    async def set(self, scope: str | None, name: str, value: dict[str, Any], *, ttl_seconds: int) -> None:
        """Store a value for ``ttl_seconds``. Best-effort: never raises."""

    @abstractmethod
    async def delete(self, scope: str | None, name: str) -> None:
        """Drop a cached value. Best-effort: never raises."""


class FileCacheStore(CacheStore):
    """Local-disk cache — the stdio default."""

    def _path(self, scope: str | None, name: str) -> Path:
        # Read lazily so test fixtures that patch `config.CONFIG_DIR` take
        # effect; an import-bound local would leak writes to the real home dir.
        from hubspot_mcp.config import CONFIG_DIR

        base = CONFIG_DIR / scope if scope else CONFIG_DIR
        return base / f"{name}.json"

    async def get(self, scope: str | None, name: str) -> dict[str, Any] | None:
        import asyncio

        return await asyncio.to_thread(self._get_sync, scope, name)

    def _get_sync(self, scope: str | None, name: str) -> dict[str, Any] | None:
        path = self._path(scope, name)
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("_expires_at", 0) < time.time():
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    async def set(self, scope: str | None, name: str, value: dict[str, Any], *, ttl_seconds: int) -> None:
        import asyncio

        await asyncio.to_thread(self._set_sync, scope, name, value, ttl_seconds)

    def _set_sync(self, scope: str | None, name: str, value: dict[str, Any], ttl_seconds: int) -> None:
        from hubspot_mcp.fileio import write_private_json

        path = self._path(scope, name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # 0600: the capability matrix and schema names describe the portal's
            # configuration, which is nobody else's business on a shared host.
            write_private_json(path, {"_expires_at": time.time() + ttl_seconds, "data": value})
        except OSError as exc:
            print(f"hubspot_mcp: cache write failed for {name}: {exc}", file=sys.stderr)

    async def delete(self, scope: str | None, name: str) -> None:
        import asyncio

        await asyncio.to_thread(self._delete_sync, scope, name)

    def _delete_sync(self, scope: str | None, name: str) -> None:
        try:
            self._path(scope, name).unlink(missing_ok=True)
        except OSError as exc:
            print(f"hubspot_mcp: cache delete failed for {name}: {exc}", file=sys.stderr)


_cache_store: CacheStore | None = None


def _build_default_cache_store() -> CacheStore:
    """Follow the same backend selection as the state store."""
    from hubspot_mcp.state import BACKEND_ENV

    backend = os.environ.get(BACKEND_ENV, "").strip().lower()
    if backend == "file":
        return FileCacheStore()
    if backend == "redis" or (not backend and os.environ.get("REDIS_URL", "").strip()):
        from hubspot_mcp.state.redis_store import RedisCacheStore

        return RedisCacheStore.from_url()
    return FileCacheStore()


def get_cache_store() -> CacheStore:
    global _cache_store
    if _cache_store is None:
        _cache_store = _build_default_cache_store()
    return _cache_store


def set_cache_store(store: CacheStore | None) -> None:
    """Install a cache store, or reset to the default with ``None``."""
    global _cache_store
    _cache_store = store
