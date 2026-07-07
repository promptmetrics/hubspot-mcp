from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ProgressTracker:
    """Lightweight progress reporter for long-running bulk operations.

    Writes a small JSON snapshot after each chunk so callers (orchestrator,
    CLI, tests) can poll progress without blocking the operation.
    """

    def __init__(
        self,
        portal_id: str,
        action_id: str,
        total_records: int,
        total_chunks: int,
        base_dir: Path | None = None,
    ) -> None:
        self.portal_id = portal_id
        self.action_id = action_id
        self.total_records = total_records
        self.total_chunks = total_chunks
        self.base_dir = base_dir or (Path.home() / ".claude" / "hubspot" / portal_id)
        self.progress_file = self.base_dir / "progress" / f"{action_id}.json"

        self._started_at = datetime.now(timezone.utc)
        self._completed_chunks = 0
        self._processed_records = 0
        self._failed_records = 0
        self._last_error: str | None = None

        self._write()

    def _write(self) -> None:
        try:
            self.progress_file.parent.mkdir(parents=True, exist_ok=True)
            snapshot = self.snapshot()
            self.progress_file.write_text(json.dumps(snapshot, indent=2))
        except OSError:
            pass  # Best-effort progress tracking; don't fail the batch

    def record_chunk(
        self,
        chunk_index: int,
        succeeded: int,
        failed: int,
        last_error: str | None = None,
    ) -> None:
        self._completed_chunks = chunk_index + 1
        self._processed_records += succeeded + failed
        self._failed_records += failed
        if last_error:
            self._last_error = last_error
        self._write()

    def _eta_seconds(self) -> float | None:
        if self._completed_chunks == 0 or self._completed_chunks >= self.total_chunks:
            return None
        elapsed = max(
            0.0, (datetime.now(timezone.utc) - self._started_at).total_seconds()
        )
        seconds_per_chunk = elapsed / self._completed_chunks
        remaining_chunks = self.total_chunks - self._completed_chunks
        return seconds_per_chunk * remaining_chunks

    def snapshot(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "started_at": self._started_at.isoformat(),
            "total_records": self.total_records,
            "total_chunks": self.total_chunks,
            "completed_chunks": self._completed_chunks,
            "processed_records": self._processed_records,
            "failed_records": self._failed_records,
            "last_error": self._last_error,
            "eta_seconds": self._eta_seconds(),
            "percent_complete": round(
                (self._completed_chunks / self.total_chunks) * 100, 1
            )
            if self.total_chunks > 0
            else 0.0,
        }

    def finalize(self) -> None:
        if self.progress_file.exists():
            self.progress_file.unlink()


def read_progress(
    portal_id: str, action_id: str, base_dir: Path | None = None
) -> dict[str, Any] | None:
    base = base_dir or (Path.home() / ".claude" / "hubspot" / portal_id)
    progress_file = base / "progress" / f"{action_id}.json"
    try:
        return json.loads(progress_file.read_text())
    except (OSError, json.JSONDecodeError):
        return None
