from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hubspot_mcp.redaction import redact_dict_for_disk


def _audit_file_path(portal_id: str) -> Path:
    base = Path.home() / ".claude" / "hubspot" / portal_id
    base.mkdir(parents=True, exist_ok=True)
    return base / "audit.log"


def log_write(
    portal_id: str,
    action: str,
    agent: str,
    result_summary: dict[str, Any],
    informing_sources: list[dict[str, Any]] | None = None,
) -> None:
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "action": action,
        "agent": agent,
        "result_summary": result_summary,
        "informing_sources": informing_sources or [],
    }
    safe_entry = redact_dict_for_disk(entry)
    file_path = _audit_file_path(portal_id)
    with file_path.open("a") as f:
        f.write(json.dumps(safe_entry) + "\n")


def get_recent_audits(portal_id: str, limit: int = 50) -> list[dict[str, Any]]:
    file_path = _audit_file_path(portal_id)
    if not file_path.exists():
        return []
    lines = file_path.read_text().strip().splitlines()
    entries = [json.loads(line) for line in lines if line.strip()]
    return entries[-limit:]
