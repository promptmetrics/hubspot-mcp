"""``pronova_log_meeting_outcome``: one preview, one confirmation, one undo.

Pins the promises the benchmark and the demo lean on: the tool writes nothing
before ``pronova_apply_changes``, a name that fits two deals is never guessed,
apply creates the meeting, note, task and contact with the right associations,
and apply followed by undo leaves HubSpot exactly as it started (no created
record survives). Runs over the file store and Redis (fakeredis).
"""
from __future__ import annotations

import copy
import json
import re
from datetime import UTC, date, datetime

import pytest
from intent_fakes import PORTAL, FakeHubSpot
from mcp import Client

from hubspot_mcp.config import PortalConfig
from hubspot_mcp.errors import HubSpotError
from hubspot_mcp.intent import common, log_meeting
from hubspot_mcp.intent.log_meeting import NewContactIn, TaskIn
from hubspot_mcp.intent.rules import load_rules
from hubspot_mcp.state import get_store, set_store

AS_OF = "2026-10-30"
_SEARCHABLE = ("dealname", "name", "domain", "firstname", "lastname", "email")
_ENGAGEMENTS = ("meetings", "notes", "tasks")


class Portal(FakeHubSpot):
    """FakeHubSpot plus Search, pipelines and owners, which the foundation fake leaves scripted."""

    def _handle(self, method, path, body):
        if method == "GET" and path == "/crm/v3/pipelines/deals":
            return {
                "results": [
                    {
                        "id": "p1",
                        "label": "Sales",
                        "stages": [
                            {"id": "s1", "label": "Proposal", "displayOrder": 0, "metadata": {"probability": "0.5", "isClosed": "false"}},
                            {"id": "s2", "label": "Closed won", "displayOrder": 1, "metadata": {"probability": "1.0", "isClosed": "true"}},
                        ],
                    }
                ]
            }
        if method == "GET" and path.startswith("/crm/v3/owners"):
            return {"results": [{"id": "77", "email": "rep@example.com", "firstName": "Rae", "lastName": "Rep"}]}
        if method == "POST" and (m := re.fullmatch(r"/crm/v3/objects/([a-z_]+)/search", path)):
            return self._search(m[1], body)
        return super()._handle(method, path, body)

    def _search(self, object_type, body):
        query = (body.get("query") or "").casefold()
        groups = body.get("filterGroups") or [{"filters": []}]
        wanted = body.get("properties") or []
        results = []
        for (kind, record_id), props in sorted(self.objects.items()):
            if kind != object_type:
                continue
            if query and not any(query in str(props.get(p) or "").casefold() for p in _SEARCHABLE):
                continue

            def matches(flt, props=props):
                assert flt["operator"] == "EQ", f"Portal fake only does EQ, got {flt['operator']}"
                return str(props.get(flt["propertyName"]) or "").casefold() == str(flt["value"]).casefold()

            if any(all(matches(f) for f in g["filters"]) for g in groups):
                results.append({"id": record_id, "properties": {p: props.get(p) for p in wanted}})
        return {"results": results[: body.get("limit", 10)], "total": len(results)}

    def link(self, a, b):
        self.associate(a, b)
        self.associate(b, a)


@pytest.fixture
def http():
    portal = Portal()
    portal.add("companies", "100", name="Acme Corp", domain="acme.example.com")
    portal.add(
        "deals", "1", dealname="Acme Corp - Expansion", amount="20000", deal_currency_code="EUR",
        closedate="2026-09-30T00:00:00Z", dealstage="s1", pipeline="p1", hubspot_owner_id="77", hs_is_closed="false",
    )
    portal.add("contacts", "10", firstname="Jordan", lastname="Smith", email="jordan@acme.example.com")
    portal.link(("deals", "1"), ("companies", "100"))
    portal.link(("deals", "1"), ("contacts", "10"))
    portal.link(("companies", "100"), ("contacts", "10"))
    return portal


