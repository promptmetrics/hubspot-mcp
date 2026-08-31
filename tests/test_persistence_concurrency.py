"""T2: persistence atomic + flock + 0o600 guarantees."""
from __future__ import annotations

import os
import stat
import threading

from hubspot_mcp import persistence


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(persistence, "CONFIG_DIR", tmp_path)
    return tmp_path


def test_concurrent_store_produces_intact_files(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    portal = "123"
    errors: list[Exception] = []

    def writer(i: int):
        try:
            persistence.store(portal, f"act-{i}", {"action_id": f"act-{i}", "n": i, "payload": "x" * 100})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    for i in range(20):
        data = persistence.load(portal, f"act-{i}")
        assert data is not None
        assert data["action_id"] == f"act-{i}"
        assert data["n"] == i
        assert "_stored_at" in data


def test_store_sets_0o600(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    persistence.store("123", "act-1", {"x": 1})
    p = tmp_path / "123" / "pending_previews" / "act-1.json"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_confirm_roundtrip_atomic(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    persistence.store("123", "act-1", {"required_confirmation": 5, "agent": "objects"})
    assert persistence.confirm("123", "act-1", 5) is True
    data = persistence.load("123", "act-1")
    assert data["confirmed_count"] == 5
    # Wrong count must not mutate the persisted record.
    assert persistence.confirm("123", "act-1", 99) is False
    assert persistence.load("123", "act-1")["confirmed_count"] == 5


def test_confirm_missing_action_returns_false(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert persistence.confirm("123", "nope", 1) is False


def test_clear_removes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    persistence.store("123", "act-1", {"x": 1})
    persistence.clear("123", "act-1")
    assert persistence.load("123", "act-1") is None
    # idempotent on missing dir
    persistence.clear("999", "act-1")


def test_reap_expired_removes_old(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    persistence.store("123", "act-1", {"x": 1})
    # Negative max_age -> cutoff in the future -> everything is expired.
    removed = persistence.reap_expired("123", max_age_hours=-1)
    assert removed == 1
    assert persistence.load("123", "act-1") is None



def test_load_traversal_action_id_returns_none(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    persistence.store("123", "act-1", {"x": 1})
    assert persistence.load("123", "../../123/pending_previews/act-1") is None


def test_clear_traversal_action_id_noop(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    # Decoy at the traversal target: CONFIG_DIR/123/pending_previews/../../123.json
    decoy = tmp_path / "123.json"
    decoy.write_text("{}")
    persistence.clear("123", "../../123")
    assert decoy.exists()


def test_store_rejects_bad_action_id(monkeypatch, tmp_path):
    import pytest

    _isolate(monkeypatch, tmp_path)
    for bad in ("../x", "a/b", "a.b", ""):
        with pytest.raises(ValueError, match="Invalid action_id"):
            persistence.store("123", bad, {"x": 1})


def test_confirm_traversal_action_id_returns_false(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    decoy = tmp_path / "123.json"
    decoy.write_text('{"required_confirmation": 1}')
    assert persistence.confirm("123", "../../123", 1) is False
    assert decoy.read_text() == '{"required_confirmation": 1}'


def test_pending_dir_rejects_bad_portal_id(monkeypatch, tmp_path):
    import pytest

    _isolate(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="Invalid portal_id"):
        persistence.store("../evil", "act-1", {"x": 1})


def test_store_byte_identical_serialization(monkeypatch, tmp_path):
    # Content must match the prior json.dumps(data, indent=2, default=str) shape.
    _isolate(monkeypatch, tmp_path)
    persistence.store("123", "act-1", {"a": 1, "b": [1, 2]})
    p = tmp_path / "123" / "pending_previews" / "act-1.json"
    text = p.read_text()
    assert not text.endswith("\n")  # no trailing newline, like json.dumps
    assert '"a": 1' in text  # indent=2 spacing
    assert '"b": [\n    1,\n    2\n  ]' in text