from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.models import PreviewResult, TaskIntent

PreviewBuilder = Callable[[str, TaskIntent, HubSpotClient, str], Awaitable[PreviewResult]]
ExecuteDispatch = Callable[[str, TaskIntent, str, HubSpotClient, str, dict[str, Any] | None], Awaitable[dict[str, Any]]]
ReconcileDispatch = Callable[[str, TaskIntent, str, HubSpotClient, str, dict[str, Any]], Awaitable[dict[str, Any]]]

_PREVIEW_BUILDERS: dict[str, PreviewBuilder] = {}
_EXECUTE_DISPATCH: dict[str, ExecuteDispatch] = {}
_RECONCILE_DISPATCH: dict[str, ReconcileDispatch] = {}


def register_preview(agent_name: str) -> Callable[[PreviewBuilder], PreviewBuilder]:
    """Decorator to register a preview builder for an agent."""
    def decorator(fn: PreviewBuilder) -> PreviewBuilder:
        _PREVIEW_BUILDERS[agent_name] = fn
        return fn
    return decorator


def register_execute(agent_name: str) -> Callable[[ExecuteDispatch], ExecuteDispatch]:
    """Decorator to register an execute handler for an agent."""
    def decorator(fn: ExecuteDispatch) -> ExecuteDispatch:
        _EXECUTE_DISPATCH[agent_name] = fn
        return fn
    return decorator


def register_reconcile(agent_name: str) -> Callable[[ReconcileDispatch], ReconcileDispatch]:
    """Decorator to register a reconcile handler for an agent."""
    def decorator(fn: ReconcileDispatch) -> ReconcileDispatch:
        _RECONCILE_DISPATCH[agent_name] = fn
        return fn
    return decorator


def get_preview_builder(agent_name: str) -> PreviewBuilder | None:
    return _PREVIEW_BUILDERS.get(agent_name)


def get_execute_dispatch(agent_name: str) -> ExecuteDispatch | None:
    return _EXECUTE_DISPATCH.get(agent_name)


def get_reconcile_dispatch(agent_name: str) -> ReconcileDispatch | None:
    return _RECONCILE_DISPATCH.get(agent_name)


def list_preview_agents() -> list[str]:
    return list(_PREVIEW_BUILDERS.keys())


def list_execute_agents() -> list[str]:
    return list(_EXECUTE_DISPATCH.keys())


def list_reconcile_agents() -> list[str]:
    return list(_RECONCILE_DISPATCH.keys())
