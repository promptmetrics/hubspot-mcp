"""Regression tests for the HITL write gate (Phase 0).

``handlers._is_write_tool`` originally classified a tool as a write purely by
``.write``/``.delete`` scope suffix.  Thirteen tools emit no such scope — the
five workflow writes (bare ``automation``), the single-scope
``forms``/``reports`` writes, the ``set()``-registered refund/import/export, and
``hubspot_raw_api`` — so they reached HubSpot with no preview, no approval, no
undo snapshot and no audit entry.

These tests pin the fix: every mutating tool must return a ``preview`` with an
``action_id`` and must issue no HTTP request while doing so.
"""
from __future__ import annotations

import pytest

from hubspot_mcp import config, persistence
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.handlers import _is_write_tool, _tool_intent_type, _tool_risk_level, handle_tool
from hubspot_mcp.models import RiskLevel
from hubspot_mcp.scope_registry import RAW_API_WRITE_METHODS, WRITE_TOOLS, get_required_scopes

PORTAL_ID = "99999999"

# Every tool whose registry scope set carries no .write/.delete suffix but which
# mutates portal state. Paired with a minimal valid input for each.
UNSUFFIXED_WRITE_TOOLS = [
    ("hubspot_create_workflow", {"name": "wf", "flow": {}}),
    ("hubspot_update_workflow", {"workflow_id": "1", "updates": {}}),
    ("hubspot_enroll_workflow", {"workflow_id": "1", "object_ids": ["1"]}),
    ("hubspot_toggle_workflow", {"workflow_id": "1", "enabled": False}),
    ("hubspot_create_workflow_from_blueprint", {"blueprint_name": "welcome_email"}),
    ("hubspot_create_refund", {"payment_id": "1", "amount": 1.0}),
    ("hubspot_import_data", {"file_path": "/tmp/x.csv", "import_request": {}}),
    ("hubspot_export_data", {"export_request": {}}),
    ("hubspot_create_form", {"form_definition": {}}),
    ("hubspot_create_report", {"report_definition": {}}),
    ("hubspot_create_dashboard", {"dashboard_definition": {}}),
    ("hubspot_schedule_email", {"schedule_definition": {}}),
]


class ExplodingClient:
    """Any HTTP call through this client fails the test.

    A gated write must build its preview and persist it without touching
    HubSpot; reaching the network here means the gate was bypassed.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def _explode(self, method: str):
        async def _call(path: str, *a, **kw):
            self.calls.append((method, path))
            raise AssertionError(f"HTTP {method} {path} issued before approval — write gate bypassed")

        return _call

    def __getattr__(self, name: str):
        if name in ("get", "post", "patch", "put", "delete", "post_files"):
            return self._explode(name.upper())
        raise AttributeError(name)


@pytest.fixture
def portal(tmp_path, monkeypatch):
    """Root all on-disk state under tmp_path.

    ``persistence`` does ``from hubspot_mcp.config import CONFIG_DIR``, binding
    the value at import, so patching ``config.CONFIG_DIR`` alone is not enough —
    the module attribute must be patched too.
    """
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(persistence, "CONFIG_DIR", tmp_path)
    # scopes_granted empty → _check_tool_scope returns early, isolating the gate.
    return PortalConfig(portal_id=PORTAL_ID, token="tok", scopes_granted=[])


@pytest.mark.parametrize("tool_name,tool_input", UNSUFFIXED_WRITE_TOOLS)
async def test_unsuffixed_write_tools_are_gated(tool_name, tool_input, portal):
    client = ExplodingClient()
    result = await handle_tool(client, None, portal, {"tool_name": tool_name, "input": tool_input})

    data = result["data"]
    assert data["status"] == "preview", f"{tool_name} executed instead of previewing"
    assert data["action_id"], f"{tool_name} preview carries no action_id"
    assert client.calls == []


@pytest.mark.parametrize("method", sorted(RAW_API_WRITE_METHODS))
async def test_raw_api_mutating_verbs_are_gated(method, portal):
    client = ExplodingClient()
    result = await handle_tool(
        client,
        None,
        portal,
        {"tool_name": "hubspot_raw_api", "input": {"method": method, "path": "/crm/v3/objects/contacts/1"}},
    )

    data = result["data"]
    assert data["status"] == "preview"
    assert data["action_id"]
    assert client.calls == []


async def test_raw_api_get_still_reads_directly(portal):
    """A GET through raw_api is a read and must not be gated."""
    client = ExplodingClient()
    with pytest.raises(AssertionError, match="HTTP GET"):
        await handle_tool(
            client,
            None,
            portal,
            {"tool_name": "hubspot_raw_api", "input": {"method": "GET", "path": "/crm/v3/objects/contacts/1"}},
        )


def test_raw_api_delete_is_destructive():
    """The destructive count gate must fire for raw_api DELETE despite empty scopes."""
    assert _tool_risk_level(set(), "hubspot_raw_api", {"method": "DELETE"}) is RiskLevel.DESTRUCTIVE
    assert _tool_intent_type("hubspot_raw_api", {"method": "DELETE"}) == "delete"
    assert _tool_risk_level(set(), "hubspot_raw_api", {"method": "POST"}) is RiskLevel.MEDIUM
    assert _tool_intent_type("hubspot_raw_api", {"method": "POST"}) == "write"


def test_write_tools_set_covers_every_unsuffixed_write():
    """WRITE_TOOLS must list exactly the mutating tools the scope suffix misses."""
    assert {name for name, _ in UNSUFFIXED_WRITE_TOOLS} == WRITE_TOOLS


def test_write_tools_entries_genuinely_lack_a_write_suffix():
    """Guard against a future scope-registry change silently making an entry redundant."""
    for tool_name in WRITE_TOOLS:
        scopes = get_required_scopes([tool_name])
        assert not any(
            s.endswith((".write", ".delete")) for s in scopes
        ), f"{tool_name} now has a write-suffixed scope; the WRITE_TOOLS entry may be stale"
        assert _is_write_tool(scopes, tool_name, {}), f"{tool_name} is not classified as a write"


class RecordingClient:
    """Captures calls and returns a canned 200 — for the post-approval half."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def _record(self, method: str):
        async def _call(path: str, *a, **kw):
            from hubspot_mcp.client import APIResponse

            self.calls.append((method, path))
            return APIResponse(status_code=200, body={"id": "42"}, headers={})

        return _call

    def __getattr__(self, name: str):
        if name in ("get", "post", "patch", "put", "delete", "post_files"):
            return self._record(name.upper())
        raise AttributeError(name)

    async def close(self) -> None:
        pass


