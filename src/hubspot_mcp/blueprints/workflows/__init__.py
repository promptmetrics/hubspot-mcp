from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class WorkflowBlueprint:
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    parameter_schema: dict[str, Any] = field(default_factory=dict)
    build: Callable[[dict[str, Any]], dict[str, Any]] | None = None


_BLUEPRINT_REGISTRY: dict[str, WorkflowBlueprint] = {}


def register_blueprint(blueprint: WorkflowBlueprint) -> WorkflowBlueprint:
    _BLUEPRINT_REGISTRY[blueprint.name] = blueprint
    return blueprint


def list_blueprints() -> list[WorkflowBlueprint]:
    return list(_BLUEPRINT_REGISTRY.values())


def get_blueprint(name: str) -> WorkflowBlueprint | None:
    return _BLUEPRINT_REGISTRY.get(name)


def build_blueprint_context() -> str:
    lines: list[str] = [
        "## Workflow Blueprints",
        "",
        "Before building a workflow from scratch, check the following parameterized templates.",
        "Use a blueprint when one matches the user's intent; fall back to raw construction only when no blueprint fits.",
        "",
    ]
    for bp in list_blueprints():
        lines.append(f"- **{bp.name}** — {bp.description}  [tags: {', '.join(bp.tags)}]")
        if bp.parameter_schema:
            lines.append("  Parameters:")
            for param_name, param_info in bp.parameter_schema.items():
                default = param_info.get("default")
                desc = param_info.get("description", "")
                req = "required" if param_info.get("required") else f"default: {default}"
                lines.append(f'    - {param_name} ({req}) — {desc}')
        lines.append("")
    return "\n".join(lines)
