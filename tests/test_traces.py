from pathlib import Path

from hubspot_mcp.trace import (
    TraceEvent,
    compute_status_aggregates,
    compute_trace_summary,
    emit_trace,
    get_recent_traces,
    new_trace_id,
)


def test_new_trace_id():
    tid = new_trace_id()
    assert len(tid) == 12
    assert tid.isalnum()


def test_emit_and_retrieve(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path / ".claude" / "hubspot")

    tid = new_trace_id()
    emit_trace("123", "request_received", tid, {"request": "find contacts"})
    emit_trace("123", "route_decision", tid, {"agents": ["objects"]})

    events = get_recent_traces("123")
    assert len(events) == 2
    assert events[0].event_type == "request_received"
    assert events[0].trace_id == tid
    assert events[0].data["request"] == "find contacts"
    assert events[1].event_type == "route_decision"


def test_get_recent_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path / ".claude" / "hubspot")

    for i in range(5):
        emit_trace("123", "tool_call", new_trace_id(), {"tool_name": f"tool_{i}"})

    events = get_recent_traces("123", limit=3)
    assert len(events) == 3
    assert events[-1].data["tool_name"] == "tool_4"


def test_invalid_event_type(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path / ".claude" / "hubspot")

    try:
        emit_trace("123", "bogus", new_trace_id(), {})
        assert False, "should have raised"
    except ValueError as exc:
        assert "Invalid event_type" in str(exc)


def test_compute_trace_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path / ".claude" / "hubspot")

    tid = new_trace_id()
    emit_trace("123", "request_received", tid, {"request": "update contacts"})
    emit_trace("123", "tool_call", tid, {"tool_name": "hubspot_search_objects"})
    emit_trace("123", "tool_call", tid, {"tool_name": "hubspot_update_objects"})
    emit_trace("123", "completion", tid, {"token_count": 150, "estimated_usd": 0.003})

    summary = compute_trace_summary("123", tid)
    assert summary is not None
    assert summary.trace_id == tid
    assert summary.tool_call_count == 2
    assert summary.token_count == 150
    assert summary.estimated_usd == 0.003
    assert summary.duration_ms >= 0


def test_compute_trace_summary_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path / ".claude" / "hubspot")
    assert compute_trace_summary("123", "missing") is None


def test_compute_status_aggregates(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path / ".claude" / "hubspot")

    tid = new_trace_id()
    emit_trace("123", "request_received", tid, {"request": "find contacts"})
    emit_trace("123", "tool_call", tid, {"tool_name": "hubspot_search_objects_v1"})
    emit_trace("123", "completion", tid, {"estimated_usd": 0.002})

    agg = compute_status_aggregates("123", window_hours=24)
    assert agg["total_requests"] == 1
    assert agg["avg_latency_ms"] >= 0
    assert agg["total_estimated_usd"] == 0.002
    assert agg["tool_call_counts"]["hubspot_search_objects_v1"] == 1
    assert agg["error_rate"] == 0.0


def test_compute_status_aggregates_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path / ".claude" / "hubspot")

    agg = compute_status_aggregates("123", window_hours=24)
    assert agg["total_requests"] == 0
    assert agg["avg_latency_ms"] == 0.0
    assert agg["total_estimated_usd"] == 0.0
    assert agg["tool_call_counts"] == {}
    assert agg["error_rate"] == 0.0


def test_compute_status_aggregates_with_error(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path / ".claude" / "hubspot")

    tid = new_trace_id()
    emit_trace("123", "request_received", tid, {"request": "bad request"})
    emit_trace("123", "error", tid, {"message": "something failed"})

    agg = compute_status_aggregates("123", window_hours=24)
    assert agg["total_requests"] == 1
    assert agg["error_rate"] == 1.0


def test_trace_event_from_dict():
    raw = {
        "event_type": "request_received",
        "timestamp": "2026-05-08T12:00:00+00:00",
        "trace_id": "abc123",
        "portal_id": "456",
        "data": {"request": "hello"},
    }
    event = TraceEvent.from_dict(raw)
    assert event.event_type == "request_received"
    assert event.trace_id == "abc123"
    assert event.data["request"] == "hello"


def test_trace_event_to_dict():
    event = TraceEvent(
        event_type="tool_call",
        timestamp=__import__("datetime").datetime(2026, 5, 8, 12, 0, 0, tzinfo=__import__("datetime").timezone.utc),
        trace_id="tid",
        portal_id="123",
        data={"tool_name": "x"},
    )
    d = event.to_dict()
    assert d["event_type"] == "tool_call"
    assert d["trace_id"] == "tid"
    assert d["data"]["tool_name"] == "x"


def test_redaction_applied(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path / ".claude" / "hubspot")

    tid = new_trace_id()
    emit_trace("123", "request_received", tid, {"email": "test@example.com"})

    lines = (tmp_path / ".claude" / "hubspot" / "123" / "traces.jsonl").read_text().strip().splitlines()
    raw = __import__("json").loads(lines[0])
    # Email should be redacted in the on-disk JSONL
    assert "<email:" in raw["data"]["email"]
    assert "test@example.com" not in raw["data"]["email"]
