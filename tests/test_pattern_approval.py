"""Pattern approval, divergence-safe (spec 2026-07-22).

A ``--pattern`` write lets the human approve ONE transformation rule, then it
scales across the matched set with per-record compare-and-set so nothing that
drifted between approval and execute gets overwritten.  These tests exercise the
full tool path: ``handle_tool`` runs the eligibility gate and builds the rule
preview (capturing each record's pre-image), and ``handle_approve`` /
``execute_pending_write`` runs the compare-and-set executor.

``hubspot_mcp.handlers.invoke_tool`` is patched with a stateful fake so the
preview-time GET (pre-image capture) and the execute-time re-GET can return
different values — that is how drift is simulated.  No real HubSpot I/O.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from hubspot_mcp import audit
from hubspot_mcp.handlers import (
    HandlerError,
    execute_pending_write,
    handle_approve,
    handle_tool,
)
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.persistence import load as _load_pending
from hubspot_mcp.snapshot import load_undo_snapshot, snapshot_dir_for_portal


class _FakeClient:
    async def close(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hubspot_mcp.persistence.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    return tmp_path


def _portal():
    return PortalConfig(portal_id="123", token="test-token")


def _make_invoke(preview_props, execute_props, *, patch_errors=None, get_errors=None):
    """Stateful fake for ``handlers.invoke_tool``.

    ``state["phase"]`` starts at ``"preview"``; flip it to ``"execute"`` between
    ``handle_tool`` and ``handle_approve`` so the re-GET can return drifted values
    from ``execute_props``.  ``patch_errors``/``get_errors`` inject hard errors for
    specific ids to exercise continue-through.
    """
    patch_errors = set(patch_errors or ())
    get_errors = set(get_errors or ())
    state = {"phase": "preview"}
    calls = {"gets": [], "patches": []}

    async def fake_invoke(tool_name, portal_id, **kwargs):
        if tool_name == "hubspot_get_object":
            oid = str(kwargs["object_id"])
            calls["gets"].append((state["phase"], oid))
            if state["phase"] == "execute" and oid in get_errors:
                return {"error": "500 read boom", "tool": "hubspot_get_object"}
            src = preview_props if state["phase"] == "preview" else execute_props
            return {"id": oid, "properties": dict(src.get(oid, {}))}
        if tool_name == "hubspot_update_object":
            oid = str(kwargs["object_id"])
            props = dict(kwargs.get("properties", {}))
            if oid in patch_errors:
                return {"error": "409 conflict", "tool": "hubspot_update_object"}
            calls["patches"].append((oid, props))
            return {"id": oid, "properties": props}
        return {}

    return fake_invoke, state, calls


def _pattern_input(records, object_type="contacts"):
    return {"object_type": object_type, "records": records}


# --------------------------------------------------------------------------- #
# Eligibility (§4)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_pattern_rejected_for_destructive_delete(env):
    fake_invoke, _state, _calls = _make_invoke(
        {"c-1": {"firstname": "A"}}, {"c-1": {"firstname": "A"}}
    )
    with patch("hubspot_mcp.handlers.invoke_tool", side_effect=fake_invoke):
        out = await handle_tool(
            _FakeClient(),
            None,
            _portal(),
            {
                "tool_name": "hubspot_delete_object",
                "input": {"object_type": "contacts", "object_id": "c-1"},
                "batch_mode": "pattern",
            },
        )
    data = out["data"]
    assert data.get("pattern") is None
    assert "pattern_fallback" in data
    assert "destructive" in data["pattern_fallback"].lower()
    # Fell back to the normal per-op gate: a destructive delete is FULL_GATE.
    assert data["approval_tier"] == "FULL_GATE"


@pytest.mark.asyncio
async def test_pattern_rejected_for_non_update_tool(env):
    fake_invoke, _state, _calls = _make_invoke({}, {})
    with patch("hubspot_mcp.handlers.invoke_tool", side_effect=fake_invoke):
        out = await handle_tool(
            _FakeClient(),
            None,
            _portal(),
            {
                "tool_name": "hubspot_create_object",
                "input": {"object_type": "contacts", "properties": {"region": "west"}},
                "batch_mode": "pattern",
            },
        )
    data = out["data"]
    assert data.get("pattern") is None
    assert "pattern_fallback" in data
    assert "reversible property update" in data["pattern_fallback"]


@pytest.mark.asyncio
async def test_pattern_rejected_for_sensitive_field(env):
    # lifecyclestage is a default sensitive property → pattern mode not allowed.
    originals = {"c-1": {"lifecyclestage": ""}, "c-2": {"lifecyclestage": ""}}
    fake_invoke, _state, _calls = _make_invoke(originals, originals)
    with patch("hubspot_mcp.handlers.invoke_tool", side_effect=fake_invoke):
        out = await handle_tool(
            _FakeClient(),
            None,
            _portal(),
            {
                "tool_name": "hubspot_bulk_update_objects",
                "input": _pattern_input(
                    [
                        {"id": "c-1", "properties": {"lifecyclestage": "lead"}},
                        {"id": "c-2", "properties": {"lifecyclestage": "lead"}},
                    ]
                ),
                "batch_mode": "pattern",
            },
        )
    data = out["data"]
    assert data.get("pattern") is None
    assert "pattern_fallback" in data
    assert "sensitive" in data["pattern_fallback"].lower()
    assert "lifecyclestage" in data["pattern_fallback"]


@pytest.mark.asyncio
async def test_pattern_accepted_for_reversible_nonsensitive_update(env):
    originals = {
        "c-1": {"region": ""},
        "c-2": {"region": ""},
        "c-3": {"region": ""},
    }
    fake_invoke, _state, _calls = _make_invoke(originals, originals)
    with patch("hubspot_mcp.handlers.invoke_tool", side_effect=fake_invoke):
        out = await handle_tool(
            _FakeClient(),
            None,
            _portal(),
            {
                "tool_name": "hubspot_bulk_update_objects",
                "input": _pattern_input(
                    [{"id": rid, "properties": {"region": "west"}} for rid in originals],
                    object_type="contacts",
                ),
                "batch_mode": "pattern",
            },
        )
    data = out["data"]
    assert data["status"] == "preview"
    assert data["pattern_eligible"] is True
    assert data["approval_tier"] == "CONFIRM"  # under threshold → count-free approve
    assert data["pattern"]["count"] == 3
    assert data["pattern"]["rule"]["changes"] == {"region": "west"}
    # A rule + before/after sample (first pattern_sample_size), not N previews.
    assert len(data["pattern"]["sample"]) == 3
    assert data["pattern"]["sample"][0]["pre_image"] == {"region": ""}
    # Persisted pending record carries the divergence-safe metadata.
    pd = _load_pending("123", data["action_id"])
    assert pd["batch_mode"] == "pattern"
    assert pd["pattern_eligible"] is True
    assert len(pd["pattern"]["matched"]) == 3


# --------------------------------------------------------------------------- #
# Compare-and-set (§8.1)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_compare_and_set_skips_drifted_applies_unchanged(env):
    preview_props = {"c-1": {"region": ""}, "c-2": {"region": ""}}
    # c-1 drifted between approval and execute (someone set it to "east"); c-2 is
    # unchanged and still matches its pre-image.
    execute_props = {"c-1": {"region": "east"}, "c-2": {"region": ""}}
    fake_invoke, state, calls = _make_invoke(preview_props, execute_props)
    with patch("hubspot_mcp.handlers.invoke_tool", side_effect=fake_invoke):
        out = await handle_tool(
            _FakeClient(),
            None,
            _portal(),
            {
                "tool_name": "hubspot_bulk_update_objects",
                "input": _pattern_input(
                    [
                        {"id": "c-1", "properties": {"region": "west"}},
                        {"id": "c-2", "properties": {"region": "west"}},
                    ]
                ),
                "batch_mode": "pattern",
            },
        )
        action_id = out["data"]["action_id"]
        state["phase"] = "execute"
        approved = await handle_approve(_FakeClient(), None, _portal(), {"action_id": action_id})

    report = approved["data"]["pattern_report"]
    assert report["applied"] == ["c-2"]
    assert report["skipped_drifted"] == ["c-1"]
    assert report["failed"] == []
    # The drifted record's value was NEVER overwritten (no PATCH to c-1).
    patched_ids = [oid for oid, _props in calls["patches"]]
    assert patched_ids == ["c-2"]
    assert ("c-2", {"region": "west"}) in calls["patches"]


# --------------------------------------------------------------------------- #
# Over-threshold backstop (§8.3)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_over_threshold_requires_typed_count(env):
    # Set a low pattern_confirm_threshold so a small matched set trips the gate.
    (env / "123").mkdir()
    (env / "123" / "approval_policy.json").write_text(
        json.dumps({"pattern_confirm_threshold": 2})
    )
    originals = {f"c-{i}": {"region": ""} for i in range(1, 4)}  # 3 records > 2
    fake_invoke, state, calls = _make_invoke(originals, originals)
    with patch("hubspot_mcp.handlers.invoke_tool", side_effect=fake_invoke):
        out = await handle_tool(
            _FakeClient(),
            None,
            _portal(),
            {
                "tool_name": "hubspot_bulk_update_objects",
                "input": _pattern_input(
                    [{"id": rid, "properties": {"region": "west"}} for rid in originals]
                ),
                "batch_mode": "pattern",
            },
        )
        data = out["data"]
        assert data["approval_tier"] == "FULL_GATE"
        assert data["required_confirmation"] == 3
        assert data["requires_count"] is True
        action_id = data["action_id"]
        state["phase"] = "execute"
        # Bare approve is refused — the typed count is required.
        with pytest.raises(HandlerError) as exc:
            await handle_approve(_FakeClient(), None, _portal(), {"action_id": action_id})
        assert exc.value.error["kind"] == "validation"
        assert _load_pending("123", action_id) is not None  # still pending
        assert calls["patches"] == []  # nothing written
        # The typed count executes.
        approved = await handle_approve(
            _FakeClient(), None, _portal(), {"action_id": action_id, "confirm_count": 3}
        )
    report = approved["data"]["pattern_report"]
    assert sorted(report["applied"]) == ["c-1", "c-2", "c-3"]


@pytest.mark.asyncio
async def test_under_threshold_single_bare_approve(env):
    originals = {"c-1": {"region": ""}, "c-2": {"region": ""}}
    fake_invoke, state, calls = _make_invoke(originals, originals)
    with patch("hubspot_mcp.handlers.invoke_tool", side_effect=fake_invoke):
        out = await handle_tool(
            _FakeClient(),
            None,
            _portal(),
            {
                "tool_name": "hubspot_bulk_update_objects",
                "input": _pattern_input(
                    [{"id": rid, "properties": {"region": "west"}} for rid in originals]
                ),
                "batch_mode": "pattern",
            },
        )
        data = out["data"]
        assert data["approval_tier"] == "CONFIRM"
        assert data["requires_count"] is False
        action_id = data["action_id"]
        state["phase"] = "execute"
        approved = await handle_approve(_FakeClient(), None, _portal(), {"action_id": action_id})
    assert sorted(approved["data"]["pattern_report"]["applied"]) == ["c-1", "c-2"]


# --------------------------------------------------------------------------- #
# Continue-through + per-record undo/audit (§8.4, §8.5)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_continue_through_reports_applied_skipped_failed(env):
    ids = ["c-1", "c-2", "c-3", "c-4"]
    preview_props = {rid: {"region": ""} for rid in ids}
    # c-2 drifted; c-3 hard-errors on write; c-1 and c-4 are clean.
    execute_props = dict(preview_props)
    execute_props["c-2"] = {"region": "east"}  # drift
    fake_invoke, state, calls = _make_invoke(
        preview_props, execute_props, patch_errors={"c-3"}
    )
    with patch("hubspot_mcp.handlers.invoke_tool", side_effect=fake_invoke):
        out = await handle_tool(
            _FakeClient(),
            None,
            _portal(),
            {
                "tool_name": "hubspot_bulk_update_objects",
                "input": _pattern_input(
                    [{"id": rid, "properties": {"region": "west"}} for rid in ids]
                ),
                "batch_mode": "pattern",
            },
        )
        action_id = out["data"]["action_id"]
        state["phase"] = "execute"
        approved = await handle_approve(_FakeClient(), None, _portal(), {"action_id": action_id})

    report = approved["data"]["pattern_report"]
    assert sorted(report["applied"]) == ["c-1", "c-4"]
    assert report["skipped_drifted"] == ["c-2"]
    assert [f["id"] for f in report["failed"]] == ["c-3"]
    assert report["counts"] == {"matched": 4, "applied": 2, "skipped_drifted": 1, "failed": 1}
    assert report["undo_command"] == f"hubspot undo {action_id}"
    # Never hides a partial: all three buckets enumerated.
    assert "409 conflict" in report["failed"][0]["error"]

    # Per-record undo snapshot: the batch snapshot holds one pre-image entry per
    # APPLIED record only (drifted/failed excluded), so undo restores exactly what
    # changed.
    snap = load_undo_snapshot(snapshot_dir_for_portal("123"), action_id)
    assert set(snap["original_values"].keys()) == {"c-1", "c-4"}
    assert snap["original_values"]["c-1"] == {"region": ""}
    assert snap["metadata"]["undoable"] is True
    assert snap["metadata"]["intent_type"] == "update"

    # Per-record audit entry for each applied record (identified by the
    # un-redacted record_id in result_summary; the action string embeds the
    # action_id, which the on-disk audit redactor may mask if it is all digits).
    entries = audit.get_recent_audits("123", limit=20)
    pattern_audits = [e for e in entries if e.get("result_summary", {}).get("pattern")]
    assert {e["result_summary"]["record_id"] for e in pattern_audits} == {"c-1", "c-4"}
    # No audit for skipped/failed records.
    assert "c-2" not in {e["result_summary"]["record_id"] for e in pattern_audits}
    assert "c-3" not in {e["result_summary"]["record_id"] for e in pattern_audits}

    # Pending cleared after execute.
    assert _load_pending("123", action_id) is None


@pytest.mark.asyncio
async def test_continue_through_survives_reread_error(env):
    ids = ["c-1", "c-2"]
    props = {rid: {"region": ""} for rid in ids}
    fake_invoke, state, calls = _make_invoke(props, props, get_errors={"c-1"})
    with patch("hubspot_mcp.handlers.invoke_tool", side_effect=fake_invoke):
        out = await handle_tool(
            _FakeClient(),
            None,
            _portal(),
            {
                "tool_name": "hubspot_bulk_update_objects",
                "input": _pattern_input(
                    [{"id": rid, "properties": {"region": "west"}} for rid in ids]
                ),
                "batch_mode": "pattern",
            },
        )
        action_id = out["data"]["action_id"]
        state["phase"] = "execute"
        approved = await handle_approve(_FakeClient(), None, _portal(), {"action_id": action_id})
    report = approved["data"]["pattern_report"]
    assert report["applied"] == ["c-2"]
    assert [f["id"] for f in report["failed"]] == ["c-1"]
    # c-1 re-read failed → never written; c-2 applied.
    assert [oid for oid, _ in calls["patches"]] == ["c-2"]


# --------------------------------------------------------------------------- #
# Undo restores the applied set (§8.4)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_undo_restores_applied_set(env, monkeypatch):
    # Upstream drives undo through cli._undo_action; this port hosts the same
    # logic in handlers.undo_action so every write-safety decision stays in one
    # module. The env fixture already redirects CONFIG_DIR.
    from hubspot_mcp.handlers import undo_action

    preview_props = {"c-1": {"region": "old1"}, "c-2": {"region": "old2"}}
    fake_invoke, state, calls = _make_invoke(preview_props, preview_props)
    with patch("hubspot_mcp.handlers.invoke_tool", side_effect=fake_invoke):
        out = await handle_tool(
            _FakeClient(),
            None,
            _portal(),
            {
                "tool_name": "hubspot_bulk_update_objects",
                "input": _pattern_input(
                    [{"id": rid, "properties": {"region": "west"}} for rid in preview_props]
                ),
                "batch_mode": "pattern",
            },
        )
        action_id = out["data"]["action_id"]
        state["phase"] = "execute"
        await handle_approve(_FakeClient(), None, _portal(), {"action_id": action_id})

    snap = load_undo_snapshot(snapshot_dir_for_portal("123"), action_id)
    restore_calls = []

    async def fake_restore_invoke(tool_name, portal_id, **kwargs):
        if tool_name == "hubspot_update_object":
            restore_calls.append((str(kwargs["object_id"]), dict(kwargs.get("properties", {}))))
            return {"id": kwargs["object_id"], "properties": kwargs.get("properties", {})}
        return {}

    with patch("hubspot_mcp.handlers.invoke_tool", side_effect=fake_restore_invoke):
        succeeded, message = await undo_action(
            snap, "123", _portal(), client=_FakeClient()
        )

    assert succeeded is True
    # Each applied record restored to its captured pre-image.
    restored = dict(restore_calls)
    assert restored["c-1"] == {"region": "old1"}
    assert restored["c-2"] == {"region": "old2"}


# --------------------------------------------------------------------------- #
# Loop-originated writes are unaffected (§8.6)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_loop_step_never_uses_pattern(env):
    originals = {"c-1": {"region": ""}, "c-2": {"region": ""}}
    fake_invoke, _state, _calls = _make_invoke(originals, originals)
    with patch("hubspot_mcp.handlers.invoke_tool", side_effect=fake_invoke):
        out = await handle_tool(
            _FakeClient(),
            None,
            _portal(),
            {
                "tool_name": "hubspot_bulk_update_objects",
                "input": _pattern_input(
                    [{"id": rid, "properties": {"region": "west"}} for rid in originals]
                ),
                "batch_mode": "pattern",
                "loop_step_number": 2,
            },
        )
    data = out["data"]
    # A loop step never enters pattern mode; it pauses at the write like any other.
    assert data.get("pattern") is None
    assert data["status"] == "preview"
    pd = _load_pending("123", data["action_id"])
    assert "pattern" not in pd
