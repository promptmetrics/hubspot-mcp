from pathlib import Path

from hubspot_mcp.progress import ProgressTracker, read_progress


def test_progress_tracker_creates_file(tmp_path):
    ProgressTracker("123", "a1", 100, 10, base_dir=tmp_path)
    assert (tmp_path / "progress" / "a1.json").exists()


def test_progress_tracker_initial_snapshot(tmp_path):
    pt = ProgressTracker("123", "a1", 100, 10, base_dir=tmp_path)
    snap = pt.snapshot()
    assert snap["action_id"] == "a1"
    assert snap["total_records"] == 100
    assert snap["total_chunks"] == 10
    assert snap["completed_chunks"] == 0
    assert snap["processed_records"] == 0
    assert snap["percent_complete"] == 0.0


def test_progress_tracker_record_chunk_updates(tmp_path):
    pt = ProgressTracker("123", "a1", 100, 10, base_dir=tmp_path)
    pt.record_chunk(0, 8, 2)
    snap = pt.snapshot()
    assert snap["completed_chunks"] == 1
    assert snap["processed_records"] == 10
    assert snap["failed_records"] == 2
    assert snap["percent_complete"] == 10.0


def test_progress_tracker_last_error(tmp_path):
    pt = ProgressTracker("123", "a1", 100, 10, base_dir=tmp_path)
    pt.record_chunk(0, 9, 1, last_error="rate limited")
    assert pt.snapshot()["last_error"] == "rate limited"


def test_progress_tracker_eta_at_start_is_none(tmp_path):
    pt = ProgressTracker("123", "a1", 100, 10, base_dir=tmp_path)
    assert pt.snapshot()["eta_seconds"] is None


def test_progress_tracker_eta_midway_exists(tmp_path):
    pt = ProgressTracker("123", "a1", 100, 10, base_dir=tmp_path)
    pt.record_chunk(0, 10, 0)
    pt.record_chunk(1, 10, 0)
    eta = pt.snapshot()["eta_seconds"]
    assert eta is not None
    assert eta >= 0


def test_progress_tracker_eta_on_completion_is_none(tmp_path):
    pt = ProgressTracker("123", "a1", 20, 2, base_dir=tmp_path)
    pt.record_chunk(0, 10, 0)
    pt.record_chunk(1, 10, 0)
    assert pt.snapshot()["eta_seconds"] is None


def test_progress_tracker_finalize_removes_file(tmp_path):
    pt = ProgressTracker("123", "a1", 100, 10, base_dir=tmp_path)
    pt.finalize()
    assert not (tmp_path / "progress" / "a1.json").exists()


def test_read_progress_returns_snapshot(tmp_path):
    pt = ProgressTracker("123", "a1", 100, 10, base_dir=tmp_path)
    pt.record_chunk(0, 10, 0)
    snap = read_progress("123", "a1", base_dir=tmp_path)
    assert snap is not None
    assert snap["completed_chunks"] == 1


def test_read_progress_missing_returns_none(tmp_path):
    assert read_progress("123", "a1", base_dir=tmp_path) is None


def test_read_progress_corrupt_returns_none(tmp_path):
    progress_file = tmp_path / "progress" / "a1.json"
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress_file.write_text("not json")
    assert read_progress("123", "a1", base_dir=tmp_path) is None


def test_progress_tracker_zero_chunks(tmp_path):
    pt = ProgressTracker("123", "a1", 0, 0, base_dir=tmp_path)
    snap = pt.snapshot()
    assert snap["percent_complete"] == 0.0
    assert snap["eta_seconds"] is None


def test_progress_tracker_finalize_idempotent(tmp_path):
    pt = ProgressTracker("123", "a1", 100, 10, base_dir=tmp_path)
    pt.finalize()
    pt.finalize()  # Should not raise
    assert not (tmp_path / "progress" / "a1.json").exists()


def test_progress_tracker_write_oserror_ignored(monkeypatch, tmp_path):
    pt = ProgressTracker("123", "a1", 100, 10, base_dir=tmp_path)

    def _raise(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "mkdir", _raise)
    pt.record_chunk(0, 10, 0)  # Should not raise despite OSError
