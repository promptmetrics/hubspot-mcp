from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class WorkflowBlueprint:
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    parameter_schema: dict[str, Any] = field(default_factory=dict)
    build: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    origin: str = "shipped"  # shipped | extracted | manual | user


_BLUEPRINT_REGISTRY: dict[str, WorkflowBlueprint] = {}


def register_blueprint(blueprint: WorkflowBlueprint) -> WorkflowBlueprint:
    _BLUEPRINT_REGISTRY[blueprint.name] = blueprint
    return blueprint


def list_blueprints() -> list[WorkflowBlueprint]:
    return list(_BLUEPRINT_REGISTRY.values())


def get_blueprint(name: str) -> WorkflowBlueprint | None:
    return _BLUEPRINT_REGISTRY.get(name)


# Loader is imported after the registry + dataclass exist so its ``from . import``
# resolves against this partially-initialized module without a cycle.
from .loader import (  # noqa: E402
    load_packaged_blueprints,
    load_user_blueprints,
    list_drafts,
)


def reload_blueprints(base_dir: Path | None = None) -> int:
    """Rebuild the registry: packaged first, then user (user overrides on collision).

    Refreshes only the calling process — a daemon in another process must restart
    to see promoted blueprints (documented; file-watcher/IPC refresh is out of scope).
    """
    _BLUEPRINT_REGISTRY.clear()
    for bp in load_packaged_blueprints():
        register_blueprint(bp)
    for bp in load_user_blueprints(base_dir):
        register_blueprint(bp)
    return len(_BLUEPRINT_REGISTRY)


def _pending_unknowns_line(base_dir: Path | None = None) -> str:
    """One-line summary of unknown actions logged by extractions (learning loop).

    No-ops until the learning-log module exists (added in Phase 4); the hook keeps
    ``build_blueprint_context`` stable across phases.
    """
    try:
        from .learning_log import pending_unknowns_summary
    except ImportError:
        return ""
    return pending_unknowns_summary(base_dir)


def build_blueprint_context() -> str:
    lines: list[str] = [
        "## Workflow Blueprints",
        "",
        "Before building a workflow from scratch, check the following parameterized templates.",
        "Use a blueprint when one matches the user's intent; fall back to raw construction only when no blueprint fits.",
        "",
        "You can also turn an existing portal workflow into a reusable blueprint: extract it "
        "(hubspot_extract_workflow_blueprint), parameterize the draft (hubspot_parameterize_blueprint_draft), "
        "then promote it (hubspot_promote_blueprint_draft). Drafts are reviewed before they become usable.",
        "",
    ]
    for bp in list_blueprints():
        lines.append(f"- **{bp.name}** [{bp.origin}] — {bp.description}  [tags: {', '.join(bp.tags)}]")
        if bp.parameter_schema:
            lines.append("  Parameters:")
            for param_name, param_info in bp.parameter_schema.items():
                default = param_info.get("default")
                desc = param_info.get("description", "")
                req = "required" if param_info.get("required") else f"default: {default}"
                lines.append(f"    - {param_name} ({req}) — {desc}")
        lines.append("")

    drafts = list_drafts()
    if drafts:
        lines.append("### Pending draft blueprints (awaiting review — not yet usable)")
        for d in drafts:
            lines.append(f"- {d.name}")
        lines.append("")

    unknowns = _pending_unknowns_line()
    if unknowns:
        lines.append("### Pending unknown actions")
        lines.append(unknowns)
        lines.append("")

    return "\n".join(lines)


def _initial_population() -> None:
    # Packaged data only — a package resource with no Path.home() access, so this
    # is safe at import time and does not pull the developer's real user library
    # into the registry during tests. User blueprints merge in via reload_blueprints
    # / the tool layer, which read Path.home() under the caller's (test-isolated) context.
    for bp in load_packaged_blueprints():
        register_blueprint(bp)


_initial_population()