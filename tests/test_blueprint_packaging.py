"""Blueprint data must resolve from the installed package, not just the repo.

Blueprints moved from Python modules (which the import system finds anywhere)
to JSON data files loaded via importlib.resources. That only works if the
`data/` directory is actually packaged -- if it is dropped from the wheel the
source tree still passes every other test while a `pip install` ships a server
with zero blueprints and `hubspot_create_workflow_from_blueprint` fails for
every name.
"""
from __future__ import annotations

import json
from pathlib import Path

from hubspot_mcp.blueprints.workflows import list_blueprints, reload_blueprints
from hubspot_mcp.blueprints.workflows.loader import load_packaged_blueprints


def test_packaged_blueprints_are_discoverable_via_importlib():
    packaged = load_packaged_blueprints()
    assert len(packaged) == 19, f"expected 19 packaged blueprints, found {len(packaged)}"


def test_every_json_file_parses_into_a_blueprint():
    """A malformed JSON blueprint must fail here, not at create time."""
    import hubspot_mcp.blueprints.workflows as pkg

    data_dir = Path(pkg.__file__).parent / "data"
    files = sorted(data_dir.glob("*.json"))
    assert files, "no blueprint JSON found next to the package"
    for path in files:
        payload = json.loads(path.read_text())
        assert payload.get("name"), f"{path.name} has no name"

    loaded = {b.name for b in load_packaged_blueprints()}
    assert len(loaded) == len(files)


def test_reload_picks_up_a_user_promoted_blueprint(tmp_path, monkeypatch):
    """The a51d8ee fix: a fresh process loads only packaged blueprints at import,
    so a blueprint promoted by an earlier process is invisible to `create`
    unless the create path reloads from disk."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    user_dir = tmp_path / ".claude" / "hubspot" / "blueprints"
    user_dir.mkdir(parents=True)
    (user_dir / "custom_thing.json").write_text(
        json.dumps(
            {
                "name": "custom_thing",
                "description": "a user-promoted blueprint",
                "tags": [],
                "parameter_schema": {},
                "spec": {"name": "custom_thing", "actions": []},
            }
        )
    )

    count = reload_blueprints()
    names = {b.name for b in list_blueprints()}
    assert "custom_thing" in names, "a promoted blueprint stayed invisible after reload"
    assert count == len(names)
