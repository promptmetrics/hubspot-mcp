from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hubspot_mcp.redaction import redact_dict_for_disk

EVENT_TYPES = frozenset({
    "request_received",
    "loop_start",
    "webhook_received",
    "route_decision",
    "tool_call",
    "approval",
    "completion",
    "error",
    "reflection",
})


class TraceEvent:
    def __init__(
        self,
        event_type: str,
        timestamp: datetime,
        trace_id: str,
        portal_id: str,
        data: dict[str, Any],
    ) -> None:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Invalid event_type: {event_type}")
        self.event_type = event_type
        self.timestamp = timestamp
        self.trace_id = trace_id
        self.portal_id = portal_id
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "trace_id": self.trace_id,
            "portal_id": self.portal_id,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TraceEvent:
        ts = raw.get("timestamp", "")
        timestamp = datetime.fromisoformat(ts) if ts else datetime.now(UTC)
        return cls(
            event_type=raw["event_type"],
            timestamp=timestamp,
            trace_id=raw["trace_id"],
            portal_id=raw["portal_id"],
            data=raw.get("data", {}),
        )


class TraceSummary:
    def __init__(
        self,
        trace_id: str,
        duration_ms: float,
        tool_call_count: int,
        token_count: int | None = None,
        estimated_usd: float | None = None,
        batch_approval_mode: str | None = None,
    ) -> None:
        self.trace_id = trace_id
        self.duration_ms = duration_ms
        self.tool_call_count = tool_call_count
        self.token_count = token_count
        self.estimated_usd = estimated_usd
        self.batch_approval_mode = batch_approval_mode


def _trace_file_path(portal_id: str) -> Path:
    # Upstream routes this through ``maintenance._portal_dir``, which is not
    # ported (CLI housekeeping). Root it at ``config.CONFIG_DIR`` instead, the
    # single source of truth every other module here uses, read lazily so test
    # fixtures that patch it take effect. ``portal_id`` is validated before it
    # builds a path, for the same reason persistence validates it: a traversal
    # value would otherwise escape the config root.
    from hubspot_mcp.config import CONFIG_DIR, _validate_portal_id

    _validate_portal_id(portal_id)
    base = CONFIG_DIR / portal_id
    base.mkdir(parents=True, exist_ok=True)
    return base / "traces.jsonl"


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def emit_trace(
    portal_id: str,
    event_type: str,
    trace_id: str,
    data: dict[str, Any],
) -> None:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Invalid event_type: {event_type}")

    event = TraceEvent(
        event_type=event_type,
        timestamp=datetime.now(UTC),
        trace_id=trace_id,
        portal_id=portal_id,
        data=data,
    )

    safe_entry = event.to_dict()
    safe_entry["data"] = redact_dict_for_disk(safe_entry["data"])
    line = json.dumps(safe_entry, sort_keys=True) + "\n"

    # Append, not rewrite. The upstream version copied the whole file into a
    # temp file on every event; with one event per tool call that copy grows
    # with the log and lands inside the latency being measured. A single
    # ``write`` of one line is atomic on POSIX for lines this size.
    file_path = _trace_file_path(portal_id)
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(line)


TRACE_ENV = "HUBSPOT_MCP_TRACE"


def emit_safely(portal_id: str | None, event_type: str, data: dict[str, Any]) -> None:
    """``emit_trace`` for telemetry call sites: never raises, off with ``HUBSPOT_MCP_TRACE=0``."""
    if not portal_id or os.getenv(TRACE_ENV, "1") == "0":
        return
    try:
        emit_trace(portal_id, event_type, new_trace_id(), data)
    except Exception:  # noqa: BLE001 — telemetry must never fail a tool call
        pass


