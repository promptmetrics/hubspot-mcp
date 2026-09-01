"""Learning-loop tools: extract a portal workflow into a reviewable blueprint
draft, parameterize the draft, and promote it to a reusable user blueprint.

Extract reads HubSpot (``automation`` scope, no HITL gate — it only GETs).
Parameterize and promote touch the user's local disk only (``set()`` scopes,
not in ``WRITE_TOOLS``) — they never POST to HubSpot, so they stay read-side
of the HITL gate. Promote refuses while flags remain neither parameterized
away nor acknowledged (unless ``force``), and refuses to overwrite an existing
user blueprint (unless ``force``).

Storage layout (global, under ``~/.claude/hubspot`` — the learning log is the
only per-portal artifact)::

    ~/.claude/hubspot/blueprints/drafts/<slug>.json   # extracted, in review
    ~/.claude/hubspot/blueprints/<slug>.json          # promoted, usable
    ~/.claude/hubspot/<portal_id>/blueprint_learning.jsonl

Flag resolution in ``parameterize``: extraction flags carry V4-actionId-relative
paths (``actions[{actionId}].content_id``) that cannot be navigated into the
blueprint (whose actions carry 1-based ``step`` numbers, no actionIds). A
parameterize edit therefore resolves value-flags by matching the OLD value at
the blueprint path; an acknowledge edit resolves any flag by its exact path
string (copied from the extraction output).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hubspot_mcp.blueprints.workflows import (
    get_blueprint,
    reload_blueprints,
)
from hubspot_mcp.blueprints.workflows.extractor import v4_payload_to_blueprint
from hubspot_mcp.blueprints.workflows.learning_log import record_unknown_actions
from hubspot_mcp.blueprints.workflows.schema import validate_blueprint
from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.tools import invoke_tool, tool

_BLUEPRINTS_DIR = Path(".claude", "hubspot")
_PATH_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "blueprint"


def _store_root(base_dir: Path | None) -> Path:
    return Path(base_dir) if base_dir is not None else Path.home() / _BLUEPRINTS_DIR


def _draft_path(name: str, base_dir: Path | None = None) -> Path:
    return _store_root(base_dir) / "blueprints" / "drafts" / f"{_slug(name)}.json"


def _promoted_path(name: str, base_dir: Path | None = None) -> Path:
    return _store_root(base_dir) / "blueprints" / f"{_slug(name)}.json"


def _path_parts(path: str) -> list[Any]:
    """Parse ``spec.actions[0].fields.content_id`` into ['spec','actions',0,'fields','content_id']."""
    parts: list[Any] = []
    for m in _PATH_TOKEN_RE.finditer(path):
        if m.group(2) is not None:
            parts.append(int(m.group(2)))
        else:
            parts.append(m.group(1))
    return parts


def _get_by_path(node: Any, path: str) -> Any:
    cur: Any = node
    for part in _path_parts(path):
        cur = cur[part]
    return cur


def _set_by_path(node: Any, path: str, value: Any) -> None:
    parts = _path_parts(path)
    cur: Any = node
    for part in parts[:-1]:
        cur = cur[part]
    cur[parts[-1]] = value


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _summary(blueprint: dict[str, Any], result: Any) -> dict[str, Any]:
    actions = blueprint.get("spec", {}).get("actions", [])
    return {
        "object_type": blueprint.get("spec", {}).get("object_type"),
        "n_actions": len(actions),
        "enrollment_type": blueprint.get("spec", {}).get("enrollment", {}).get("type"),
        "raw_action_count": sum(1 for a in actions if isinstance(a, dict) and a.get("raw") is True),
        "n_flags": len(result.flags),
        "n_dropped_settings": len(result.dropped_settings),
        "n_unknown_actions": len(result.unknown_actions),
    }


@tool(
    name="hubspot_extract_workflow_blueprint",
    description="Extract an existing HubSpot workflow into a reviewable blueprint draft.",
)
async def hubspot_extract_workflow_blueprint(
    workflow_id: str,
    client: HubSpotClient,
    portal_id: str,
    name: str | None = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """GET the workflow, invert it into a blueprint, write a draft, log unknowns.

    Read-only vs HubSpot (a single GET), so it does not pass the HITL write gate.
    The draft is written to ``<home>/.claude/hubspot/blueprints/drafts/<slug>.json``
    and is NOT registered — it must be parameterized and promoted before use.
    """
    flow = await invoke_tool(
        "hubspot_get_workflow", portal_id, workflow_id=workflow_id, client=client
    )
    if not isinstance(flow, dict) or flow.get("error"):
        err = flow.get("error", "Failed to fetch workflow") if isinstance(flow, dict) else "Failed to fetch workflow"
        return {"error": err, "tool": "hubspot_extract_workflow_blueprint"}

    result = v4_payload_to_blueprint(flow)
    blueprint = result.blueprint
    if name:
        blueprint["name"] = name
    blueprint["source"]["portal_id"] = str(portal_id)
    blueprint["source"]["extracted_at"] = _now_iso()

    draft = _draft_path(blueprint["name"], base_dir)
    _write_json(draft, blueprint)
    record_unknown_actions(
        portal_id, workflow_id, result.unknown_actions, base_dir=base_dir, recorded_at=_now_iso()
    )

    return {
        "draft_path": str(draft),
        "name": blueprint["name"],
        "summary": _summary(blueprint, result),
        "flags": result.flags,
        "unknown_actions": result.unknown_actions,
        "dropped_settings": result.dropped_settings,
        "warnings": result.warnings,
        "next_steps": [
            "Review the flags and dropped_settings in the draft.",
            "Parameterize portal-specific values with hubspot_parameterize_blueprint_draft, "
            "or acknowledge flags you accept as intentional.",
            "Promote the draft with hubspot_promote_blueprint_draft once no flags remain unacknowledged.",
        ],
    }


@tool(
    name="hubspot_parameterize_blueprint_draft",
    description="Apply surgical edits to a blueprint draft: parameterize a value or acknowledge a flag.",
)
async def hubspot_parameterize_blueprint_draft(
    name: str,
    edits: list[dict[str, Any]],
    portal_id: str,
    client: HubSpotClient | None = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Apply ``edits`` to the draft ``name``.

    Each edit is one of:
      - ``{"path": "<blueprint path>", "param_name": "x", "default?"|"description?"|"required?"}``
        replaces the value at ``path`` with ``{{param:x}}`` and registers the
        parameter. Value-flags whose ``value`` equals the old value are dropped
        (the parameterization resolves them).
      - ``{"path": "<flag path>", "acknowledge": true}`` marks the matching flag
        acknowledged (resolved by exact flag path string).
    """
    draft = _draft_path(name, base_dir)
    if not draft.is_file():
        return {"error": f"No draft named '{name}' at {draft}", "tool": "hubspot_parameterize_blueprint_draft"}
    data = json.loads(draft.read_text())

    applied = 0
    flags: list[dict[str, Any]] = data.get("flags", [])
    for edit in edits or []:
        path = edit.get("path")
        if not path:
            continue
        if edit.get("acknowledge"):
            for f in flags:
                if f.get("path") == path:
                    f["acknowledged"] = True
            applied += 1
            continue
        param_name = edit.get("param_name")
        if not param_name:
            return {"error": f"Edit at '{path}' has no param_name and no acknowledge; nothing to do."}
        try:
            old_value = _get_by_path(data, path)
        except (KeyError, IndexError, TypeError) as exc:
            return {"error": f"Path '{path}' not found in draft: {exc}"}
        _set_by_path(data, path, f"{{{{param:{param_name}}}}}")
        params: dict[str, Any] = data.setdefault("parameters", {})
        if param_name not in params:
            params[param_name] = {
                "type": "string",
                "default": edit.get("default", old_value),
                "description": edit.get("description", ""),
                "required": bool(edit.get("required", False)),
            }
        # Drop value-flags resolved by this parameterization (match old value)
        # and any flag pointing exactly at this blueprint path.
        data["flags"] = [
            f for f in flags
            if f.get("value") != old_value and f.get("path") != path
        ]
        flags = data["flags"]
        applied += 1

    try:
        validate_blueprint(data)
    except ValueError as exc:
        return {"error": str(exc), "tool": "hubspot_parameterize_blueprint_draft"}
    _write_json(draft, data)

    remaining = [f for f in data.get("flags", []) if not f.get("acknowledged")]
    return {
        "name": data["name"],
        "draft_path": str(draft),
        "applied": applied,
        "remaining_flags": remaining,
        "next_steps": [
            "Continue parameterizing or acknowledging until remaining_flags is empty.",
            "Promote with hubspot_promote_blueprint_draft when ready.",
        ],
    }