async def test_gated_write_still_executes_after_approval(portal):
    """Gating must defer the write, not break it: approve → the call goes through.

    Guards against trading a bypass for a dead tool.
    """
    from hubspot_mcp.handlers import ExecuteError, execute_pending_write

    gate_client = ExplodingClient()
    preview = await handle_tool(
        gate_client,
        None,
        portal,
        {"tool_name": "hubspot_raw_api", "input": {"method": "POST", "path": "/crm/v3/objects/contacts"}},
    )
    action_id = preview["data"]["action_id"]
    assert gate_client.calls == []

    # A raw_api POST has no undo path, so Bounded Autonomy classifies it
    # FULL_GATE: approval requires the exact impact count, not a bare approve.
    assert preview["data"]["approval_tier"] == "FULL_GATE"
    assert preview["data"]["requires_count"] is True

    exec_client = RecordingClient()
    with pytest.raises(ExecuteError, match="exact impact count"):
        await execute_pending_write(portal, action_id, client=exec_client)
    assert exec_client.calls == [], "write executed without the required count"

    result = await execute_pending_write(portal, action_id, confirm_count=1, client=exec_client)

    assert exec_client.calls == [("POST", "/crm/v3/objects/contacts")]
    assert result.data["status"] == "success"


# --- OAuth state path traversal (upstream M1, 457237b) ----------------------

class TestOAuthStateTraversal:
    """The OAuth ``state`` returns verbatim from the callback URL, so it is
    attacker-influenceable and must be validated before it builds a path.
    ``../<portal_id>`` resolved to the portal's stored token file, which
    ``_clear_oauth_state`` unlinks — a forced-re-auth denial of service."""

    @staticmethod
    def _isolated(tmp_path, monkeypatch):
        from hubspot_mcp import oauth_flow

        monkeypatch.setattr(oauth_flow, "CONFIG_DIR", tmp_path)
        return oauth_flow

    def test_crafted_state_cannot_delete_the_portal_token(self, tmp_path, monkeypatch):
        oauth_flow = self._isolated(tmp_path, monkeypatch)
        victim = tmp_path / "99999999.json"
        victim.write_text('{"token": "portal-token"}')

        oauth_flow._clear_oauth_state("../99999999")

        assert victim.exists(), "crafted OAuth state deleted the portal token file"

    def test_crafted_state_reads_nothing(self, tmp_path, monkeypatch):
        oauth_flow = self._isolated(tmp_path, monkeypatch)
        (tmp_path / "99999999.json").write_text('{"expires_at": 99999999999}')

        assert oauth_flow._load_oauth_state("../99999999") is None

    def test_state_file_rejects_separators_and_dot_segments(self, tmp_path, monkeypatch):
        import pytest

        oauth_flow = self._isolated(tmp_path, monkeypatch)
        for bad in ("../evil", "a/b", "", "x" * 129, "nul\x00byte"):
            with pytest.raises(ValueError, match="Invalid OAuth state"):
                oauth_flow._oauth_state_file(bad)

    def test_legitimate_state_still_resolves(self, tmp_path, monkeypatch):
        oauth_flow = self._isolated(tmp_path, monkeypatch)
        assert oauth_flow._oauth_state_file("aB3-_x").name == "aB3-_x.json"