@pytest.fixture(params=["file", "redis"])
async def env(request, tmp_path, monkeypatch, http):
    from hubspot_mcp import config, persistence, server

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(persistence, "CONFIG_DIR", tmp_path)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("HUBSPOT_MCP_STATE_BACKEND", "file")
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / f"{PORTAL}.yaml").write_text(
        f"as_of: {AS_OF}\ntimezone: Europe/Berlin\ndefault_owner: rep@example.com\nhome_currency: EUR\n", encoding="utf-8"
    )
    monkeypatch.setenv("HUBSPOT_MCP_RULES_DIR", str(rules_dir))
    common.clear_caches()
    set_store(None)
    fake_redis = None
    if request.param == "redis":
        import fakeredis.aioredis
        from cryptography.fernet import Fernet

        from hubspot_mcp.state.redis_store import RedisStateStore

        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
        set_store(RedisStateStore(fake_redis, encryption_key=Fernet.generate_key().decode()))

    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(common, "_sleep", no_sleep)
    lifespan = {
        "client": http,
        "cache": None,
        "portal_config": PortalConfig(portal_id=PORTAL, token="t", scopes_granted=[]),
        "portal_id": PORTAL,
        "auth_error": None,
    }
    monkeypatch.setattr(server, "_lifespan", lambda ctx: lifespan)
    monkeypatch.setattr(server, "_session_resolver", None)
    yield {"lf": lifespan, "rules_dir": rules_dir, "server": server}
    common.clear_caches()
    set_store(None)
    if fake_redis is not None:
        await fake_redis.aclose()


def _data(result):
    return result.structured_content if result.structured_content is not None else json.loads(result.content[0].text)


def _text(result):
    return result.content[0].text if result.content else ""


# The debrief the demo uses: pilot agreed, close moves, amount changes, a task, a new CFO.
FULL_CALL = {
    "deal_or_company": "Acme Corp",
    "summary": "They will pilot it with the finance team. Security review is the only open item.",
    "outcome": "Agreed to a pilot",
    "close_date": "2026-12-15",
    "amount": 24000,
    "currency": "EUR",
    "tasks": [{"subject": "Send the security document", "due_date": "2026-11-06"}],
    "new_contacts": [{"first_name": "Maria", "last_name": "Lopez", "email": "maria.lopez@acme.example.com", "job_title": "CFO"}],
}


async def _call(env, args, tool="pronova_log_meeting_outcome"):
    async with Client(env["server"].intent) as client:
        return await client.call_tool(tool, args)


async def _apply(env, preview):
    return await _call(
        env,
        {"confirmation_id": preview["confirmation_id"], "expected_count": preview["change_count"]},
        tool="pronova_apply_changes",
    )


# --- nothing is written before apply ---------------------------------------


async def test_the_preview_lists_all_five_changes_and_writes_nothing(env, http):
    before = (copy.deepcopy(http.objects), set(http.associations))
    result = await _call(env, FULL_CALL)
    assert result.is_error is False, _text(result)
    preview = _data(result)

    assert preview["status"] == "preview"
    assert preview["change_count"] == 5
    assert preview["by_kind"] == {
        "create contacts": 1, "create meetings": 1, "create notes": 1, "create tasks": 1, "update deals": 1,
    }
    assert preview["as_of"] == AS_OF and re.fullmatch(r"[0-9a-f]{12}", preview["rules_hash"])
    assert re.fullmatch(r"chg_[a-z]{12}", preview["confirmation_id"])
    assert "pronova_apply_changes" in preview["next"] and "expected_count=5" in preview["next"]
    assert any("close date 2026-09-30 to 2026-12-15" in c and "amount 20000 to 24000" in c for c in preview["changes"])
    assert any("Maria Lopez (CFO)" in c for c in preview["changes"])
    assert preview["deal"]["id"] == "1" and preview["deal"]["stage_position"] == "Stage 1 of 2"
    assert preview["company"] == {"id": "100", "name": "Acme Corp"}
    assert preview["contact"] == {"id": "10", "name": "Jordan Smith"}

    assert http.writes() == [], "building the preview must not write anything to HubSpot"
    assert all(m == "GET" or p.endswith(("/search", "/batch/read")) for m, p, _ in http.calls)
    assert (http.objects, http.associations) == before, "HubSpot state is untouched by the preview"
    assert not [k for k in http.objects if k[0] in _ENGAGEMENTS]


