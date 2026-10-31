"""The intent write path: preview a changeset, apply it once, undo it.

Pins the safety promises the intent surface makes in place of the ``/mcp`` write
gate: nothing reaches HubSpot before apply, apply refuses a wrong count and
replays idempotently, a record someone changed since the preview is skipped
rather than overwritten, partial failure is reported rather than raised, undo
restores exactly what apply changed, and the old ``hubspot_approve_write`` and
``hubspot_undo_write`` tools can never execute or revert a changeset.
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx
import pytest
from intent_fakes import PORTAL, FakeHubSpot
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from hubspot_mcp.config import PortalConfig
from hubspot_mcp.errors import HubSpotError, RateLimitError
from hubspot_mcp.intent import changes, common
from hubspot_mcp.state import get_store, set_store


@pytest.fixture
def http():
    return FakeHubSpot()


@pytest.fixture(params=["file", "redis"])
async def env(request, tmp_path, monkeypatch, http):
    """Every test runs over the local file store and over Redis (fakeredis): hosted mode must behave the same."""
    from hubspot_mcp import config, persistence, server

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(persistence, "CONFIG_DIR", tmp_path)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("HUBSPOT_MCP_STATE_BACKEND", "file")
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    monkeypatch.setenv("HUBSPOT_MCP_RULES_DIR", str(rules_dir))
    set_store(None)
    fake_redis = None
    if request.param == "redis":
        import fakeredis.aioredis
        from cryptography.fernet import Fernet

        from hubspot_mcp.state.redis_store import RedisStateStore

        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
        set_store(RedisStateStore(fake_redis, encryption_key=Fernet.generate_key().decode()))
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(common, "_sleep", fake_sleep)
    lifespan = {
        "client": http,
        "cache": None,
        "portal_config": PortalConfig(portal_id=PORTAL, token="t", scopes_granted=[]),
        "portal_id": PORTAL,
        "auth_error": None,
    }
    monkeypatch.setattr(server, "_lifespan", lambda ctx: lifespan)
    monkeypatch.setattr(server, "_session_resolver", None)
    yield {"lf": lifespan, "rules_dir": rules_dir, "sleeps": sleeps, "server": server}
    set_store(None)
    if fake_redis is not None:
        await fake_redis.aclose()


@pytest.fixture
def lf(env):
    return env["lf"]


def _update(record_id, **props):
    return {"op": "update", "object_type": "deals", "id": str(record_id), "properties": props, "summary": f"Update deal {record_id}"}


def _seed_deals(http, n=2):
    for i in range(1, n + 1):
        http.add("deals", str(i), closedate="2026-09-01", amount="1000", dealname=f"Acme Corp deal {i}")


async def _propose(lf, ops, title="Fix close dates", **kw):
    return await changes.propose(lf, title=title, ops=ops, **kw)


# --- propose ---------------------------------------------------------------


async def test_propose_stores_the_changeset_and_writes_nothing(lf, http):
    _seed_deals(http)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30"), _update(2, closedate="2026-11-30")])

    assert preview["status"] == "preview"
    assert re.fullmatch(r"chg_[a-z]{12}", preview["confirmation_id"]), "letters only, so audit redaction cannot rewrite it"
    assert preview["change_count"] == 2
    assert preview["changes"] == ["Update deal 1", "Update deal 2"]
    assert preview["by_kind"] == {"update deals": 2}
    assert "expected_count=2" in preview["next"] and preview["confirmation_id"] in preview["next"]
    assert http.writes() == [], "a preview must not write to HubSpot"
    assert http.paths("POST") == ["/crm/v3/objects/deals/batch/read"], "only a read to capture current values"

    stored = await get_store().load_pending(PORTAL, preview["confirmation_id"])
    assert stored["kind"] == "intent_changeset" and stored["status"] == "pending"
    assert stored["original_values"] == {"deals:1": {"closedate": "2026-09-01"}, "deals:2": {"closedate": "2026-09-01"}}
    for forbidden in ("tool_name", "agent_name", "intent", "proposed_payload", "approval_tier", "required_confirmation"):
        assert forbidden not in stored, f"{forbidden} would let hubspot_approve_write execute it"


async def test_propose_uses_caller_supplied_original_values_and_reads_only_the_rest(lf, http):
    _seed_deals(http)
    await _propose(
        lf,
        [_update(1, closedate="2026-11-30"), _update(2, closedate="2026-11-30")],
        original_values={"deals:1": {"closedate": "2026-09-01"}},
    )
    read = [body for m, p, body in http.calls if p.endswith("/batch/read")]
    assert len(read) == 1 and read[0]["inputs"] == [{"id": "2"}]


async def test_propose_refuses_an_update_to_a_record_that_does_not_exist(lf, http):
    with pytest.raises(ToolError, match="deals 404 was not found"):
        await _propose(lf, [_update(404, closedate="2026-11-30")])


async def test_a_long_changeset_previews_a_sample_and_a_breakdown(lf, http):
    _seed_deals(http, 40)
    ops = [_update(i, closedate="2026-11-30") for i in range(1, 41)]
    preview = await _propose(lf, ops)
    assert preview["change_count"] == 40
    assert len(preview["changes"]) == 10 and preview["sample_only"] is True and preview["omitted"] == 30


@pytest.mark.parametrize(
    ("ops", "needle"),
    [
        ([], "at least one op"),
        ([{"op": "delete", "object_type": "deals", "id": "1"}], "op must be"),
        ([{"op": "update", "object_type": "deals", "id": "1", "properties": {}}], "must not be empty"),
        ([{"op": "update", "object_type": "deals", "properties": {"a": "b"}}], "needs the record id"),
        ([{"op": "update", "object_type": "deals", "id": "1", "properties": {"hs_object_id": "9"}}], "read-only"),
        ([{"op": "update", "object_type": "deals", "id": "1", "properties": {"a": {"nested": 1}}}], "text, numbers or booleans"),
        ([_update(1, a="b"), _update(1, c="d")], "updated twice"),
        ([{"op": "create", "object_type": "tasks", "properties": {"a": "b"}, "associations": [{"to": {"ref": "ghost"}}]}], "matches no create op"),
        (
            [
                {"op": "create", "object_type": "tasks", "ref": "t", "properties": {"a": "b"}},
                {"op": "create", "object_type": "notes", "ref": "t", "properties": {"a": "b"}},
            ],
            "used twice",
        ),
        ([{"op": "associate", "from": {"object_type": "deals", "id": "1"}, "to": {"object_type": "contacts"}}], "needs an id"),
        (
            [{"op": "associate", "from": {"object_type": "deals", "id": "1"}, "to": {"object_type": "contacts", "id": "2"}, "type": "bogus"}],
            "association type",
        ),
        ([{"op": "update", "object_type": "../deals", "id": "1", "properties": {"a": "b"}}], "object_type"),
        ([{"op": "create", "object_type": "tasks", "properties": {"a": "b"}, "depends_on": 0}], "earlier update op"),
        ([_update(1, a="b"), {"op": "create", "object_type": "tasks", "properties": {"a": "b"}, "depends_on": 5}], "earlier update op"),
        ([_update(1, a="b"), {"op": "create", "object_type": "tasks", "properties": {"a": "b"}, "depends_on": True}], "earlier update op"),
        (
            [
                {"op": "create", "object_type": "notes", "properties": {"a": "b"}},
                {"op": "create", "object_type": "tasks", "properties": {"a": "b"}, "depends_on": 0},
            ],
            "not an update",
        ),
    ],
)
async def test_invalid_ops_are_refused_with_a_message_naming_the_problem(lf, http, ops, needle):
    with pytest.raises(ToolError, match=needle):
        await _propose(lf, ops)
    assert http.calls == [], "validation happens before any HubSpot call"


async def test_op_count_is_capped(lf, http, monkeypatch):
    monkeypatch.setattr(changes, "MAX_OPS", 3)
    with pytest.raises(ToolError, match="at most 3 ops"):
        await _propose(lf, [_update(i, a="b") for i in range(4)])


async def test_propose_requires_a_title(lf, http):
    with pytest.raises(ToolError, match="title"):
        await _propose(lf, [_update(1, a="b")], title="  ")


async def test_propose_from_ops_accepts_the_loose_shape(lf, http):
    _seed_deals(http, 1)
    preview = await changes.propose_from_ops(
        lf,
        title="Mixed",
        changes=[
            {"op": "update", "object_type": "deals", "id": "1", "properties": {"amount": "2000"}},
            {"op": "create", "object_type": "tasks", "properties": {"hs_task_subject": "Follow up"}, "associate_to": [{"object_type": "deals", "id": "1"}]},
            {"op": "associate", "object_type": "deals", "id": "1", "associate_to": [{"object_type": "contacts", "id": "7"}, {"object_type": "contacts", "id": "8"}]},
        ],
    )
    assert preview["change_count"] == 4
    stored = await get_store().load_pending(PORTAL, preview["confirmation_id"])
    kinds = [op["op"] for op in stored["ops"]]
    assert kinds == ["update", "create", "associate", "associate"]
    assert stored["ops"][1]["associations"] == [{"to": {"object_type": "deals", "id": "1"}, "type": "default"}]
    assert stored["original_values"]["deals:1"] == {"amount": "1000"}


# --- apply -----------------------------------------------------------------


async def test_apply_writes_updates_creates_and_associations_and_reports_counts(lf, http):
    _seed_deals(http, 1)
    http.add("contacts", "10", firstname="Jane")
    ops = [
        _update(1, closedate="2026-11-30", amount="2000"),
        {
            "op": "create",
            "object_type": "contacts",
            "ref": "new_contact",
            "properties": {"firstname": "Sam", "lastname": "Lee"},
            "associations": [{"to": {"object_type": "deals", "id": "1"}}],
            "summary": "Create contact Sam Lee",
        },
        {
            "op": "create",
            "object_type": "tasks",
            "properties": {"hs_task_subject": "Confirm timing"},
            "associations": [{"to": {"object_type": "deals", "id": "1"}}, {"to": {"ref": "new_contact"}}],
            "summary": "Create task",
        },
        {"op": "associate", "from": {"object_type": "deals", "id": "1"}, "to": {"object_type": "contacts", "id": "10"}, "summary": "Link Jane"},
    ]
    preview = await _propose(lf, ops)
    result = await changes.apply_changeset(lf, preview["confirmation_id"], preview["change_count"])

    assert result["status"] == "applied", result
    assert (result["succeeded"], result["failed"], result["drifted"], result["created"]) == (4, 0, 0, 2)
    assert http.objects[("deals", "1")] == {"closedate": "2026-11-30", "amount": "2000", "dealname": "Acme Corp deal 1"}
    new_contact = next(i for (t, i) in http.objects if t == "contacts" and i != "10")
    new_task = next(i for (t, i) in http.objects if t == "tasks")
    assert ("contacts", new_contact, "deals", "1") in http.associations
    assert ("tasks", new_task, "deals", "1") in http.associations
    assert ("tasks", new_task, "contacts", new_contact) in http.associations, "a ref resolves to the id just created"
    assert ("deals", "1", "contacts", "10") in http.associations
    assert "pronova_undo_changes" in result["next"]

    stored = await get_store().load_pending(PORTAL, preview["confirmation_id"])
    assert stored["status"] == "applied"
    audits = await get_store().get_recent_audits(PORTAL)
    assert [a["action"] for a in audits] == ["apply_changes"]
    assert audits[0]["result_summary"]["confirmation_id"] == preview["confirmation_id"], "the id must survive audit redaction"
    assert audits[0]["result_summary"]["succeeded"] == 4


async def test_apply_refuses_a_wrong_expected_count_and_changes_nothing(lf, http):
    _seed_deals(http)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30"), _update(2, closedate="2026-11-30")])
    calls_before = len(http.calls)
    with pytest.raises(ToolError, match="expected_count 5 does not match the 2 changes"):
        await changes.apply_changeset(lf, preview["confirmation_id"], 5)
    assert len(http.calls) == calls_before, "a refused apply makes no HubSpot call at all"
    stored = await get_store().load_pending(PORTAL, preview["confirmation_id"])
    assert stored["status"] == "pending", "and the changeset stays confirmable"
    again = await changes.apply_changeset(lf, preview["confirmation_id"], 2)
    assert again["status"] == "applied"


async def test_applying_twice_returns_the_first_result_and_writes_nothing_more(lf, http):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    first = await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    writes_after_first = len(http.writes())
    second = await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    assert second["status"] == "already_applied"
    assert second["succeeded"] == first["succeeded"] == 1
    assert len(http.writes()) == writes_after_first


async def test_apply_skips_a_record_changed_since_the_preview_and_reports_it_as_drifted(lf, http):
    _seed_deals(http, 3)
    preview = await _propose(lf, [_update(i, closedate="2026-11-30") for i in (1, 2, 3)])
    http.objects[("deals", "2")]["closedate"] = "2026-10-05"  # a colleague moved it after the preview

    result = await changes.apply_changeset(lf, preview["confirmation_id"], 3)

    assert result["status"] == "partially_applied"
    assert (result["succeeded"], result["drifted"]) == (2, 1)
    assert http.objects[("deals", "2")]["closedate"] == "2026-10-05", "the colleague's change survives"
    assert http.objects[("deals", "1")]["closedate"] == "2026-11-30"
    problem = result["problems"][0]
    assert problem["status"] == "drifted" and problem["id"] == "2"
    assert "previewed '2026-09-01'" in problem["detail"] and "2026-10-05" in problem["detail"]


async def test_drift_check_ignores_formatting_differences_in_numbers_and_dates(lf, http):
    http.add("deals", "1", closedate="2026-09-01", amount="1000")
    preview = await _propose(lf, [_update(1, closedate="2026-11-30", amount="2000")])
    # HubSpot echoes the same values in another format on the next read.
    http.objects[("deals", "1")].update(closedate="2026-09-01T00:00:00.000Z", amount="1000.0")
    result = await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    assert (result["succeeded"], result["drifted"]) == (1, 0)


async def test_a_record_already_at_the_proposed_value_is_left_alone(lf, http):
    _seed_deals(http, 2)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30"), _update(2, closedate="2026-11-30")])
    http.objects[("deals", "1")]["closedate"] = "2026-11-30"
    result = await changes.apply_changeset(lf, preview["confirmation_id"], 2)
    assert (result["succeeded"], result["unchanged"], result["failed"]) == (1, 1, 0)
    batch = next(body for m, p, body in http.calls if p.endswith("/batch/update"))
    assert [i["id"] for i in batch["inputs"]] == ["2"]


async def test_a_record_archived_since_the_preview_is_a_failure_not_a_crash(lf, http):
    _seed_deals(http, 2)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30"), _update(2, closedate="2026-11-30")])
    del http.objects[("deals", "2")]
    result = await changes.apply_changeset(lf, preview["confirmation_id"], 2)
    assert (result["succeeded"], result["failed"]) == (1, 1)
    assert "not found" in result["problems"][0]["detail"]


async def test_more_than_one_batch_of_updates_is_written_in_batches_of_the_configured_size(lf, http, env):
    (env["rules_dir"] / f"{PORTAL}.yaml").write_text("bulk_write_batch_size: 40\n")
    _seed_deals(http, 101)
    preview = await _propose(lf, [_update(i, closedate="2026-11-30") for i in range(1, 102)])
    result = await changes.apply_changeset(lf, preview["confirmation_id"], 101)
    assert result["succeeded"] == 101
    sizes = [len(body["inputs"]) for m, p, body in http.calls if p.endswith("/batch/update")]
    assert sizes == [40, 40, 21]
    assert all(v["closedate"] == "2026-11-30" for (t, _), v in http.objects.items() if t == "deals")


async def test_updates_to_two_object_types_use_their_own_batch_endpoints(lf, http):
    _seed_deals(http, 1)
    http.add("contacts", "10", jobtitle="Buyer")
    preview = await _propose(
        lf,
        [_update(1, closedate="2026-11-30"), {"op": "update", "object_type": "contacts", "id": "10", "properties": {"jobtitle": "VP"}}],
    )
    await changes.apply_changeset(lf, preview["confirmation_id"], 2)
    paths = [p for m, p, _ in http.writes()]
    assert "/crm/v3/objects/deals/batch/update" in paths and "/crm/v3/objects/contacts/batch/update" in paths


async def test_a_rejected_chunk_is_reported_and_the_other_chunks_still_apply(lf, http, env):
    (env["rules_dir"] / f"{PORTAL}.yaml").write_text("bulk_write_batch_size: 2\n")
    _seed_deals(http, 4)
    preview = await _propose(lf, [_update(i, closedate="2026-11-30") for i in range(1, 5)])
    http.script("POST", "/batch/update", HubSpotError("Property values were not valid", status_code=400))

    result = await changes.apply_changeset(lf, preview["confirmation_id"], 4)

    assert result["status"] == "partially_applied"
    assert (result["succeeded"], result["failed"]) == (2, 2)
    assert {p["id"] for p in result["problems"]} == {"1", "2"}
    assert all("not valid" in p["detail"] for p in result["problems"])
    assert http.objects[("deals", "1")]["closedate"] == "2026-09-01"
    assert http.objects[("deals", "3")]["closedate"] == "2026-11-30"
    snap = await get_store().load_undo_snapshot(PORTAL, preview["confirmation_id"])
    assert set(snap["original_values"]) == {"deals:3", "deals:4"}, "undo only covers what was written"


async def test_a_server_error_leaves_the_outcome_unconfirmed_and_undo_still_covers_it(lf, http):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    http.script("POST", "/batch/update", HubSpotError("upstream", status_code=502))
    result = await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    assert result["failed"] == 1 and result["problems"][0]["status"] == "unconfirmed"
    snap = await get_store().load_undo_snapshot(PORTAL, preview["confirmation_id"])
    assert "deals:1" in snap["original_values"], "a 5xx may have been applied, so undo must be able to revert it"


async def test_per_record_errors_in_a_207_response_fail_only_those_records(lf, http):
    _seed_deals(http, 2)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30"), _update(2, closedate="2026-11-30")])
    http.script(
        "POST",
        "/batch/update",
        {"status": "COMPLETE_WITH_ERRORS", "results": [{"id": "1"}], "errors": [{"message": "Invalid date", "context": {"ids": ["2"]}}]},
    )
    result = await changes.apply_changeset(lf, preview["confirmation_id"], 2)
    assert (result["succeeded"], result["failed"]) == (1, 1)
    assert result["problems"][0]["id"] == "2" and result["problems"][0]["detail"] == "Invalid date"


async def test_a_429_on_a_write_is_retried_rather_than_reported_as_a_failure(lf, http, env):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    http.script("POST", "/batch/update", RateLimitError("slow down", retry_after=4))
    result = await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    assert result["status"] == "applied" and env["sleeps"] == [4.0]
    assert http.objects[("deals", "1")]["closedate"] == "2026-11-30"


async def test_when_a_create_fails_the_ops_that_depend_on_it_are_blocked_and_dependents_are_reported(lf, http):
    _seed_deals(http, 1)
    ops = [
        {"op": "create", "object_type": "contacts", "ref": "c", "properties": {"firstname": "Sam"}, "associations": [{"to": {"object_type": "deals", "id": "1"}}], "summary": "Create contact"},
        {"op": "associate", "from": {"ref": "c"}, "to": {"object_type": "deals", "id": "1"}, "summary": "Link contact"},
    ]
    preview = await _propose(lf, ops)
    http.script("POST", "/crm/v3/objects/contacts", HubSpotError("Email invalid", status_code=400))
    result = await changes.apply_changeset(lf, preview["confirmation_id"], 2)
    assert result["status"] == "failed"
    assert result["failed"] == 2 and result["succeeded"] == 0
    statuses = {p["index"]: p["status"] for p in result["problems"]}
    assert statuses[0] == "failed" and statuses[1] == "blocked"
    assert not http.associations


async def test_an_association_that_already_exists_is_skipped_and_never_undone(lf, http):
    http.add("deals", "1", dealname="Acme Corp deal")
    http.add("contacts", "10", firstname="Jane")
    http.associate(("deals", "1"), ("contacts", "10"))
    preview = await _propose(
        lf, [{"op": "associate", "from": {"object_type": "deals", "id": "1"}, "to": {"object_type": "contacts", "id": "10"}}]
    )
    result = await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    assert result["unchanged"] == 1 and result["succeeded"] == 0
    assert not any(p.endswith("associate/default") for p in http.paths("POST"))
    undone = await changes.undo_changeset(lf, preview["confirmation_id"])
    assert undone["associations_removed"] == 0
    assert ("deals", "1", "contacts", "10") in http.associations, "undo must not remove an association it did not add"


async def test_a_partial_association_response_is_verified_by_reading_back(lf, http):
    http.add("deals", "1", dealname="a")
    http.add("contacts", "10", firstname="a")
    http.add("contacts", "11", firstname="b")
    preview = await _propose(
        lf,
        [
            {"op": "associate", "from": {"object_type": "deals", "id": "1"}, "to": {"object_type": "contacts", "id": "10"}},
            {"op": "associate", "from": {"object_type": "deals", "id": "1"}, "to": {"object_type": "contacts", "id": "11"}},
        ],
    )
    # HubSpot reports an error without naming the pair; only 10 actually lands.
    def lands_only_ten(*_):
        http.associate(("deals", "1"), ("contacts", "10"))
        return {"status": "COMPLETE_WITH_ERRORS", "errors": [{"message": "Cannot associate"}]}

    http.script("POST", "associate/default", lands_only_ten)
    result = await changes.apply_changeset(lf, preview["confirmation_id"], 2)
    assert (result["succeeded"], result["failed"]) == (1, 1)
    assert result["problems"][0]["status"] == "failed"


async def test_labelled_associations_send_the_category_and_type_id(lf, http):
    http.add("deals", "1", dealname="a")
    http.add("contacts", "10", firstname="a")
    preview = await _propose(
        lf,
        [{"op": "associate", "from": {"object_type": "deals", "id": "1"}, "to": {"object_type": "contacts", "id": "10"}, "type": 3}],
    )
    await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    body = next(b for m, p, b in http.calls if p.endswith("/batch/create"))
    assert body["inputs"][0]["types"] == [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 3}]


async def test_apply_refuses_an_unknown_expired_or_malformed_id(lf, http):
    with pytest.raises(ToolError, match="No changeset chg_abcdefghjkmn"):
        await changes.apply_changeset(lf, "chg_abcdefghjkmn", 1)
    with pytest.raises(ToolError, match="not a changeset id"):
        await changes.apply_changeset(lf, "../../etc/passwd", 1)
    assert http.calls == []


async def test_apply_refuses_a_standard_pending_preview_even_with_a_valid_looking_id(lf, http):
    await get_store().store_pending(PORTAL, "chg_aaaaaaaaaaaa", {"tool_name": "hubspot_update_object", "proposed_payload": {}})
    with pytest.raises(ToolError, match="not an intent changeset"):
        await changes.apply_changeset(lf, "chg_aaaaaaaaaaaa", 1)
    assert http.calls == []


async def test_an_apply_that_stopped_partway_is_not_retried_blindly(lf, http):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    stored = await get_store().load_pending(PORTAL, preview["confirmation_id"])
    stored["status"] = "applying"
    await get_store().store_pending(PORTAL, preview["confirmation_id"], stored)
    with pytest.raises(ToolError, match="stopped partway"):
        await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    assert http.writes() == []


async def test_a_concurrent_apply_in_the_same_process_is_refused(lf, http):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    changes._inflight.add(preview["confirmation_id"])
    try:
        with pytest.raises(ToolError, match="being applied"):
            await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    finally:
        changes._inflight.discard(preview["confirmation_id"])
    assert http.writes() == []


async def test_a_failed_drift_read_leaves_the_changeset_pending_and_retryable(lf, http):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    http.script("POST", "/batch/read", HubSpotError("boom", status_code=500))
    with pytest.raises(ToolError):
        await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    assert (await get_store().load_pending(PORTAL, preview["confirmation_id"]))["status"] == "pending"
    assert (await changes.apply_changeset(lf, preview["confirmation_id"], 1))["status"] == "applied"


# --- undo ------------------------------------------------------------------


async def test_undo_restores_original_values_archives_created_records_and_removes_new_associations(lf, http):
    http.add("deals", "1", closedate="2026-09-01", amount=None, dealname="Acme Corp deal")
    http.add("contacts", "10", firstname="Jane")
    ops = [
        _update(1, closedate="2026-11-30", amount="500"),
        {"op": "create", "object_type": "tasks", "properties": {"hs_task_subject": "Confirm"}, "associations": [{"to": {"object_type": "deals", "id": "1"}}]},
        {"op": "associate", "from": {"object_type": "deals", "id": "1"}, "to": {"object_type": "contacts", "id": "10"}},
    ]
    preview = await _propose(lf, ops)
    await changes.apply_changeset(lf, preview["confirmation_id"], 3)
    assert any(t == "tasks" for t, _ in http.objects) and ("deals", "1", "contacts", "10") in http.associations

    undone = await changes.undo_changeset(lf, preview["confirmation_id"])

    assert undone["status"] == "undone"
    assert (undone["restored"], undone["archived"], undone["associations_removed"]) == (1, 1, 1)
    assert http.objects[("deals", "1")]["closedate"] == "2026-09-01"
    assert http.objects[("deals", "1")]["amount"] == "", "a property that was empty is cleared, not left at the new value"
    assert not any(t == "tasks" for t, _ in http.objects)
    assert ("deals", "1", "contacts", "10") not in http.associations
    assert await get_store().load_undo_snapshot(PORTAL, preview["confirmation_id"]) is None
    audits = await get_store().get_recent_audits(PORTAL)
    assert {a["action"] for a in audits} == {"apply_changes", "undo_changes"}
    assert {a["result_summary"]["confirmation_id"] for a in audits} == {preview["confirmation_id"]}


async def test_a_second_undo_is_a_no_op_with_a_clear_message(lf, http):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    await changes.undo_changeset(lf, preview["confirmation_id"])
    writes = len(http.writes())
    again = await changes.undo_changeset(lf, preview["confirmation_id"])
    assert again["status"] == "already_undone" and "nothing was changed" in again["note"]
    assert len(http.writes()) == writes
    with pytest.raises(ToolError, match="undone"):
        await changes.apply_changeset(lf, preview["confirmation_id"], 1)


async def test_undo_still_works_after_the_pending_record_has_expired(lf, http):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    await get_store().clear_pending(PORTAL, preview["confirmation_id"])  # what the 24 hour TTL does

    undone = await changes.undo_changeset(lf, preview["confirmation_id"])

    assert undone["status"] == "undone" and http.objects[("deals", "1")]["closedate"] == "2026-09-01"
    writes = len(http.writes())
    with pytest.raises(ToolError, match="already undone"):
        await changes.undo_changeset(lf, preview["confirmation_id"])
    assert len(http.writes()) == writes, "a second undo after expiry must not write either"


async def test_undo_before_apply_is_refused(lf, http):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    with pytest.raises(ToolError, match="is pending, not applied"):
        await changes.undo_changeset(lf, preview["confirmation_id"])
    assert http.writes() == []


async def test_undo_with_an_expired_snapshot_changes_nothing(lf, http):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    await get_store().delete_undo_snapshot(PORTAL, preview["confirmation_id"])
    writes = len(http.writes())
    with pytest.raises(ToolError, match="snapshot .* is gone"):
        await changes.undo_changeset(lf, preview["confirmation_id"])
    assert len(http.writes()) == writes


async def test_a_partial_undo_keeps_the_snapshot_and_can_be_retried(lf, http):
    _seed_deals(http, 1)
    ops = [_update(1, closedate="2026-11-30"), {"op": "create", "object_type": "tasks", "properties": {"hs_task_subject": "x"}}]
    preview = await _propose(lf, ops)
    await changes.apply_changeset(lf, preview["confirmation_id"], 2)
    http.script("POST", "/crm/v3/objects/tasks/batch/archive", HubSpotError("boom", status_code=500))

    first = await changes.undo_changeset(lf, preview["confirmation_id"])
    assert first["status"] == "partially_undone" and first["failed"] == 1 and first["restored"] == 1
    assert await get_store().load_undo_snapshot(PORTAL, preview["confirmation_id"]) is not None

    second = await changes.undo_changeset(lf, preview["confirmation_id"])
    assert second["status"] == "undone" and second["archived"] == 1
    assert not any(t == "tasks" for t, _ in http.objects)


async def test_undo_treats_an_already_archived_record_as_done(lf, http):
    ops = [{"op": "create", "object_type": "notes", "properties": {"hs_note_body": "x"}}]
    preview = await _propose(lf, ops)
    await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    http.objects.clear()
    http.script("POST", "/crm/v3/objects/notes/batch/archive", HubSpotError("gone", status_code=404))
    http.script("DELETE", "/crm/v3/objects/notes", HubSpotError("gone", status_code=404))
    undone = await changes.undo_changeset(lf, preview["confirmation_id"])
    assert undone["status"] == "undone" and undone["archived"] == 1


# --- an apply that stops partway, and two that overlap ---------------------


class SlowHubSpot(FakeHubSpot):
    """Every call yields to the event loop first, as real HTTP does."""

    async def _call(self, method, path, body):
        await asyncio.sleep(0.02)
        return await super()._call(method, path, body)


class FlakyNotes(FakeHubSpot):
    """The ``fail_on``-th note create raises ``error``; ``hang_on`` makes that create wait forever instead."""

    def __init__(self, *, fail_on=None, error=None, hang_on=None):
        super().__init__()
        self.fail_on, self.error, self.hang_on = fail_on, error, hang_on
        self.note_posts = 0

    async def _call(self, method, path, body):
        if method == "POST" and path == "/crm/v3/objects/notes":
            self.note_posts += 1
            if self.note_posts == self.hang_on:
                self.calls.append((method, path, body))
                await asyncio.Event().wait()
            if self.note_posts == self.fail_on:
                self.calls.append((method, path, body))
                raise self.error
        return await super()._call(method, path, body)


def _note(n):
    return {"op": "create", "object_type": "notes", "properties": {"hs_note_body": f"note {n}"}, "summary": f"Note {n}"}


def _notes(http):
    return sorted(i for (t, i) in http.objects if t == "notes")


async def test_two_overlapping_applies_of_one_changeset_write_once(lf):
    slow = SlowHubSpot()
    _seed_deals(slow, 1)
    lf = {**lf, "client": slow}
    preview = await _propose(lf, [_update(1, closedate="2026-11-30"), _note(1)])

    first, second = await asyncio.gather(
        changes.apply_changeset(lf, preview["confirmation_id"], 2),
        changes.apply_changeset(lf, preview["confirmation_id"], 2),
        return_exceptions=True,
    )

    outcomes = [first, second]
    assert sum(isinstance(o, dict) and o["status"] == "applied" for o in outcomes) == 1, outcomes
    assert sum(isinstance(o, ToolError) and "being applied right now" in str(o) for o in outcomes) == 1, outcomes
    assert len(_notes(slow)) == 1, "the note is created once, not once per call"
    assert preview["confirmation_id"] not in changes._inflight


async def test_a_read_timeout_on_a_create_is_reported_unconfirmed_and_the_changeset_is_not_stranded(lf, http):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30"), _note(1), _note(2)])
    http.script("POST", "/crm/v3/objects/notes", httpx.ReadTimeout("timed out"))

    result = await changes.apply_changeset(lf, preview["confirmation_id"], 3)

    assert result["status"] == "partially_applied"
    assert (result["succeeded"], result["unconfirmed"], result["created"]) == (2, 1, 1)
    problem = result["problems"][0]
    assert problem["status"] == "unconfirmed" and "ReadTimeout" in problem["detail"]
    assert "may already be written" in result["next"] and "Check HubSpot" in result["next"]
    assert "not written; build a new changeset" not in result["next"], "an unconfirmed write must not be rebuilt blindly"
    stored = await get_store().load_pending(PORTAL, preview["confirmation_id"])
    assert stored["status"] == "applied", "not left in 'applying'"

    again = await changes.apply_changeset(lf, preview["confirmation_id"], 3)
    assert again["status"] == "already_applied" and len(_notes(http)) == 1

    undone = await changes.undo_changeset(lf, preview["confirmation_id"])
    assert undone["status"] == "undone" and (undone["restored"], undone["archived"]) == (1, 1)
    assert http.objects[("deals", "1")]["closedate"] == "2026-09-01" and _notes(http) == []


async def test_a_connection_error_on_the_update_batch_is_unconfirmed_and_undo_covers_it(lf, http):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    http.script("POST", "/batch/update", httpx.ConnectError("refused"))

    result = await changes.apply_changeset(lf, preview["confirmation_id"], 1)

    assert result["status"] == "partially_applied" and result["unconfirmed"] == 1, "it may have landed, so not 'failed'"
    assert result["problems"][0]["status"] == "unconfirmed"
    snap = await get_store().load_undo_snapshot(PORTAL, preview["confirmation_id"])
    assert set(snap["original_values"]) == {"deals:1"}, "the write may have landed, so undo keeps its original"
    assert (await changes.undo_changeset(lf, preview["confirmation_id"]))["status"] == "undone"


async def test_a_timeout_on_an_association_batch_is_reported_per_op(lf, http):
    _seed_deals(http, 1)
    ops = [{"op": "create", "object_type": "notes", "properties": {"hs_note_body": "x"},
            "associations": [{"to": {"object_type": "deals", "id": "1"}}]}]
    preview = await _propose(lf, ops)
    http.script("POST", "/associate/default", httpx.ReadTimeout("slow"))
    result = await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    assert result["status"] == "partially_applied" and result["partial"] == 1 and result["unconfirmed"] == 0
    assert "association unconfirmed" in result["problems"][0]["detail"]
    assert "do not create it again" in result["next"]
    assert "not written; build a new changeset" not in result["next"]


async def test_an_unexpected_error_in_one_create_does_not_lose_the_ids_of_the_others(lf):
    flaky = FlakyNotes(fail_on=3, error=RuntimeError("worker died"))
    lf = {**lf, "client": flaky}
    preview = await _propose(lf, [_note(n) for n in range(1, 6)])

    result = await changes.apply_changeset(lf, preview["confirmation_id"], 5)

    assert result["status"] == "partially_applied" and (result["succeeded"], result["unconfirmed"]) == (4, 1)
    assert "RuntimeError" in result["problems"][0]["detail"]
    snap = await get_store().load_undo_snapshot(PORTAL, preview["confirmation_id"])
    assert len(snap["metadata"]["created"]) == 4, "every record that was created is in the undo snapshot"
    undone = await changes.undo_changeset(lf, preview["confirmation_id"])
    assert undone["status"] == "undone" and undone["archived"] == 4 and _notes(flaky) == []


async def test_a_cancelled_apply_records_what_it_created_and_ends_applied_with_interrupted_set(lf, monkeypatch):
    monkeypatch.setattr(changes, "CREATE_CHECKPOINT", 2)
    hanging = FlakyNotes(hang_on=3)
    lf = {**lf, "client": hanging}
    preview = await _propose(lf, [_note(n) for n in range(1, 6)])
    cid = preview["confirmation_id"]

    task = asyncio.create_task(changes.apply_changeset(lf, cid, 5))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if len(_notes(hanging)) == 4:
            break
    assert len(_notes(hanging)) == 4, "four creates finished; the third POST is still waiting"
    mid = await get_store().load_undo_snapshot(PORTAL, cid)
    assert len(mid["metadata"]["created"]) == 4, "created ids are flushed while the apply is still running"
    assert (await get_store().load_pending(PORTAL, cid))["status"] == "applying"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    stored = await get_store().load_pending(PORTAL, cid)
    assert stored["status"] == "applied" and stored["summary"]["interrupted"] is True
    assert (stored["summary"]["succeeded"], stored["summary"]["unconfirmed"]) == (4, 1)
    assert cid not in changes._inflight
    undone = await changes.undo_changeset(lf, cid)
    assert undone["status"] == "undone" and undone["archived"] == 4 and _notes(hanging) == []


async def test_a_failure_before_any_write_puts_the_changeset_back_to_pending(lf, http, monkeypatch):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    store = get_store()
    real = store.save_undo_snapshot

    async def store_down(*args, **kwargs):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(store, "save_undo_snapshot", store_down)
    with pytest.raises(RuntimeError, match="store unavailable"):
        await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    assert http.writes() == []
    assert (await store.load_pending(PORTAL, preview["confirmation_id"]))["status"] == "pending"

    monkeypatch.setattr(store, "save_undo_snapshot", real)
    assert (await changes.apply_changeset(lf, preview["confirmation_id"], 1))["status"] == "applied"


async def test_undo_accepts_a_changeset_left_in_applying_by_a_process_that_died(lf, http):
    preview = await _propose(lf, [_note(1)])
    cid = preview["confirmation_id"]
    await changes.apply_changeset(lf, cid, 1)
    stored = await get_store().load_pending(PORTAL, cid)
    stored["status"] = "applying"
    await get_store().store_pending(PORTAL, cid, stored)

    changes._inflight.add(cid)
    try:
        with pytest.raises(ToolError, match="being applied right now"):
            await changes.undo_changeset(lf, cid)
    finally:
        changes._inflight.discard(cid)
    assert len(_notes(http)) == 1

    undone = await changes.undo_changeset(lf, cid)
    assert undone["status"] == "undone" and undone["archived"] == 1 and _notes(http) == []


async def test_a_create_that_depends_on_an_update_is_only_made_when_that_update_was_written(lf, http):
    _seed_deals(http, 2)
    ops = [
        _update(1, closedate="2026-11-30"),
        _update(2, closedate="2026-11-30"),
        {**_note(1), "depends_on": 0},
        {**_note(2), "depends_on": 1},
    ]
    preview = await _propose(lf, ops)
    http.objects[("deals", "2")]["closedate"] = "2026-10-15"  # a rep moved it after the preview

    result = await changes.apply_changeset(lf, preview["confirmation_id"], 4)

    assert (result["succeeded"], result["drifted"], result["created"], result["skipped_dependents"]) == (2, 1, 1, 1)
    assert [http.objects[k]["hs_note_body"] for k in http.objects if k[0] == "notes"] == ["note 1"]
    assert "were not created because the change they belong to was not written" in result["next"]
    assert [p["status"] for p in result["problems"]] == ["drifted"], "a skipped dependent is not itself a problem"


async def test_undo_archives_created_records_in_batches_of_100_not_one_request_each(lf, http):
    ops = [{"op": "create", "object_type": "tasks", "properties": {"hs_task_subject": f"t{n}"}} for n in range(250)]
    preview = await _propose(lf, ops)
    await changes.apply_changeset(lf, preview["confirmation_id"], 250)
    http.calls.clear()

    undone = await changes.undo_changeset(lf, preview["confirmation_id"])

    assert undone["status"] == "undone" and undone["archived"] == 250
    archives = [b for m, p, b in http.calls if p == "/crm/v3/objects/tasks/batch/archive"]
    assert [len(b["inputs"]) for b in archives] == [100, 100, 50]
    assert not [c for c in http.calls if c[0] == "DELETE"], "no request per record"
    assert not any(t == "tasks" for t, _ in http.objects)


async def test_a_batch_archive_failure_is_reported_and_a_retry_finishes_the_job(lf, http):
    preview = await _propose(lf, [_note(1), _note(2)])
    await changes.apply_changeset(lf, preview["confirmation_id"], 2)
    http.script("POST", "/crm/v3/objects/notes/batch/archive", httpx.ReadTimeout("slow"))

    first = await changes.undo_changeset(lf, preview["confirmation_id"])
    assert first["status"] == "partially_undone" and first["failed"] == 1 and "ReadTimeout" in first["problems"][0]["error"]
    second = await changes.undo_changeset(lf, preview["confirmation_id"])
    assert second["status"] == "undone" and second["archived"] == 2


async def test_list_pending_detailed_adds_the_first_change_summaries(lf, http):
    await _propose(lf, [_note(n) for n in range(1, 15)], title="Many notes")
    concise = await changes.list_pending(lf)
    detailed = await changes.list_pending(lf, response_format="detailed")
    assert "changes" not in concise["pending"][0]
    assert detailed["pending"][0]["changes"] == [f"Note {n}" for n in range(1, 11)]


# --- list ------------------------------------------------------------------


async def test_list_pending_shows_only_unapplied_changesets_newest_first(lf, http):
    _seed_deals(http, 3)
    first = await _propose(lf, [_update(1, closedate="2026-11-30")], title="First")
    applied = await _propose(lf, [_update(2, closedate="2026-11-30")], title="Applied")
    await changes.apply_changeset(lf, applied["confirmation_id"], 1)
    third = await _propose(lf, [_update(3, closedate="2026-11-30")], title="Third")
    await get_store().store_pending(PORTAL, "a1b2c3d4", {"tool_name": "hubspot_update_object", "proposed_payload": {}})

    listing = await changes.list_pending(lf)

    ids = [p["confirmation_id"] for p in listing["pending"]]
    assert listing["count"] == 2
    assert set(ids) == {first["confirmation_id"], third["confirmation_id"]}
    assert applied["confirmation_id"] not in ids and "a1b2c3d4" not in ids
    assert {p["title"] for p in listing["pending"]} == {"First", "Third"}
    assert all(p["change_count"] == 1 for p in listing["pending"])


# --- the old write tools must never touch a changeset -----------------------


async def test_hubspot_approve_write_cannot_execute_a_changeset(lf, http, env):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    calls_before = len(http.calls)
    async with Client(env["server"].mcp) as client:
        result = await client.call_tool("hubspot_approve_write", {"action_id": preview["confirmation_id"]})
    assert result.is_error is True and "No pending preview found" in result.content[0].text
    assert len(http.calls) == calls_before, "the approve path must not make any HubSpot call"
    assert http.objects[("deals", "1")]["closedate"] == "2026-09-01"
    assert await get_store().load_pending(PORTAL, preview["confirmation_id"]) is not None, "the changeset stays pending"


async def test_old_reject_and_pending_list_leave_a_changeset_alone(lf, http, env):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    cid = preview["confirmation_id"]
    async with Client(env["server"].mcp) as client:
        rejected = await client.call_tool("hubspot_reject_write", {"action_id": cid})
        listed = await client.call_tool("hubspot_list_pending_writes", {})
    assert rejected.is_error is True
    assert cid not in json.dumps(listed.structured_content or listed.content[0].text)
    applied = await changes.apply_changeset(lf, cid, 1)
    assert http.objects[("deals", "1")]["closedate"] == "2026-11-30", applied


async def test_hubspot_undo_write_refuses_a_changeset_snapshot(lf, http, env):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    await changes.apply_changeset(lf, preview["confirmation_id"], 1)
    calls_before = len(http.calls)
    async with Client(env["server"].mcp) as client:
        result = await client.call_tool("hubspot_undo_write", {"action_id": preview["confirmation_id"]})
    assert http.objects[("deals", "1")]["closedate"] == "2026-11-30", "only pronova_undo_changes may revert it"
    assert len(http.calls) == calls_before
    text = json.dumps(result.structured_content) if result.structured_content else result.content[0].text
    assert "not undoable" in text or result.is_error


# --- through the MCP surface -------------------------------------------------


async def test_the_three_tools_are_registered_with_documented_parameters(env):
    tools = {t.name: t for t in await env["server"].intent.list_tools()}
    for name in ("pronova_apply_changes", "pronova_undo_changes", "pronova_list_pending_changes"):
        assert name in tools
        assert "Returns" in tools[name].description or "returns" in tools[name].description
        assert len(tools[name].description) < 600
    props = tools["pronova_apply_changes"].input_schema["properties"]
    assert set(props) == {"confirmation_id", "expected_count"}
    assert all(p.get("description") for p in props.values())
    assert set(tools["pronova_list_pending_changes"].input_schema.get("properties", {})) == {"response_format"}


async def test_apply_and_undo_end_to_end_through_the_intent_surface(lf, http, env):
    _seed_deals(http, 1)
    preview = await _propose(lf, [_update(1, closedate="2026-11-30")])
    async with Client(env["server"].intent) as client:
        listing = await client.call_tool("pronova_list_pending_changes", {})
        assert (listing.structured_content or json.loads(listing.content[0].text))["count"] == 1

        wrong = await client.call_tool(
            "pronova_apply_changes", {"confirmation_id": preview["confirmation_id"], "expected_count": 9}
        )
        assert wrong.is_error is True and "does not match" in wrong.content[0].text

        applied = await client.call_tool(
            "pronova_apply_changes", {"confirmation_id": preview["confirmation_id"], "expected_count": 1}
        )
        assert applied.is_error is False
        assert (applied.structured_content or json.loads(applied.content[0].text))["status"] == "applied"
        assert http.objects[("deals", "1")]["closedate"] == "2026-11-30"

        undone = await client.call_tool("pronova_undo_changes", {"confirmation_id": preview["confirmation_id"]})
        assert (undone.structured_content or json.loads(undone.content[0].text))["status"] == "undone"
    assert http.objects[("deals", "1")]["closedate"] == "2026-09-01"


# --- value comparison ------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "equal"),
    [
        (None, "", True),
        ("1000", "1000.0", True),
        ("1000", 1000, True),
        ("2026-09-01", "2026-09-01T00:00:00.000Z", True),
        ("2026-09-01T00:00:00Z", "1788220800000", True),
        ("2026-09-01", "2026-09-02", False),
        ("0049123", "49123", False),
        ("Acme", " Acme ", True),
        ("Acme", "acme", False),
        (True, "true", True),
        ("1000", "1001", False),
    ],
)
def test_same_value_compares_the_way_hubspot_echoes_values(a, b, equal):
    assert changes.same_value(a, b) is equal
