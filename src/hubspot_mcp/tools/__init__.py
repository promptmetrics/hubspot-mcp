from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

@dataclass
class ToolDef:
    name: str
    description: str
    func: Callable[..., Any]
    is_async: bool


registry: dict[str, ToolDef] = {}


def tool(name: str, description: str) -> Callable[[Callable], Callable]:
    def decorator(func: Callable) -> Callable:
        registry[name] = ToolDef(
            name=name,
            description=description,
            func=func,
            is_async=inspect.iscoroutinefunction(func),
        )
        return func
    return decorator


def get_tool(name: str) -> ToolDef | None:
    return registry.get(name)


def list_tools() -> list[ToolDef]:
    return list(registry.values())


async def invoke_tool(tool_name: str, portal_id: str, **kwargs: Any) -> Any:
    tool_def = get_tool(tool_name)
    if tool_def is None:
        raise ValueError(f"Unknown tool: {tool_name}")

    kwargs["portal_id"] = portal_id
    if tool_def.is_async:
        result = await tool_def.func(**kwargs)
    else:
        result = tool_def.func(**kwargs)

    return result


def _import_all_submodules() -> None:
    """Import every submodule in this package so its ``@tool`` decorators run.

    The registry is populated by decorator side effects at import time.  Any
    process that imports ``hubspot_mcp.tools`` without also importing the
    tool submodules sees an empty registry — notably the warm-client daemon
    subprocess (``python -m hubspot_mcp.daemon`` → ``handlers`` → this
    package, but never ``agents/*``), which therefore rejected every tool call
    with ``Unknown tool``.  Walking the package here makes a populated registry
    an invariant of importing ``hubspot_mcp.tools``, regardless of which
    entrypoint did the import.  Submodules only need the ``tool`` decorator
    (defined above) plus leaf modules (client/cache/models/…), none of which
    import this package back, so there is no import cycle.
    """
    import importlib
    import pkgutil
    from pathlib import Path

    pkg_dir = Path(__file__).resolve().parent
    for _mod in pkgutil.iter_modules([str(pkg_dir)]):
        importlib.import_module(f"{__name__}.{_mod.name}")


_import_all_submodules()