async def test_the_changeset_is_stored_pending_with_the_exact_ops(env, http):
    preview = _data(await _call(env, FULL_CALL))
    stored = await get_store().load_pending(PORTAL, preview["confirmation_id"])

    assert stored["kind"] == "intent_changeset" and stored["status"] == "pending"
    assert stored["change_count"] == 5 and stored["rules_hash"] == preview["rules_hash"]
    by_type = {op["object_type"]: op for op in stored["ops"]}
    assert by_type["deals"]["properties"] == {"closedate": "2026-12-15", "amount": "24000"}, "same currency: not rewritten"
    assert stored["original_values"]["deals:1"] == {"closedate": "2026-09-30T00:00:00Z", "amount": "20000"}
    meeting = by_type["meetings"]["properties"]
    assert meeting["hs_timestamp"] == "2026-10-30T11:00:00.000Z", "frozen as_of at 12:00 Berlin (CET)"
    assert meeting["hs_meeting_outcome"] == "COMPLETED" and meeting["hubspot_owner_id"] == "77"
    assert [a["to"] for a in by_type["meetings"]["associations"]] == [
        {"object_type": "deals", "id": "1"}, {"object_type": "companies", "id": "100"}, {"object_type": "contacts", "id": "10"},
    ]
    assert by_type["notes"]["properties"]["hs_note_body"].startswith("<p>Outcome: Agreed to a pilot</p>")
    assert "Follow-ups" in by_type["notes"]["properties"]["hs_note_body"]
    assert by_type["tasks"]["properties"]["hs_timestamp"] == "2026-11-06T11:00:00.000Z"
    assert [a["to"] for a in by_type["tasks"]["associations"]] == [
        {"object_type": "deals", "id": "1"}, {"object_type": "companies", "id": "100"},
    ]
    contact = by_type["contacts"]
    assert contact["properties"] == {
        "firstname": "Maria", "lastname": "Lopez", "email": "maria.lopez@acme.example.com", "jobtitle": "CFO",
    }
    assert [a["to"]["object_type"] for a in contact["associations"]] == ["deals", "companies"]


# --- apply, then undo ------------------------------------------------------


async def test_apply_creates_the_records_and_undo_leaves_no_created_record_behind(env, http):
    before_objects, before_assoc = copy.deepcopy(http.objects), set(http.associations)
    preview = _data(await _call(env, FULL_CALL))
    confirmation = preview["confirmation_id"]

    applied = await _apply(env, preview)
    assert applied.is_error is False, _text(applied)
    summary = _data(applied)
    assert summary["status"] == "applied", summary
    assert (summary["succeeded"], summary["failed"], summary["drifted"], summary["created"]) == (5, 0, 0, 4)

    deal = http.objects[("deals", "1")]
    assert (deal["closedate"], deal["amount"]) == ("2026-12-15", "24000")
    created = {t: (i, p) for (t, i), p in http.objects.items() if t in _ENGAGEMENTS}
    assert set(created) == set(_ENGAGEMENTS)
    for kind in ("meetings", "notes"):
        for target in (("deals", "1"), ("companies", "100"), ("contacts", "10")):
            assert (kind, created[kind][0], *target) in http.associations, f"{kind} -> {target}"
    assert ("tasks", created["tasks"][0], "deals", "1") in http.associations
    assert ("tasks", created["tasks"][0], "companies", "100") in http.associations
    assert created["tasks"][1]["hs_task_subject"] == "Send the security document"
    maria = next(i for (t, i), p in http.objects.items() if t == "contacts" and p.get("firstname") == "Maria")
    assert ("contacts", maria, "deals", "1") in http.associations
    assert ("contacts", maria, "companies", "100") in http.associations

    undone = await _call(env, {"confirmation_id": confirmation}, tool="pronova_undo_changes")
    assert undone.is_error is False, _text(undone)
    assert _data(undone)["status"] == "undone"

    assert http.objects == before_objects, "every created record is archived and the deal is restored"
    assert http.associations == before_assoc, "no association to a created record survives"
    assert not [k for k in http.objects if k[0] in _ENGAGEMENTS]
    assert [k for k in http.objects if k[0] == "contacts"] == [("contacts", "10")]


