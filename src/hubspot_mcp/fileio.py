from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_private_text(path: Path, content: str) -> None:
    """Atomically write ``content`` to ``path`` with 0600 permissions from birth.

    mkstemp creates the temp file 0600, so the bytes never exist on disk with
    the process umask; the explicit chmod before ``os.replace`` is
    belt-and-braces. Use for anything secret-bearing (tokens, client secrets,
    PKCE verifiers) instead of ``write_text`` + ``chmod``, which leaves a
    world-readable window.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + "-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_private_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """``write_private_text`` with JSON serialization."""
    write_private_text(path, json.dumps(data, indent=indent, default=str))
