from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hubspot_mcp import config


def snapshot_dir_for_portal(portal_id: str) -> str:
    """Directory holding a portal's undo snapshots.

    ``config.CONFIG_DIR`` is read lazily so test fixtures that monkeypatch
    ``hubspot_mcp.config.CONFIG_DIR`` redirect snapshot writes; an
    import-bound local would freeze the original path and leak to real disk.
    """
    return str(config.CONFIG_DIR / portal_id / "undo_snapshots")


def save_undo_snapshot_for_action(
    portal_id: str,
    action_id: str,
    preview_data: dict[str, Any],
    created_ids: list[str] | None = None,
) -> Path:
    """Persist an undo snapshot for a pending write from its preview record.

    Called by :func:`hubspot_mcp.handlers.execute_pending_write`, the shared
    core used by both the CLI approve path and the daemon ``handle_approve``,
    so both capture the same undo artifact (FR-17/FR-18).
    """
    intent = preview_data.get("intent") or {}
    intent_type = intent.get("intent_type", "unknown")
    target_object = intent.get("target_object")
    preview = preview_data.get("preview") or {}
    original_values = preview.get("original_values", {})

    undoable = intent_type in ("create", "update")
    metadata: dict[str, Any] = {
        "intent_type": intent_type,
        "target_object": target_object,
        "undoable": undoable,
    }
    if created_ids:
        metadata["created_ids"] = created_ids

    return save_undo_snapshot(
        snapshot_dir_for_portal(portal_id),
        action_id,
        original_values,
        metadata=metadata,
    )


def save_undo_snapshot(
    snapshot_dir: str,
    action_id: str,
    original_values: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> Path:
    dir_path = Path(snapshot_dir)
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / f"{action_id}.json"
    payload: dict[str, Any] = {
        "action_id": action_id,
        "original_values": original_values,
    }
    if metadata:
        payload["metadata"] = metadata
    file_path.write_text(json.dumps(payload, indent=2))
    return file_path


def update_undo_snapshot(
    snapshot_dir: str,
    action_id: str,
    original_values: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path | None:
    """Merge additional data into an existing undo snapshot."""
    file_path = Path(snapshot_dir) / f"{action_id}.json"
    if not file_path.exists():
        return None
    payload = json.loads(file_path.read_text())
    if original_values is not None:
        payload["original_values"] = original_values
    if metadata is not None:
        payload.setdefault("metadata", {}).update(metadata)
    file_path.write_text(json.dumps(payload, indent=2))
    return file_path


def load_undo_snapshot(snapshot_dir: str, action_id: str) -> dict[str, Any] | None:
    file_path = Path(snapshot_dir) / f"{action_id}.json"
    if not file_path.exists():
        return None
    return json.loads(file_path.read_text())


def delete_undo_snapshot(snapshot_dir: str, action_id: str) -> None:
    file_path = Path(snapshot_dir) / f"{action_id}.json"
    if file_path.exists():
        file_path.unlink()