async def test_a_wrong_expected_count_is_refused_and_writes_nothing(env, http):
    preview = _data(await _call(env, FULL_CALL))
    result = await _call(env, {"confirmation_id": preview["confirmation_id"], "expected_count": 4}, tool="pronova_apply_changes")
    assert result.is_error is True and "does not match" in _text(result)
    assert http.writes() == []


async def test_a_deal_edited_after_the_preview_is_not_overwritten(env, http):
    preview = _data(await _call(env, FULL_CALL))
    http.objects[("deals", "1")]["closedate"] = "2026-10-15T00:00:00Z"  # a colleague moved it meanwhile

    summary = _data(await _apply(env, preview))
    assert summary["status"] == "partially_applied" and summary["drifted"] == 1
    assert http.objects[("deals", "1")]["closedate"] == "2026-10-15T00:00:00Z"
    assert http.objects[("deals", "1")]["amount"] == "20000"
    assert any(k[0] == "meetings" for k in http.objects), "the rest of the changeset still applies"


# --- resolving the deal ----------------------------------------------------


@pytest.fixture
def two_deals(http):
    http.add(
        "deals", "2", dealname="Acme Corp - Renewal", amount="5000", deal_currency_code="EUR",
        closedate="2026-12-01T00:00:00Z", dealstage="s1", pipeline="p1", hubspot_owner_id="77", hs_is_closed="false",
    )
    http.link(("deals", "2"), ("companies", "100"))
    return http


async def test_a_company_with_two_open_deals_returns_candidates_and_stores_nothing(env, two_deals):
    result = await _call(env, {"deal_or_company": "Acme Corp", "summary": "Good call."})
    assert result.is_error is False, _text(result)
    out = _data(result)

    assert out["status"] == "ambiguous"
    assert [c["id"] for c in out["candidates"]] == ["1", "2"]
    assert {c["company"] for c in out["candidates"]} == {"Acme Corp"}
    assert "deal's id" in out["next"] and out["as_of"] == AS_OF
    assert two_deals.writes() == []
    assert (await get_store().list_pending(PORTAL)) == [], "an ambiguous call must not leave a changeset behind"


async def test_the_deal_id_picks_one_of_two_candidates(env, two_deals):
    out = _data(await _call(env, {"deal_or_company": "2", "summary": "Good call.", "close_date": "2027-01-15"}))
    assert out["status"] == "preview" and out["deal"]["id"] == "2"
    assert out["changes"][0].startswith("Update deal Acme Corp - Renewal: close date 2026-12-01 to 2027-01-15")


async def test_an_exact_deal_name_wins_over_a_looser_company_match(env, two_deals):
    out = _data(await _call(env, {"deal_or_company": "acme corp - renewal", "summary": "Good call."}))
    assert out["status"] == "preview" and out["deal"]["id"] == "2"
    assert not any("resolved to" in a for a in out.get("assumptions", []))


