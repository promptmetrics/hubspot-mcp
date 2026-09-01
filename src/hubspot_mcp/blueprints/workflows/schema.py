"""Pydantic v2 schema for JSON workflow blueprints.

A blueprint file is validated data (not code) so that agent-generated user
blueprints are reviewable. ``render_spec`` turns a validated ``BlueprintFile``
plus caller params into the UI-spec dict that ``converter.blueprint_to_v4_payload``
consumes.

Substitution rule (``substitute_params``):
  - A string field that is *exactly* ``"{{param:name}}"`` is an exact-match token.
    Lists and bools are preserved raw; everything else (int/float/str) is
    stringified via ``str()``. Stringification matches the existing hand-authored
    ``_build`` functions, which wrote ``str(threshold)`` / ``str(increment)`` —
    so JSON parity with the legacy Python blueprints is byte-identical, while the
    ``include_if`` truthiness check still sees the raw int (``0`` disables a
    branch). Hardcoded values (no token) pass through untouched, so extracted
    numeric values round-trip with their original type.
  - A token embedded in a longer string interpolates as ``str(value)``.
  - HubSpot personalization tokens (``{{contact.firstname}}``, ``{{timestamp + 5m}}``)
    have no ``param:`` prefix and are left untouched.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError


_PARAM_TOKEN_RE = re.compile(r"\{\{param:([a-zA-Z_][a-zA-Z0-9_]*)\}\}")
_EXACT_TOKEN_RE = re.compile(r"^\{\{param:([a-zA-Z_][a-zA-Z0-9_]*)\}\}$")


class BlueprintSource(BaseModel):
    origin: str = "shipped"  # shipped | extracted | manual
    portal_id: str | None = None
    workflow_id: str | None = None
    extracted_at: str | None = None


class BlueprintParameter(BaseModel):
    type: str = "string"
    default: Any = None
    description: str = ""
    required: bool = False


class Flag(BaseModel):
    path: str
    kind: str
    value: Any = None
    suggestion: str = ""
    # Set by the parameterize tool when the user accepts a portal-specific or
    # unmodeled value as intentional. Promote refuses while any flag is
    # unacknowledged (and not parameterized away) unless ``force=True``.
    acknowledged: bool = False


class BlueprintSpec(BaseModel):
    ui_path: str = ""
    object_type: str = "Contact-based"
    enrollment: dict[str, Any] = Field(default_factory=dict)
    # Each action is a UiAction dict ({ui_action, fields, true_branch?, include_if?})
    # or a RawAction dict ({raw: True, action_type_id, node, note, include_if?}).
    actions: list[dict[str, Any]] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    validation: list[str] = Field(default_factory=list)


class BlueprintFile(BaseModel):
    format_version: int = 1
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    source: BlueprintSource = Field(default_factory=BlueprintSource)
    notes: list[str] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    parameters: dict[str, BlueprintParameter] = Field(default_factory=dict)
    spec: BlueprintSpec


def is_raw_action(action: dict[str, Any]) -> bool:
    return bool(isinstance(action, dict) and action.get("raw") is True)


def _coerce_exact(value: Any) -> Any:
    # Exact-match token: preserve lists and bools raw; stringify numbers/strings.
    if isinstance(value, (list, bool)):
        return value
    return str(value)


def _substitute_string(value: str, params: dict[str, Any], path: str) -> Any:
    exact = _EXACT_TOKEN_RE.match(value)
    if exact:
        name = exact.group(1)
        if name not in params:
            raise ValueError(
                f"Missing parameter '{name}' referenced at {path}; "
                f"available: {sorted(params)}"
            )
        return _coerce_exact(params[name])

    def _repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in params:
            raise ValueError(
                f"Missing parameter '{name}' referenced at {path}; "
                f"available: {sorted(params)}"
            )
        return str(params[name])

    return _PARAM_TOKEN_RE.sub(_repl, value)


def substitute_params(node: Any, params: dict[str, Any], path: str = "") -> Any:
    """Deep-walk ``node`` substituting ``{{param:name}}`` tokens per the typed rule."""
    if isinstance(node, str):
        return _substitute_string(node, params, path)
    if isinstance(node, list):
        return [substitute_params(v, params, f"{path}[{i}]") for i, v in enumerate(node)]
    if isinstance(node, dict):
        return {
            k: substitute_params(v, params, f"{path}.{k}" if path else k)
            for k, v in node.items()
        }
    return node


def _resolve_params(
    parameters: dict[str, BlueprintParameter], params: dict[str, Any] | None
) -> dict[str, Any]:
    resolved: dict[str, Any] = {name: p.default for name, p in parameters.items()}
    provided = params or {}
    for k, v in provided.items():
        resolved[k] = v
    for name, p in parameters.items():
        if p.required and resolved.get(name) is None and name not in provided:
            raise ValueError(f"Required parameter '{name}' was not provided")
    return resolved


def _filter_actions(
    actions: list[dict[str, Any]], params: dict[str, Any]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for action in actions:
        cond = action.get("include_if")
        if cond is not None and not params.get(cond):
            continue
        if is_raw_action(action):
            out.append(
                {
                    "raw": True,
                    "action_type_id": action["action_type_id"],
                    "node": action.get("node", {}),
                    "note": action.get("note", ""),
                }
            )
        else:
            a: dict[str, Any] = {
                "ui_action": action["ui_action"],
                "fields": dict(action.get("fields", {})),
            }
            if action.get("true_branch"):
                a["true_branch"] = _filter_actions(action["true_branch"], params)
            out.append(a)
    return out


def _renumber(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Top-level actions carry a 1-based ``step``; nested true_branch actions do not.
    for i, action in enumerate(actions, start=1):
        action["step"] = i
    return actions


def render_spec(blueprint_file: BlueprintFile, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render a validated ``BlueprintFile`` into the UI-spec dict consumed by
    ``converter.blueprint_to_v4_payload``.

    Order: resolve params (defaults <- provided) -> filter ``include_if`` actions
    -> renumber top-level steps -> substitute param tokens. The workflow ``name``
    is intentionally NOT emitted here; the calling tool injects it (legacy behavior).
    """
    resolved = _resolve_params(blueprint_file.parameters, params)
    spec = blueprint_file.spec
    actions = _renumber(_filter_actions(spec.actions, resolved))
    rendered: dict[str, Any] = {
        "ui_path": spec.ui_path,
        "object_type": spec.object_type,
        "enrollment": spec.enrollment,
        "actions": actions,
        "prerequisites": list(spec.prerequisites),
        "validation": list(spec.validation),
    }
    return substitute_params(rendered, resolved)


def validate_blueprint(data: dict[str, Any]) -> BlueprintFile:
    """Validate a raw dict into a ``BlueprintFile`` with path-listing errors."""
    try:
        bf = BlueprintFile.model_validate(data)
    except ValidationError as exc:
        lines = [f"  {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise ValueError("Invalid blueprint file:\n" + "\n".join(lines)) from exc

    for i, action in enumerate(bf.spec.actions):
        if is_raw_action(action):
            if not action.get("action_type_id"):
                raise ValueError(
                    f"Invalid blueprint file:\n  spec.actions[{i}]: raw action missing 'action_type_id'"
                )
        else:
            if not action.get("ui_action"):
                raise ValueError(
                    f"Invalid blueprint file:\n  spec.actions[{i}]: action missing 'ui_action'"
                )
    return bf