"""Phase 1 smoke tests — fast, no network, no real HubSpot portal.

Verifies the pieces that make the MCP server bootable end-to-end:
package imports, the 76-domain + 5-safety tool registration, ``ctx`` stays out
of tool schemas, auth providers fail closed on unknown portals, scope
resolution, and the StateStore seam. Live HubSpot calls are out of scope here
(they need a real portal + credentials) and are exercised manually via the
stdio handshake + ``hubspot-mcp auth login``.
"""
from __future__ import annotations

import pytest

from hubspot_mcp.auth import EnvTokenProvider, OAuthProvider, TokenProvider
from hubspot_mcp.auth.base import NotAuthenticatedError
from hubspot_mcp.auth.oauth_provider import _resolve_scopes
from hubspot_mcp.handlers import HANDLERS
from hubspot_mcp.state import FileStateStore, StateStore
from hubspot_mcp.tools import list_tools


def test_package_imports():
    import hubspot_mcp  # noqa: F401

    assert hubspot_mcp.__version__ == "0.1.1"


def test_registry_has_76_tools():
    assert len(list_tools()) == 79


def test_handlers_trimmed_to_tools_only():
    assert sorted(HANDLERS) == ["approve", "reject", "tool"]


async def test_server_registers_81_tools():
    from hubspot_mcp import server

    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    assert len(tools) == 86  # 79 domain + 7 safety/status/route
    for safety in (
        "hubspot_approve_write",
        "hubspot_reject_write",
        "hubspot_list_pending_writes",
        "hubspot_list_recent_audit",
        "hubspot_undo_write",
        "hubspot_status",
        "hubspot_route",
    ):
        assert safety in names
    assert "hubspot_get_object" in names


async def test_ctx_excluded_from_tool_schemas():
    """Pins the context-injection seam.

    The SDK resolves the ctx parameter from ``__annotations__`` but builds the
    schema from ``__signature__``; ``_make_domain_wrapper`` synthesises both, so
    setting only the signature silently leaks ``ctx`` into all 76 schemas.
    """
    from hubspot_mcp import server

    tools = {t.name: t for t in await server.mcp.list_tools()}
    # a domain tool: ctx must not appear in its JSON-schema properties
    props = tools["hubspot_get_object"].input_schema.get("properties", {})
    assert "ctx" not in props
    # ``properties`` scopes the fetch to named fields — required so preview
    # snapshots capture only the properties a write touches, rather than the
    # whole record (which drags read-only fields into undo replay).
    assert set(props) == {"object_id", "object_type", "properties"}
    # a safety tool: same
    assert "ctx" not in tools["hubspot_approve_write"].input_schema.get("properties", {})
    # the Callable-injection param must be dropped from hubspot_docs_search
    docs_props = tools["hubspot_docs_search"].input_schema.get("properties", {})
    assert "search_backend" not in docs_props
    assert "query" in docs_props


@pytest.mark.asyncio
async def test_env_provider_fails_closed_on_unknown_portal():
    with pytest.raises(NotAuthenticatedError):
        await EnvTokenProvider().resolve("999999")


@pytest.mark.asyncio
async def test_oauth_provider_fails_closed_on_unknown_portal():
    with pytest.raises(NotAuthenticatedError):
        await OAuthProvider().resolve("999999")


def test_provider_modes():
    assert EnvTokenProvider().mode == "token"
    assert OAuthProvider().mode == "oauth"
    assert isinstance(EnvTokenProvider(), TokenProvider)
    assert isinstance(OAuthProvider(), TokenProvider)


def test_scope_resolution_from_env(monkeypatch):
    monkeypatch.setenv("HUBSPOT_SCOPES", "crm.objects.contacts.read, crm.objects.contacts.write")
    assert _resolve_scopes(None) == ["crm.objects.contacts.read", "crm.objects.contacts.write"]


def test_scope_resolution_explicit_wins(monkeypatch):
    monkeypatch.setenv("HUBSPOT_SCOPES", "crm.objects.contacts.read")
    assert _resolve_scopes(["crm.deals.read", "crm.deals.write"]) == ["crm.deals.read", "crm.deals.write"]


def test_scope_resolution_raises_when_missing(monkeypatch):
    monkeypatch.delenv("HUBSPOT_SCOPES", raising=False)
    with pytest.raises(ValueError):
        _resolve_scopes(None)


def test_file_state_store_is_state_store():
    store = FileStateStore()
    assert isinstance(store, StateStore)
    # all abstract methods implemented
    for name in (
        "store_pending", "load_pending", "confirm_pending", "clear_pending", "list_pending",
        "save_undo_snapshot_for_action", "load_undo_snapshot", "update_undo_snapshot", "delete_undo_snapshot",
        "log_write", "get_recent_audits",
    ):
        assert callable(getattr(store, name))