async def test_a_company_with_one_open_deal_resolves_to_it_and_says_so(env, http):
    http.add("deals", "9", dealname="Acme Corp - Old", dealstage="s2", pipeline="p1", hs_is_closed="true", closedate="2025-01-01T00:00:00Z")
    http.link(("deals", "9"), ("companies", "100"))
    out = _data(await _call(env, {"deal_or_company": "acme.example.com", "summary": "Check-in."}))
    assert out["status"] == "preview" and out["deal"]["id"] == "1", "the closed deal is not a candidate"
    assert any("the only open deal" in a for a in out["assumptions"])


async def test_an_unknown_name_is_an_error_and_writes_nothing(env, http):
    result = await _call(env, {"deal_or_company": "Globex Industries", "summary": "Check-in."})
    assert result.is_error is True and "No open deal or company matches" in _text(result)
    assert http.writes() == []


async def test_a_company_with_no_open_deal_says_to_pick_a_deal(env, http):
    del http.objects[("deals", "1")]
    http.associations = {a for a in http.associations if ("deals", "1") not in ((a[0], a[1]), (a[2], a[3]))}
    result = await _call(env, {"deal_or_company": "Acme Corp", "summary": "Check-in."})
    assert result.is_error is True and "has no open deal" in _text(result)


async def test_a_closed_deal_given_by_id_is_allowed_with_a_warning(env, http):
    http.add("deals", "9", dealname="Acme Corp - Old", dealstage="s2", pipeline="p1", hs_is_closed="true", closedate="2025-01-01T00:00:00Z")
    out = _data(await _call(env, {"deal_or_company": "9", "summary": "Late follow-up."}))
    assert out["status"] == "preview" and any("is closed" in w for w in out["warnings"])


# --- associations that are not unambiguous --------------------------------


async def test_a_deal_with_two_contacts_gets_no_guessed_contact(env, http):
    http.add("contacts", "11", firstname="Sam", lastname="Lee")
    http.link(("deals", "1"), ("contacts", "11"))
    out = _data(await _call(env, {"deal_or_company": "1", "summary": "Call."}))
    assert out["contact"] is None and any("2 contacts" in w for w in out["warnings"])
    stored = await get_store().load_pending(PORTAL, out["confirmation_id"])
    meeting = next(op for op in stored["ops"] if op["object_type"] == "meetings")
    assert [a["to"]["object_type"] for a in meeting["associations"]] == ["deals", "companies"]


async def test_a_deal_with_two_companies_uses_the_primary_one(env, http):
    http.add("companies", "200", name="Acme Holding")
    http.link(("deals", "1"), ("companies", "200"))

    def with_labels(body):
        rows = []
        for item in body["inputs"]:
            targets = [
                {
                    "toObjectId": int(a[3]),
                    "associationTypes": [{"category": "HUBSPOT_DEFINED", "typeId": 5, "label": "Primary" if a[3] == "200" else None}],
                }
                for a in sorted(http.associations)
                if a[:3] == ("deals", str(item["id"]), "companies")
            ]
            rows.append({"from": {"id": str(item["id"])}, "to": targets})
        return {"status": "COMPLETE", "results": rows}

    http.script("POST", "/associations/deals/companies/batch/read", with_labels)
    out = _data(await _call(env, {"deal_or_company": "1", "summary": "Call."}))
    assert out["company"] == {"id": "200", "name": "Acme Holding"}


async def test_a_deal_with_two_companies_and_no_primary_gets_no_company_and_a_warning(env, http):
    http.add("companies", "200", name="Acme Holding")
    http.link(("deals", "1"), ("companies", "200"))
    out = _data(await _call(env, {"deal_or_company": "1", "summary": "Call."}))
    assert out["company"] is None and any("none is marked Primary" in w for w in out["warnings"])


# --- contacts ----------------------------------------------------------------


