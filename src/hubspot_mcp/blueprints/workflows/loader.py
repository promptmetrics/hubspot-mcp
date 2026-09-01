"""Loaders for JSON workflow blueprints.

Two sources:
  - ``load_packaged_blueprints()`` — JSON shipped inside the package
    (``hubspot_mcp/blueprints/workflows/data/*.json``), read via
    ``importlib.resources`` so it resolves identically from a source checkout or
    an installed wheel.
  - ``load_user_blueprints(base_dir)`` — JSON under ``<base_dir>/blueprints/``;
    defaults to ``~/.claude/hubspot/``. User blueprints override shipped ones on
    name collision (registered after, so they win).

Both take ``base_dir: Path | None = None`` (tmp_path-friendly test pattern). Bad
files are skipped with a warning, never crash startup.
"""

from __future__ import annotations

import json
import logging
from importlib.resources import files
from pathlib import Path

from . import WorkflowBlueprint
from .schema import BlueprintFile, render_spec, validate_blueprint

log = logging.getLogger(__name__)

_USER_DIR_DEFAULT = Path(".claude", "hubspot")


def _to_workflow_blueprint(bf: BlueprintFile) -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name=bf.name,
        description=bf.description,
        tags=list(bf.tags),
        parameter_schema={name: p.model_dump() for name, p in bf.parameters.items()},
        build=lambda params, _bf=bf: render_spec(_bf, params),
        origin=bf.source.origin,
    )


def load_packaged_blueprints() -> list[WorkflowBlueprint]:
    """Load blueprints shipped inside the package ``data/`` directory."""
    out: list[WorkflowBlueprint] = []
    data_dir = files(__package__) / "data"  # type: ignore[name-defined]
    if not data_dir.is_dir():
        return out
    for entry in sorted(data_dir.iterdir(), key=lambda e: e.name):
        if not entry.name.endswith(".json"):
            continue
        try:
            raw = json.loads(entry.read_text())
            out.append(_to_workflow_blueprint(validate_blueprint(raw)))
        except Exception as exc:  # noqa: BLE001 - never crash startup over a data file
            log.warning("Skipping packaged blueprint %s: %s", entry.name, exc)
    return out


def load_user_blueprints(base_dir: Path | None = None) -> list[WorkflowBlueprint]:
    """Load approved user blueprints under ``<base_dir>/blueprints/``."""
    base = Path(base_dir) if base_dir is not None else Path.home() / _USER_DIR_DEFAULT
    out: list[WorkflowBlueprint] = []
    bdir = base / "blueprints"
    if not bdir.is_dir():
        return out
    for path in sorted(bdir.glob("*.json")):
        try:
            out.append(_to_workflow_blueprint(validate_blueprint(json.loads(path.read_text()))))
        except Exception as exc:  # noqa: BLE001
            log.warning("Skipping user blueprint %s: %s", path.name, exc)
    return out


def list_drafts(base_dir: Path | None = None) -> list[Path]:
    """List extraction-draft JSON files (not registered) awaiting human review."""
    base = Path(base_dir) if base_dir is not None else Path.home() / _USER_DIR_DEFAULT
    ddir = base / "blueprints" / "drafts"
    if not ddir.is_dir():
        return []
    return sorted(ddir.glob("*.json"))


def load_blueprint_file(path: Path | str) -> BlueprintFile:
    """Validate and return a single ``BlueprintFile`` from disk (used by tools)."""
    return validate_blueprint(json.loads(Path(path).read_text()))