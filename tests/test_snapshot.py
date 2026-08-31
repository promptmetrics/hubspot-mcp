import json
from pathlib import Path

from hubspot_mcp.snapshot import delete_undo_snapshot, load_undo_snapshot, save_undo_snapshot


def test_save_undo_snapshot(tmp_path):
    snapshot_dir = tmp_path / "undo_snapshots"
    save_undo_snapshot(
        str(snapshot_dir),
        action_id="act-123",
        original_values={"contacts": [{"id": "1", "email": "old@example.com"}]},
    )
    file = snapshot_dir / "act-123.json"
    assert file.exists()
    data = json.loads(file.read_text())
    assert data["original_values"]["contacts"][0]["id"] == "1"


def test_load_undo_snapshot(tmp_path):
    snapshot_dir = tmp_path / "undo_snapshots"
    save_undo_snapshot(str(snapshot_dir), action_id="act-456", original_values={"a": 1})
    data = load_undo_snapshot(str(snapshot_dir), "act-456")
    assert data is not None
    assert data["original_values"]["a"] == 1


def test_load_undo_snapshot_missing(tmp_path):
    data = load_undo_snapshot(str(tmp_path), "nonexistent")
    assert data is None


def test_delete_undo_snapshot(tmp_path):
    snapshot_dir = tmp_path / "undo_snapshots"
    save_undo_snapshot(str(snapshot_dir), action_id="act-del", original_values={})
    file = snapshot_dir / "act-del.json"
    assert file.exists()
    delete_undo_snapshot(str(snapshot_dir), "act-del")
    assert not file.exists()