@tool(
    name="hubspot_promote_blueprint_draft",
    description="Promote a reviewed blueprint draft to a reusable user blueprint.",
)
async def hubspot_promote_blueprint_draft(
    name: str,
    portal_id: str,
    client: HubSpotClient | None = None,
    force: bool = False,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Validate, gate on unresolved flags, and move the draft to ``blueprints/``.

    Refuses if any flag is neither parameterized away nor acknowledged, unless
    ``force``. Refuses to overwrite an existing user blueprint unless ``force``.
    Warns (does not refuse) when the name shadows a shipped blueprint.
    """
    draft = _draft_path(name, base_dir)
    if not draft.is_file():
        return {"error": f"No draft named '{name}' at {draft}", "tool": "hubspot_promote_blueprint_draft"}
    data = json.loads(draft.read_text())

    unresolved = [f for f in data.get("flags", []) if not f.get("acknowledged")]
    if unresolved and not force:
        return {
            "error": f"{len(unresolved)} unresolved flag(s); parameterize or acknowledge them, or re-run with force=True.",
            "unresolved_flags": unresolved,
            "tool": "hubspot_promote_blueprint_draft",
        }

    target = _promoted_path(data["name"], base_dir)
    if target.is_file() and not force:
        return {
            "error": f"A user blueprint already exists at {target}; re-run with force=True to overwrite.",
            "tool": "hubspot_promote_blueprint_draft",
        }

    try:
        validate_blueprint(data)
    except ValueError as exc:
        return {"error": str(exc), "tool": "hubspot_promote_blueprint_draft"}

    existing = get_blueprint(data["name"])
    shadowed = existing is not None and existing.origin == "shipped"
    data["source"]["origin"] = "user"
    _write_json(target, data)
    draft.unlink()
    reload_blueprints(base_dir)

    return {
        "name": data["name"],
        "blueprint_path": str(target),
        "shadowed_shipped": shadowed,
        "origin": "user",
        "next_steps": [
            "The blueprint is now usable via hubspot_create_workflow_from_blueprint.",
            "Restart the warm-client daemon to load the promoted blueprint in that process.",
        ],
    }