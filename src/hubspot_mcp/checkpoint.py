from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CheckpointManager:
    """Per-chunk checkpointing for bulk operations.

    Writes chunk status to in_flight/<action_id>.jsonl.
    On resumption, reads the last completed chunk and offers restart.
    """

    def __init__(self, portal_id: str, action_id: str, base_dir: Path | None = None) -> None:
        self.portal_id = portal_id
        self.action_id = action_id
        self.base_dir = base_dir or (Path.home() / ".claude" / "hubspot" / portal_id)
        self.in_flight_dir = self.base_dir / "in_flight"
        self.completed_dir = self.base_dir / "completed"
        self.checkpoint_file = self.in_flight_dir / f"{action_id}.jsonl"

    def _ensure_dirs(self) -> None:
        self.in_flight_dir.mkdir(parents=True, exist_ok=True)
        self.completed_dir.mkdir(parents=True, exist_ok=True)

    def _append_atomic(self, entry: dict[str, Any]) -> None:
        self._ensure_dirs()
        line = json.dumps(entry, sort_keys=True) + "\n"
        fd, temp_path = tempfile.mkstemp(dir=str(self.in_flight_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                if self.checkpoint_file.exists():
                    f.write(self.checkpoint_file.read_text())
                f.write(line)
            os.replace(temp_path, self.checkpoint_file)
        except Exception:
            os.unlink(temp_path)
            raise

    def record_chunk(
        self,
        chunk_index: int,
        operation: str,
        succeeded: int,
        failed: int,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        self._append_atomic({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action_id": self.action_id,
            "chunk_index": chunk_index,
            "operation": operation,
            "succeeded": succeeded,
            "failed": failed,
            "errors": errors or [],
        })

    def get_resume_state(self) -> dict[str, Any] | None:
        """Return the last completed chunk index, or None if checkpoint is fresh."""
        if not self.checkpoint_file.exists():
            return None
        lines = self.checkpoint_file.read_text().strip().splitlines()
        if not lines:
            return None
        entries: list[dict[str, Any]] = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not entries:
            return None
        last = entries[-1]
        return {
            "last_completed_chunk": last.get("chunk_index", -1),
            "total_chunks_completed": len(entries),
            "last_operation": last.get("operation"),
            "errors_so_far": sum(e.get("failed", 0) for e in entries),
        }

    def finalize(self) -> None:
        """Move checkpoint from in_flight to completed."""
        self._ensure_dirs()
        if self.checkpoint_file.exists():
            dest = self.completed_dir / self.checkpoint_file.name
            self.checkpoint_file.rename(dest)

    def abandon(self) -> None:
        """Delete the in_flight checkpoint (user cancelled)."""
        if self.checkpoint_file.exists():
            self.checkpoint_file.unlink()

    def list_in_flight(self) -> list[dict[str, Any]]:
        """Return resume states for all in-flight bulk operations."""
        if not self.in_flight_dir.exists():
            return []
        states: list[dict[str, Any]] = []
        for path in sorted(self.in_flight_dir.iterdir()):
            if path.suffix == ".jsonl":
                aid = path.stem
                lines = path.read_text().strip().splitlines()
                if lines:
                    def _load_line(line: str) -> dict[str, Any] | None:
                        try:
                            return json.loads(line)
                        except json.JSONDecodeError:
                            return None

                    loaded = [_load_line(l) for l in lines]
                    valid = [e for e in loaded if isinstance(e, dict)]
                    if not valid:
                        continue
                    first = valid[0]
                    last = valid[-1]
                    records = (
                        e.get("succeeded", 0) + e.get("failed", 0) for e in valid
                    )
                    states.append({
                        "action_id": aid,
                        "operation": first.get("operation", "unknown"),
                        "last_completed_chunk": last.get("chunk_index", -1),
                        "total_records": sum(records),
                    })
        return states