async def test_a_contact_whose_email_exists_is_linked_not_duplicated(env, http):
    http.add("contacts", "50", firstname="Maria", lastname="Lopez", email="maria.lopez@acme.example.com")
    out = _data(await _call(env, {"deal_or_company": "1", "summary": "Call.", "new_contacts": FULL_CALL["new_contacts"]}))
    assert out["by_kind"] == {"associate association": 2, "create meetings": 1, "create notes": 1}
    assert any("already exists (contact 50)" in a for a in out["assumptions"])

    applied = _data(await _apply(env, out))
    assert applied["status"] == "applied"
    assert ("contacts", "50", "deals", "1") in http.associations
    assert ("contacts", "50", "companies", "100") in http.associations
    assert [i for (t, i), p in http.objects.items() if t == "contacts" and p.get("lastname") == "Lopez"] == ["50"]


async def test_a_company_contact_with_the_same_name_is_reused(env, http):
    out = _data(await _call(env, {"deal_or_company": "1", "summary": "Call.", "new_contacts": [{"first_name": "jordan", "last_name": "SMITH"}]}))
    assert "create contacts" not in out["by_kind"]
    assert any("It is already linked to the deal and company" in a for a in out["assumptions"])
    assert out["change_count"] == 2, "an existing, fully linked contact adds no change"


async def test_two_new_contacts_with_the_same_email_are_refused(env, http):
    same = {"first_name": "A", "last_name": "One", "email": "x@acme.example.com"}
    result = await _call(env, {"deal_or_company": "1", "summary": "Call.", "new_contacts": [same, {**same, "first_name": "B"}]})
    assert result.is_error is True and "appears twice" in _text(result)


# --- deal update --------------------------------------------------------------


async def test_a_value_that_is_already_set_is_not_proposed_again(env, http):
    out = _data(await _call(env, {"deal_or_company": "1", "summary": "Call.", "close_date": "2026-09-30", "amount": 20000}))
    assert out["by_kind"] == {"create meetings": 1, "create notes": 1}
    assert any("close date is already" in a for a in out["assumptions"])
    assert any("amount is already" in a for a in out["assumptions"])


async def test_the_currency_is_only_set_when_it_differs(env, http):
    out = _data(await _call(env, {"deal_or_company": "1", "summary": "Call.", "amount": 18000, "currency": "usd"}))
    stored = await get_store().load_pending(PORTAL, out["confirmation_id"])
    update = next(op for op in stored["ops"] if op["op"] == "update")
    assert update["properties"] == {"amount": "18000", "deal_currency_code": "USD"}
    assert stored["original_values"]["deals:1"] == {"amount": "20000", "deal_currency_code": "EUR"}


# --- time ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("2026-11-02", "2026-11-02T11:00:00.000Z"),
        ("2026-11-02T14:30", "2026-11-02T13:30:00.000Z"),
        ("2026-07-02T14:30", "2026-07-02T12:30:00.000Z"),
        ("2026-11-02T09:00:00Z", "2026-11-02T09:00:00.000Z"),
        ("2026-11-02T09:00:00+02:00", "2026-11-02T07:00:00.000Z"),
    ],
)
async def test_meeting_time_is_read_in_the_portal_timezone_unless_it_carries_one(env, http, given, expected):
    out = _data(await _call(env, {"deal_or_company": "1", "summary": "Call.", "meeting_time": given}))
    assert out["meeting_time"] == expected


async def test_a_meeting_after_as_of_is_flagged(env, http):
    out = _data(await _call(env, {"deal_or_company": "1", "summary": "Call.", "meeting_time": "2026-11-05"}))
    assert any("after as_of" in w for w in out["warnings"])


async def test_without_a_frozen_as_of_the_meeting_defaults_to_now(env, http):
    (env["rules_dir"] / f"{PORTAL}.yaml").write_text("timezone: Europe/Berlin\n", encoding="utf-8")
    rules = load_rules(PORTAL)
    now = datetime(2026, 11, 3, 8, 15, tzinfo=UTC)
    out = await log_meeting.build_changeset(env["lf"], rules, deal_or_company="1", summary="Call.", now=now)
    assert out["meeting_time"] == "2026-11-03T08:15:00.000Z" and out["as_of"] == "2026-11-03"


