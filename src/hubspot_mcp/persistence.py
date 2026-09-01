from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hubspot_mcp.config import CONFIG_DIR, _validate_portal_id

# Charset validation, not the uuid4[:8] mint format: action_ids only need to
# be path-safe (no separators, no dot segments), and looser ids appear in
# fixtures.  User-supplied ids (approve/reject) that fail this are treated as
# not-found; store() raises because its ids are always self-minted.
_ACTION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _valid_action_id(action_id: str) -> bool:
    return bool(action_id and _ACTION_ID_RE.fullmatch(action_id))


def _pending_previews_dir(portal_id: str) -> Path:
    _validate_portal_id(portal_id)
    return CONFIG_DIR / portal_id / "pending_previews"


@contextmanager
def _dir_lock(dir_path: Path):
    """Exclusive flock on a directory to serialize concurrent pending-preview writers."""
    dir_path.mkdir(parents=True, exist_ok=True)
    fd = os.open(dir_path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically write JSON to ``path`` (mkstemp -> fsync -> os.replace).

    Caller is responsible for holding ``_dir_lock`` if concurrent safety is needed;
    this helper does not lock (avoids self-deadlock when the caller already locks).
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + "-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def store(portal_id: str, action_id: str, data: dict[str, Any]) -> None:
    """Persist a pending preview to disk with a server-side timestamp."""
    if not _valid_action_id(action_id):
        raise ValueError(f"Invalid action_id: {action_id!r}")
    pending_dir = _pending_previews_dir(portal_id)
    file_path = pending_dir / f"{action_id}.json"
    data["_stored_at"] = datetime.now(UTC).isoformat()
    with _dir_lock(pending_dir):
        _atomic_write_json(file_path, data)


def load(portal_id: str, action_id: str) -> dict[str, Any] | None:
    """Load a pending preview from disk by action_id."""
    if not _valid_action_id(action_id):
        return None
    file_path = _pending_previews_dir(portal_id) / f"{action_id}.json"
    if not file_path.exists():
        return None
    return json.loads(file_path.read_text())


def clear(portal_id: str, action_id: str) -> None:
    """Remove a pending preview from disk."""
    if not _valid_action_id(action_id):
        return
    pending_dir = _pending_previews_dir(portal_id)
    if not pending_dir.exists():
        return
    file_path = pending_dir / f"{action_id}.json"
    with _dir_lock(pending_dir):
        if file_path.exists():
            file_path.unlink()


def list_pending(portal_id: str) -> list[Path]:
    """List pending preview files, newest first."""
    pending_dir = _pending_previews_dir(portal_id)
    if not pending_dir.exists():
        return []
    return sorted(pending_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)


def reap_expired(portal_id: str, max_age_hours: int = 24) -> int:
    """Remove previews older than max_age_hours. Returns count removed."""
    pending_dir = _pending_previews_dir(portal_id)
    if not pending_dir.exists():
        return 0
    cutoff = datetime.now(UTC).timestamp() - (max_age_hours * 3600)
    removed = 0
    with _dir_lock(pending_dir):
        for file_path in pending_dir.iterdir():
            if file_path.suffix != ".json":
                continue
            try:
                data = json.loads(file_path.read_text())
                stored = data.get("_stored_at")
                if not stored:
                    mtime = file_path.stat().st_mtime
                    if mtime < cutoff:
                        file_path.unlink()
                        removed += 1
                    continue
                stored_dt = datetime.fromisoformat(stored)
                if stored_dt.timestamp() < cutoff:
                    file_path.unlink()
                    removed += 1
            except (json.JSONDecodeError, ValueError, OSError):
                continue
    return removed


def confirm(portal_id: str, action_id: str, count: int) -> bool:
    """Record a count-based confirmation for a destructive pending preview.

    Returns True if the provided count matches the preview's required
    confirmation value and the confirmation is persisted.
    """
    if not _valid_action_id(action_id):
        return False
    pending_dir = _pending_previews_dir(portal_id)
    file_path = pending_dir / f"{action_id}.json"
    if not file_path.exists():
        return False
    with _dir_lock(pending_dir):
        if not file_path.exists():
            return False
        data = json.loads(file_path.read_text())
        required = data.get("required_confirmation")
        if required is None or int(required) != int(count):
            return False
        data["confirmed_count"] = count
        _atomic_write_json(file_path, data)
        return True