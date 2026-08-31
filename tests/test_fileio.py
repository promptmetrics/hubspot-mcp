"""M2: fileio.write_private_* — 0600-from-birth atomic secret writes."""
from __future__ import annotations

import json
import os
import stat

import pytest

from hubspot_mcp import fileio
from hubspot_mcp.fileio import write_private_json, write_private_text


def test_write_private_json_mode_0o600(tmp_path):
    target = tmp_path / "creds.json"
    write_private_json(target, {"token": "secret-value"})
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert json.loads(target.read_text()) == {"token": "secret-value"}


def test_write_private_replaces_world_readable_file(tmp_path):
    # A pre-existing loose-permission file must end up 0600 with new content —
    # the atomic replace carries the temp file's tight mode, never the old one.
    target = tmp_path / "creds.json"
    target.write_text('{"token": "old"}')
    target.chmod(0o644)

    write_private_json(target, {"token": "new"})

    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert json.loads(target.read_text()) == {"token": "new"}


def test_write_private_text_creates_parent_dir(tmp_path):
    target = tmp_path / "nested" / "state.json"
    write_private_text(target, "content")
    assert target.read_text() == "content"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_write_private_no_tmp_left_on_error(tmp_path, monkeypatch):
    target = tmp_path / "creds.json"

    def _boom(src, dst):
        raise OSError("replace denied")

    monkeypatch.setattr(fileio.os, "replace", _boom)
    with pytest.raises(OSError, match="replace denied"):
        write_private_text(target, "content")

    assert not target.exists()
    assert list(tmp_path.glob("*.tmp")) == []
