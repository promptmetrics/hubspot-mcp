"""bench/portal_read.py: the read-only portal reader behind the intent benchmark's end-state checks.

A fake HubSpot behind httpx.MockTransport stands in for the portal; placeholder data only.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
import portal_read as pr  # noqa: E402

TOKEN = "tok-fake-123"
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
SINCE = NOW.isoformat()
OLD = int((NOW - timedelta(days=30)).timestamp() * 1000)
NEW = int((NOW + timedelta(minutes=5)).timestamp() * 1000)
TYPE_BY_ID = {v: k for k, v in pr.OBJ_TYPE.items()}


class FakePortal:
    def __init__(self) -> None:
        self.objs: dict[str, dict[str, dict]] = {k: {} for k in pr.OBJ_TYPE}
        self.assoc: dict[tuple[str, str], dict[str, list[str]]] = {}
        self.requests: list[tuple[str, str]] = []
        self.fail_next: list[int] = []
        self.hidden_from_search: set[tuple[str, str]] = set()
        self.index_lag: dict[tuple[str, str], dict] = {}  # what search still shows, by (kind, id)
        self.filter_lag: dict[tuple[str, str], dict] = {}  # what search filters still match on, by (kind, id)

    def add(self, kind: str, rec_id: str, props: dict, *, tagged: bool = False, created: int = OLD) -> None:
        props = {**props, **({pr.TAG_PROPERTY: pr.TAG} if tagged else {})}
        self.objs[kind][rec_id] = {"id": rec_id, "properties": props, "created": created}

    def link(self, src: str, rec_id: str, dst: str, to_ids: list[str]) -> None:
        self.assoc.setdefault((src, dst), {})[rec_id] = to_ids

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), text=f"boom {TOKEN}")
        body = json.loads(request.content) if request.content else {}
        if path == "/crm/v3/pipelines/deals":
            return httpx.Response(200, json={"results": [{"id": "p1", "label": "Sales", "stages": [
                {"id": "s1", "label": "Proposal"}, {"id": "s2", "label": "Won"}]}]})
        if path.endswith("/search"):
            kind = path.split("/")[4]
            flt = body["filterGroups"][0]["filters"][0]
            if flt["propertyName"] == pr.TAG_PROPERTY:
                hits = [o for o in self.objs[kind].values() if o["properties"].get(pr.TAG_PROPERTY) == pr.TAG]
                for extra in body["filterGroups"][0]["filters"][1:]:  # closedate LT <epoch ms>, matched on the index
                    cutoff = datetime.fromtimestamp(int(extra["value"]) / 1000, UTC).strftime("%Y-%m-%d")
                    hits = [o for o in hits if str({**o["properties"], **self.filter_lag.get((kind, o["id"]), {})}
                                                   .get(extra["propertyName"]) or "9999")[:10] < cutoff]
                hits = [{**o, "properties": {**o["properties"], **self.index_lag.get((kind, o["id"]), {})}} for o in hits]
            elif flt["operator"] == "EQ":
                wanted = {f["propertyName"]: f["value"] for f in body["filterGroups"][0]["filters"]}
                hits = [o for o in self.objs[kind].values() if all(o["properties"].get(k) == v for k, v in wanted.items())]
            else:
                hits = [o for o in self.objs[kind].values()
                        if o["created"] >= int(flt["value"]) and (kind, o["id"]) not in self.hidden_from_search]
            return httpx.Response(200, json={"results": [{"id": o["id"], "properties": o["properties"]} for o in hits]})
        if path.endswith("/batch/read") and "/crm/v3/objects/" in path:
            kind = path.split("/")[4]
            found = [self.objs[kind][i["id"]] for i in body["inputs"] if i["id"] in self.objs[kind]]
            return httpx.Response(200, json={"results": [{"id": o["id"], "properties": o["properties"]} for o in found]})
        if "/crm/associations/" in path:
            parts = path.split("/")
            src, dst = TYPE_BY_ID[parts[4]], TYPE_BY_ID[parts[5]]
            rows = [{"from": {"id": i["id"]}, "to": [{"toObjectId": t} for t in self.assoc.get((src, dst), {}).get(i["id"], [])]}
                    for i in body["inputs"]]
            return httpx.Response(200, json={"results": rows})
        return httpx.Response(404, text="unknown")

    def reader(self) -> pr.PortalReader:
        return pr.PortalReader(TOKEN, transport=httpx.MockTransport(self.handler), sleep=lambda s: None,
                               settle_seconds=0.0)


@pytest.fixture
def portal() -> FakePortal:
    p = FakePortal()
    p.add("deals", "d1", {"dealname": "Acme Corp renewal", "amount": "20000", "closedate": "2026-11-02T00:00:00Z",
                          "pipeline": "p1", "dealstage": "s1", "hubspot_owner_id": "11"}, tagged=True)
    p.add("deals", "d2", {"dealname": "Globex expansion", "amount": "5000", "closedate": "2026-12-01T00:00:00Z",
                          "pipeline": "p1", "dealstage": "s1"}, tagged=True)
    p.add("companies", "c1", {"name": "Acme Corp", "domain": "acme.example.com"}, tagged=True)
    p.add("contacts", "k1", {"email": "jane@acme.example.com", "firstname": "Jane", "lastname": "Doe",
                             "company": "Acme Corp"}, tagged=True)
    return p


def add_session_writes(portal: FakePortal) -> None:
    """What a good job-3 session leaves behind: a changed deal, a task, a meeting and a new contact."""
    portal.objs["deals"]["d1"]["properties"].update(closedate="2026-12-15T00:00:00Z", amount="24000.0")
    portal.add("tasks", "t1", {"hs_task_subject": "Send security doc", "hs_task_status": "NOT_STARTED",
                               "hs_timestamp": "2026-11-13T09:00:00Z", "hubspot_owner_id": "11"}, created=NEW)
    portal.add("meetings", "m1", {"hs_meeting_title": "Pilot call", "hs_timestamp": "2026-11-10T08:00:00Z"}, created=NEW)
    portal.add("contacts", "k2", {"email": "maria@acme.example.com", "firstname": "Maria", "lastname": "Lopez",
                                  "jobtitle": "CFO"}, created=NEW)
    portal.link("tasks", "t1", "deals", ["d1"])
    portal.link("tasks", "t1", "companies", ["c1"])
    portal.link("tasks", "t1", "contacts", ["k2"])
    portal.link("meetings", "m1", "deals", ["d1"])
    portal.link("contacts", "k2", "companies", ["c1"])
    portal.link("contacts", "k2", "deals", ["d1"])


def test_baseline_keys_records_by_natural_key_and_maps_stage_labels(portal):
    data = portal.reader().baseline()
    assert set(data["deals"]) == {"Acme Corp renewal", "Globex expansion"}
    assert data["deals"]["Acme Corp renewal"]["props"]["dealstage"] == "Proposal"
    assert data["deals"]["Acme Corp renewal"]["props"]["pipeline"] == "Sales"
    assert set(data["contacts"]) == {"jane@acme.example.com"}
    assert set(data["companies"]) == {"Acme Corp"}


@pytest.mark.parametrize(("method", "path"), [
    ("PATCH", "/crm/v3/objects/deals/d1"),
    ("POST", "/crm/v3/objects/deals"),
    ("POST", "/crm/v3/objects/deals/batch/update"),
    ("POST", "/crm/v3/objects/deals/batch/archive"),
    ("DELETE", "/crm/v3/objects/deals/d1"),
    ("PUT", "/crm/associations/2026-09/0-3/0-1/batch/associate/default"),
])
def test_a_write_request_raises_before_anything_is_sent(portal, method, path):
    with pytest.raises(pr.WriteAttempt):
        portal.reader()._request(method, path, {})
    assert portal.requests == []


def test_the_client_exposes_no_write_method():
    public = {n for n in dir(pr.PortalReader) if not n.startswith("_")}
    assert not {n for n in public if n.startswith(("create", "update", "delete", "archive", "patch", "put", "write"))}


def test_end_state_reports_the_changed_deal_the_created_records_and_their_associations(portal):
    reader = portal.reader()
    reader.baseline()
    add_session_writes(portal)

    state = reader.end_state(SINCE, ["Acme Corp renewal"])

    assert state["tz"] == "Europe/Berlin" and state["stable"] is True and state["rounds"] == 2
    assert state["target_names"] == ["Acme Corp renewal"]
    assert state["deals_target"]["Acme Corp renewal"]["closedate"] == "2026-12-15T00:00:00Z"
    assert state["deals_changed"] == {"Acme Corp renewal": {"changes": {
        "amount": {"from": "20000", "to": "24000.0"},
        "closedate": {"from": "2026-11-02T00:00:00Z", "to": "2026-12-15T00:00:00Z"},
    }}}
    assert state["deals_missing"] == [] and state["deals_created"] == []
    assert state["contacts_changed"] == {} and state["companies_changed"] == {}
    (task,) = state["created"]["tasks"]
    assert task["title"] == "Send security doc" and task["status"] == "NOT_STARTED" and task["due"] == "2026-11-13T09:00:00Z"
    assert task["associations"] == {"deals": ["Acme Corp renewal"], "companies": ["Acme Corp"], "contacts": ["Maria Lopez"]}
    (meeting,) = state["created"]["meetings"]
    assert meeting["associations"]["deals"] == ["Acme Corp renewal"]
    (contact,) = state["created"]["contacts"]
    assert (contact["firstname"], contact["lastname"], contact["jobtitle"]) == ("Maria", "Lopez", "CFO")
    assert contact["associations"] == {"companies": ["Acme Corp"], "deals": ["Acme Corp renewal"]}
    assert state["counts"] == {"tasks": 1, "notes": 0, "meetings": 1, "calls": 0, "emails": 0, "contacts": 1, "deals_created": 0}


def test_end_state_waits_until_two_reads_agree_and_flags_a_read_that_never_settles(portal):
    reader = portal.reader()
    reader.baseline()
    rounds = iter(range(100))
    original = reader._round

    def moving(since, targets):
        state = original(since, targets)
        state["counts"]["tasks"] = next(rounds)
        return state

    reader._round = moving  # type: ignore[method-assign]
    state = reader.end_state(SINCE, [])
    assert state["stable"] is False and state["rounds"] == pr.MAX_ROUNDS


def test_a_deleted_deal_is_reported_missing_and_a_new_deal_as_created(portal):
    reader = portal.reader()
    reader.baseline()
    del portal.objs["deals"]["d2"]
    portal.add("deals", "d9", {"dealname": "Initech pilot", "amount": "1"}, created=NEW)
    state = reader.end_state(SINCE, [])
    assert state["deals_missing"] == ["Globex expansion"]
    assert [d["dealname"] for d in state["deals_created"]] == ["Initech pilot"]


def test_a_changed_seeded_contact_is_reported_by_email(portal):
    reader = portal.reader()
    reader.baseline()
    portal.objs["contacts"]["k1"]["properties"]["phone"] = "+49 30 0000"
    state = reader.end_state(SINCE, [])
    assert state["contacts_changed"] == {"jane@acme.example.com": {
        "name": "Jane Doe", "company": "Acme Corp", "changes": {"phone": {"from": None, "to": "+49 30 0000"}}}}


def test_read_check_is_ok_when_nothing_changed_and_names_what_did_otherwise(portal):
    reader = portal.reader()
    reader.baseline()
    quiet = reader.read_check(SINCE)
    assert quiet == {"ok": True, "deals_changed": 0, "contacts_changed": 0, "companies_changed": 0,
                     "created": {"tasks": 0, "notes": 0, "meetings": 0, "calls": 0, "emails": 0, "contacts": 0},
                     "changed_names": []}
    portal.objs["deals"]["d1"]["properties"]["closedate"] = "2026-12-31T00:00:00Z"
    portal.add("tasks", "t1", {"hs_task_subject": "x"}, created=NEW)
    loud = reader.read_check(SINCE)
    assert loud["ok"] is False
    assert loud["deals_changed"] == 1 and loud["created"]["tasks"] == 1
    assert loud["changed_names"] == ["deals:Acme Corp renewal"]


def test_verify_baseline_ignores_engagements_and_flags_a_changed_seed_record(portal):
    reader = portal.reader()
    reader.baseline()
    portal.add("tasks", "t1", {"hs_task_subject": "x"}, created=NEW)
    assert reader.verify_baseline() == {"ok": True, "differs": [], "unrestorable": []}
    portal.objs["companies"]["c1"]["properties"]["domain"] = "other.example.com"
    assert reader.verify_baseline() == {"ok": False, "differs": ["companies:Acme Corp"], "unrestorable": []}


def test_verify_baseline_ignores_properties_restore_py_never_rewrites(portal):
    reader = portal.reader()
    reader.baseline()
    portal.objs["deals"]["d1"]["properties"]["description"] = "stray write"
    portal.objs["deals"]["d1"]["properties"]["dealtype"] = "newbusiness"
    portal.objs["companies"]["c1"]["properties"]["hubspot_owner_id"] = "99"
    state = reader.verify_baseline()
    assert state["ok"] is True and state["differs"] == []
    assert state["unrestorable"] == ["deals:Acme Corp renewal", "companies:Acme Corp"]
    portal.objs["deals"]["d1"]["properties"]["amount"] = "1"
    assert reader.verify_baseline()["differs"] == ["deals:Acme Corp renewal"]


def test_amounts_compare_as_numbers_and_unset_values_are_equal():
    assert pr.same_value("24000", "24000.0") and pr.same_value(None, "") and not pr.same_value("1", "2")
    assert pr.diff_props({"a": "1", "b": None}, {"a": "1.0", "b": ""}) == {}


def test_a_429_is_retried_and_a_persistent_5xx_raises_without_the_token(portal):
    reader = portal.reader()
    portal.fail_next = [429]
    assert reader.baseline()["deals"]
    portal.fail_next = [503, 503, 503, 503]
    with pytest.raises(pr.ReadError) as err:
        portal.reader().baseline()
    assert TOKEN not in str(err.value)


def test_a_4xx_body_is_scrubbed_of_the_token(portal):
    portal.fail_next = [403]
    with pytest.raises(pr.ReadError) as err:
        portal.reader().baseline()
    assert TOKEN not in str(err.value) and "<redacted>" in str(err.value)


def test_search_requests_are_spaced_to_at_most_four_per_second(portal):
    sleeps: list[float] = []
    clock = iter([0.0, 0.0, 0.0, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1])
    reader = pr.PortalReader(TOKEN, transport=httpx.MockTransport(portal.handler), sleep=sleeps.append,
                             clock=lambda: next(clock, 9.9), settle_seconds=0.0)
    reader.search_tagged("deals", ["dealname"])
    reader.search_tagged("deals", ["dealname"])
    assert any(0 < s <= pr.SEARCH_GAP for s in sleeps)


def test_search_index_differs_names_a_deal_search_still_shows_with_the_benchmarks_value(portal):
    reader = portal.reader()
    reader.baseline()
    assert reader.search_index_differs() == []
    portal.index_lag[("deals", "d2")] = {"closedate": "2026-12-31T00:00:00Z"}
    assert reader.search_index_differs() == ["deals:Globex expansion"]


def test_search_index_differs_names_an_archived_contact_search_still_returns(portal):
    reader = portal.reader()
    reader.baseline()
    portal.add("contacts", "k9", {"firstname": "Maria", "lastname": "Lopez"}, created=NEW)
    assert reader.search_index_differs((("Maria", "Lopez"),)) == ["contacts:Maria Lopez still in search"]
    del portal.objs["contacts"]["k9"]
    assert reader.search_index_differs((("Maria", "Lopez"),)) == []


def test_wait_search_index_polls_until_the_index_catches_up_and_gives_up_after_the_timeout(portal):
    clock = [0.0]
    reader = pr.PortalReader(TOKEN, transport=httpx.MockTransport(portal.handler), settle_seconds=0.0,
                             sleep=lambda s: clock.__setitem__(0, clock[0] + s), clock=lambda: clock[0])
    reader.baseline()
    portal.index_lag[("deals", "d1")] = {"amount": "24000"}
    gave_up = reader.wait_search_index(timeout=30, interval=10)
    assert gave_up["ok"] is False and gave_up["differs"] == ["deals:Acme Corp renewal"] and gave_up["waited_s"] >= 30
    portal.index_lag.clear()
    assert reader.wait_search_index(timeout=30, interval=10)["ok"] is True


def test_a_closedate_filter_that_lags_the_values_is_caught_when_as_of_is_given(portal):
    reader = portal.reader()
    reader.baseline()
    assert reader.search_index_differs(as_of="2026-11-10") == []
    # Values already restored, but the filter index still holds the benchmark's new close date for the past-due deal.
    portal.filter_lag[("deals", "d1")] = {"closedate": "2026-12-31T00:00:00Z"}
    assert reader.search_index_differs() == []
    assert reader.search_index_differs(as_of="2026-11-10") == [
        "deals:closedate<2026-11-10 filter finds 0 of 1 (1 missing, 0 extra)"]