def get_recent_traces(portal_id: str, limit: int = 50) -> list[TraceEvent]:
    file_path = _trace_file_path(portal_id)
    if not file_path.exists():
        return []
    # The file is append-only and grows with every tool call, so read only the tail.
    from collections import deque

    with open(file_path, encoding="utf-8") as fh:
        lines = deque(fh, maxlen=limit)
    events: list[TraceEvent] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            events.append(TraceEvent.from_dict(json.loads(line)))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return events[-limit:]


def _parse_trace_id_events(portal_id: str, trace_id: str) -> list[TraceEvent]:
    return [e for e in get_recent_traces(portal_id, limit=1000) if e.trace_id == trace_id]


def compute_trace_summary(portal_id: str, trace_id: str) -> TraceSummary | None:
    events = _parse_trace_id_events(portal_id, trace_id)
    if not events:
        return None

    events.sort(key=lambda e: e.timestamp)
    start = events[0].timestamp
    end = events[-1].timestamp
    duration_ms = (end - start).total_seconds() * 1000

    tool_call_count = sum(
        1 for e in events if e.event_type == "tool_call"
    )

    completion_event = next(
        (e for e in events if e.event_type == "completion"), None
    )
    token_count = None
    estimated_usd = None
    batch_approval_mode = None
    if completion_event:
        token_count = completion_event.data.get("token_count")
        estimated_usd = completion_event.data.get("estimated_usd")
        batch_approval_mode = completion_event.data.get("batch_approval_mode")

    return TraceSummary(
        trace_id=trace_id,
        duration_ms=duration_ms,
        tool_call_count=tool_call_count,
        token_count=token_count,
        estimated_usd=estimated_usd,
        batch_approval_mode=batch_approval_mode,
    )


def compute_status_aggregates(
    portal_id: str,
    window_hours: int = 24,
) -> dict[str, Any]:
    events = get_recent_traces(portal_id, limit=5000)
    if not events:
        return {
            "total_requests": 0,
            "avg_latency_ms": 0.0,
            "total_estimated_usd": 0.0,
            "tool_call_counts": {},
            "error_rate": 0.0,
        }

    now = datetime.now(UTC)
    cutoff = now.timestamp() - (window_hours * 3600)

    trace_groups: dict[str, list[TraceEvent]] = {}
    for e in events:
        if e.timestamp.timestamp() < cutoff:
            continue
        trace_groups.setdefault(e.trace_id, []).append(e)

    total_requests = len(trace_groups)
    total_latency_ms = 0.0
    total_estimated_usd = 0.0
    tool_call_counts: dict[str, int] = {}
    error_count = 0

    for trace_events in trace_groups.values():
        trace_events.sort(key=lambda e: e.timestamp)
        start = trace_events[0].timestamp
        end = trace_events[-1].timestamp
        span_ms = (end - start).total_seconds() * 1000
        # A single tool_call event carries its own elapsed_ms; a span of one
        # event would otherwise count as zero latency.
        if span_ms == 0:
            span_ms = sum(float(e.data.get("elapsed_ms") or 0) for e in trace_events if e.event_type == "tool_call")
        total_latency_ms += span_ms

        has_error = any(
            e.event_type == "error" or (e.event_type == "tool_call" and e.data.get("ok") is False)
            for e in trace_events
        )
        if has_error:
            error_count += 1

        for e in trace_events:
            if e.event_type == "tool_call":
                tool_name = e.data.get("tool_name", "unknown")
                tool_call_counts[tool_name] = tool_call_counts.get(tool_name, 0) + 1

        completion = next((e for e in trace_events if e.event_type == "completion"), None)
        if completion and completion.data.get("estimated_usd") is not None:
            total_estimated_usd += completion.data["estimated_usd"]

    avg_latency_ms = total_latency_ms / total_requests if total_requests else 0.0
    error_rate = error_count / total_requests if total_requests else 0.0

    return {
        "total_requests": total_requests,
        "avg_latency_ms": round(avg_latency_ms, 2),
        "total_estimated_usd": round(total_estimated_usd, 4),
        "tool_call_counts": tool_call_counts,
        "error_rate": round(error_rate, 4),
    }