# --- input validation: refused before any HubSpot call -------------------------


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ({"summary": "  "}, "summary must not be empty"),
        ({"summary": "x" * 20_001}, "keep it under"),
        ({"summary": "ok", "amount": -5}, "0 or more"),
        ({"summary": "ok", "currency": "EUR"}, "currency needs an amount"),
        ({"summary": "ok", "amount": 5, "currency": "euro"}, "three-letter"),
        ({"summary": "ok", "meeting_time": "next friday"}, "not ISO 8601"),
        ({"summary": "ok", "meeting_time": "2026-02-31"}, "not a real date"),
        ({"summary": "ok", "new_contacts": [{"first_name": "A", "last_name": "B", "email": "nope"}]}, "not an email"),
        ({"summary": "ok", "tasks": [{"subject": "x", "due_date": "2026-11-06"} for _ in range(11)]}, "At most 10 tasks"),
        ({"summary": "ok", "tasks": [{"subject": "Send doc", "due_date": "Friday"}]}, None),
    ],
)
async def test_bad_input_is_refused_without_touching_hubspot(env, http, args, needle):
    result = await _call(env, {"deal_or_company": "1", **args})
    assert result.is_error is True
    if needle:
        assert needle in _text(result)
    assert http.calls == [], "validation happens before the first HubSpot call"


async def test_a_note_body_escapes_markup_from_the_summary(env, http):
    out = _data(await _call(env, {"deal_or_company": "1", "summary": "They said <b>yes</b> & signed.\n\nSecond paragraph."}))
    stored = await get_store().load_pending(PORTAL, out["confirmation_id"])
    note = next(op for op in stored["ops"] if op["object_type"] == "notes")["properties"]["hs_note_body"]
    assert note == "<p>They said &lt;b&gt;yes&lt;/b&gt; &amp; signed.</p><p>Second paragraph.</p>"


# --- failures and connection ----------------------------------------------------


async def test_a_hubspot_error_while_reading_becomes_a_tool_error_with_no_changeset(env, http):
    http.script("POST", "/deals/search", HubSpotError("boom", status_code=400))
    result = await _call(env, {"deal_or_company": "Acme Corp", "summary": "Call."})
    assert result.is_error is True and "HubSpot rejected POST" in _text(result)
    assert http.writes() == [] and (await get_store().list_pending(PORTAL)) == []


async def test_not_connected_returns_the_guidance_not_an_error(env, http):
    env["lf"]["auth_error"] = "No HubSpot portal is connected."
    env["lf"]["connect_url"] = "https://example.com/connect"
    result = await _call(env, FULL_CALL)
    assert result.is_error is False
    assert _data(result)["status"] == "not_connected" and "example.com/connect" in json.dumps(_data(result))
    assert http.calls == []


# --- registration ----------------------------------------------------------------


async def test_the_tool_is_registered_with_a_documented_schema(env):
    tools = {t.name: t for t in await env["server"].intent.list_tools()}
    tool = tools["pronova_log_meeting_outcome"]
    assert "Returns" in tool.description and len(tool.description) < 600
    schema = tool.input_schema
    assert set(schema["properties"]) == {
        "deal_or_company", "summary", "outcome", "close_date", "amount", "currency", "tasks", "new_contacts", "meeting_time",
    }
    assert schema["required"] == ["deal_or_company", "summary"]
    assert all(p.get("description") for p in schema["properties"].values())
    for model in ("TaskIn", "NewContactIn"):
        assert all(p.get("description") for p in schema["$defs"][model]["properties"].values()), model


def test_the_core_accepts_typed_inputs_directly():
    assert log_meeting._check_inputs(
        "ok", 5.0, "eur", [TaskIn(subject="x", due_date=date(2026, 11, 6))], [NewContactIn(first_name="A", last_name="B")]
    ) == "EUR"
