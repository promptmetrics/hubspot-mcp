from hubspot_mcp.checkpoint import CheckpointManager


def test_checkpoint_record_chunk_creates_file(tmp_path):
    cp = CheckpointManager("123", "a1", base_dir=tmp_path)
    cp.record_chunk(0, "batch_create", 10, 0, [])
    assert (tmp_path / "in_flight" / "a1.jsonl").exists()


def test_checkpoint_get_resume_state(tmp_path):
    cp = CheckpointManager("123", "a1", base_dir=tmp_path)
    cp.record_chunk(0, "batch_create", 10, 0, [])
    cp.record_chunk(1, "batch_create", 8, 2, [{"message": "fail"}])
    state = cp.get_resume_state()
    assert state is not None
    assert state["last_completed_chunk"] == 1
    assert state["total_chunks_completed"] == 2
    assert state["errors_so_far"] == 2
    assert state["last_operation"] == "batch_create"


def test_checkpoint_get_resume_state_fresh(tmp_path):
    cp = CheckpointManager("123", "a1", base_dir=tmp_path)
    assert cp.get_resume_state() is None


def test_checkpoint_finalize_moves_file(tmp_path):
    cp = CheckpointManager("123", "a1", base_dir=tmp_path)
    cp.record_chunk(0, "batch_create", 10, 0, [])
    cp.finalize()
    assert not (tmp_path / "in_flight" / "a1.jsonl").exists()
    assert (tmp_path / "completed" / "a1.jsonl").exists()


def test_checkpoint_abandon_removes_file(tmp_path):
    cp = CheckpointManager("123", "a1", base_dir=tmp_path)
    cp.record_chunk(0, "batch_create", 10, 0, [])
    cp.abandon()
    assert not (tmp_path / "in_flight" / "a1.jsonl").exists()


def test_checkpoint_list_in_flight(tmp_path):
    cp1 = CheckpointManager("123", "a1", base_dir=tmp_path)
    cp1.record_chunk(0, "batch_create", 5, 0, [])
    cp2 = CheckpointManager("123", "a2", base_dir=tmp_path)
    cp2.record_chunk(0, "batch_update", 3, 1, [{"message": "err"}])

    in_flight = cp1.list_in_flight()
    assert len(in_flight) == 2
    ids = {s["action_id"] for s in in_flight}
    assert ids == {"a1", "a2"}


def test_checkpoint_corrupt_line_ignored(tmp_path):
    cp = CheckpointManager("123", "a1", base_dir=tmp_path)
    log_file = tmp_path / "in_flight" / "a1.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("bad json\n")
    assert cp.get_resume_state() is None


def test_checkpoint_list_in_flight_corrupt_middle_line(tmp_path):
    cp = CheckpointManager("123", "a1", base_dir=tmp_path)
    cp.record_chunk(0, "batch_create", 5, 0, [])
    log_file = tmp_path / "in_flight" / "a1.jsonl"
    # Prepend a corrupt line before the valid record
    raw = log_file.read_text()
    log_file.write_text("bad json\n" + raw)
    in_flight = cp.list_in_flight()
    assert len(in_flight) == 1
    assert in_flight[0]["action_id"] == "a1"
    assert in_flight[0]["total_records"] == 5


def test_checkpoint_list_in_flight_missing_dir(tmp_path):
    cp = CheckpointManager("123", "a1", base_dir=tmp_path)
    assert cp.list_in_flight() == []
