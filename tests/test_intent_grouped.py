"""The grouped surface (arm D): dispatch only, and the same write gate as ``/mcp``.

Pins what the benchmark's control arm promises: twelve tools built from existing
domain tools; each tool hands its arguments to the domain tool without computing
anything; a write returns the same preview as the full surface and touches HubSpot
only after ``write_actions(action="approve")``; an incomplete call is refused before
any HubSpot request; and the not-connected state answers with guidance, not an error.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import Client

from hubspot_mcp.client import APIResponse
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.intent import common

PORTAL = "99999999"

EXPECTED_TOOLS = {
    "deals",
    "contacts",
    "companies",
    "engagements",
    "associations",
    "pipelines",
    "owners",
    "manage_deal",
    "manage_contact",
    "manage_engagement",
    "write_actions",
    "get_sales_rules",
}


class RoutingHubSpot:
    """Stands in for ``HubSpotClient``: canned bodies per (METHOD, path), every call recorded."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Any] = {}
        self.calls: list[tuple[str, str, Any]] = []

    def route(self, method: str, path: str, body: Any) -> None:
        self.routes[(method.upper(), path)] = body

    def _answer(self, method: str, path: str, body: Any) -> APIResponse:
        self.calls.append((method, path, body))
        found = self.routes.get((method, path), {"results": []})
        return APIResponse(status_code=200, body=found(body) if callable(found) else found, headers={})

    async def get(self, path, portal_id=None, expected_scopes=None):
        return self._answer("GET", path, None)

    async def post(self, path, portal_id=None, body=None, expected_scopes=None):
        return self._answer("POST", path, body)

    async def patch(self, path, portal_id=None, body=None, expected_scopes=None):
        return self._answer("PATCH", path, body)

    async def put(self, path, portal_id=None, body=None, expected_scopes=None):
        return self._answer("PUT", path, body)

    async def delete(self, path, portal_id=None, expected_scopes=None):
        return self._answer("DELETE", path, None)

    async def close(self) -> None:
        pass

    def writes(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] != "GET" and not c[1].endswith(("/search", "/batch/read"))]


@pytest.fixture
def http() -> RoutingHubSpot:
    return RoutingHubSpot()


@pytest.fixture
def lifespan(tmp_path, monkeypatch, http):
    from hubspot_mcp import config, persistence, server
    from hubspot_mcp.state import set_store

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(persistence, "CONFIG_DIR", tmp_path)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("HUBSPOT_MCP_RULES_DIR", str(tmp_path / "rules"))
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / f"{PORTAL}.yaml").write_text("as_of: 2026-10-30\ndefault_owner: rep@example.com\n", encoding="utf-8")
    set_store(None)
    common.clear_caches()
    state = {
        "client": http,
        "cache": None,
        "portal_config": PortalConfig(portal_id=PORTAL, token="t", scopes_granted=[]),
        "portal_id": PORTAL,
        "auth_error": None,
    }
    monkeypatch.setattr(server, "_lifespan", lambda ctx: state)
    monkeypatch.setattr(server, "_session_resolver", None)
    yield state
    set_store(None)
    common.clear_caches()


@pytest.fixture
def server_mod(lifespan):
    from hubspot_mcp import server

    return server


def _data(result) -> Any:
    return result.structured_content if result.structured_content is not None else json.loads(result.content[0].text)


def _text(result) -> str:
    return result.content[0].text


async def call(server, tool: str, args: dict[str, Any], *, surface: str = "grouped"):
    async with Client(getattr(server, surface)) as client:
        return await client.call_tool(tool, args)


# --- the tool set ------------------------------------------------------------


async def test_the_surface_is_twelve_unprefixed_tools_and_none_of_them_is_pronova(server_mod):
    tools = await server_mod.grouped.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert len(tools) == 12


async def test_every_parameter_is_documented_and_context_never_reaches_the_schema(server_mod):
    for tool in await server_mod.grouped.list_tools():
        props = tool.input_schema.get("properties", {})
        assert "ctx" not in props, f"{tool.name} leaks ctx into its schema"
        for name, schema in props.items():
            assert schema.get("description"), f"{tool.name}.{name} has no description"


async def test_every_description_states_what_the_tool_returns_and_stays_short(server_mod):
    for tool in await server_mod.grouped.list_tools():
        text = tool.description or ""
        assert "Returns" in text, f"{tool.name} does not say what it returns"
        assert 80 <= len(text) <= 600, f"{tool.name} description is {len(text)} characters"


async def test_the_grouped_tools_are_not_registered_on_the_full_or_intent_surfaces(server_mod):
    full = {t.name for t in await server_mod.mcp.list_tools()}
    intent = {t.name for t in await server_mod.intent.list_tools()}
    assert not (EXPECTED_TOOLS & full), "grouped tools must not leak into /mcp"
    assert not (EXPECTED_TOOLS & intent), "grouped tools must not leak into /mcp/intent"


# --- reads dispatch, they do not compute ---------------------------------------


async def test_deals_with_an_id_fetches_that_deal_with_the_requested_properties(server_mod, http):
    http.route("GET", "/crm/v3/objects/deals/101?properties=amount%2Cclosedate", {"id": "101", "properties": {"amount": "1000"}})
    result = await call(server_mod, "deals", {"id": "101", "properties": ["amount", "closedate"]})
    assert result.is_error is False, _text(result)
    assert _data(result)["result"]["id"] == "101"
    assert [(m, p) for m, p, _ in http.calls] == [("GET", "/crm/v3/objects/deals/101?properties=amount%2Cclosedate")]


async def test_deals_without_an_id_sends_the_search_body_exactly_as_asked(server_mod, http):
    http.route("POST", "/crm/v3/objects/deals/search", {"results": [{"id": "1"}], "paging": {"next": {"after": "25"}}})
    filters = [
        {"propertyName": "hubspot_owner_id", "operator": "EQ", "value": "11"},
        {"propertyName": "closedate", "operator": "LT", "value": "1793318400000"},
    ]
    result = await call(
        server_mod,
        "deals",
        {"filters": filters, "properties": ["amount"], "limit": 50, "after": "25", "sort_by": "closedate", "sort_direction": "ASCENDING"},
    )
    assert result.is_error is False, _text(result)
    assert _data(result)["result"]["paging"]["next"]["after"] == "25", "paging comes back untouched"
    (method, path, body) = http.calls[0]
    assert (method, path) == ("POST", "/crm/v3/objects/deals/search")
    assert body == {
        "limit": 50,
        "filterGroups": [{"filters": filters}],
        "properties": ["amount"],
        "after": "25",
        "sorts": [{"propertyName": "closedate", "direction": "ASCENDING"}],
    }
    assert len(http.calls) == 1, "one search, no follow-up reads or paging on the caller's behalf"


@pytest.mark.parametrize(
    ("tool", "path"),
    [("contacts", "/crm/v3/objects/contacts/search"), ("companies", "/crm/v3/objects/companies/search")],
)
async def test_contacts_and_companies_search_their_own_object(server_mod, http, tool, path):
    result = await call(server_mod, tool, {"query": "Acme"})
    assert result.is_error is False, _text(result)
    assert http.calls[0][:2] == ("POST", path)
    assert http.calls[0][2] == {"limit": 25, "query": "Acme"}


@pytest.mark.parametrize("object_type", ["notes", "tasks", "calls", "emails", "meetings"])
async def test_engagements_search_each_engagement_type_through_the_object_endpoint(server_mod, http, object_type):
    filters = [{"propertyName": "hs_object_id", "operator": "IN", "values": ["7", "8"]}]
    result = await call(server_mod, "engagements", {"object_type": object_type, "filters": filters, "sort_by": "hs_timestamp"})
    assert result.is_error is False, _text(result)
    method, path, body = http.calls[0]
    assert (method, path) == ("POST", f"/crm/v3/objects/{object_type}/search")
    assert body["filterGroups"] == [{"filters": filters}]
    assert body["sorts"] == [{"propertyName": "hs_timestamp", "direction": "DESCENDING"}]


async def test_engagements_reject_an_unknown_object_type_before_any_request(server_mod, http):
    result = await call(server_mod, "engagements", {"object_type": "deals"})
    assert result.is_error is True
    assert http.calls == []


async def test_an_id_together_with_search_arguments_is_refused_before_any_request(server_mod, http):
    result = await call(server_mod, "deals", {"id": "101", "query": "Acme"})
    assert result.is_error is True
    assert "id fetches one record" in _text(result)
    assert http.calls == []


async def test_a_filter_without_an_operator_is_refused_before_any_request(server_mod, http):
    result = await call(server_mod, "deals", {"filters": [{"propertyName": "amount", "value": "1"}]})
    assert result.is_error is True
    assert "propertyName and operator" in _text(result)
    assert http.calls == []


async def test_limit_above_the_search_page_cap_is_refused(server_mod, http):
    result = await call(server_mod, "deals", {"limit": 201})
    assert result.is_error is True
    assert http.calls == []


async def test_pipelines_lists_or_fetches_one(server_mod, http):
    await call(server_mod, "pipelines", {})
    await call(server_mod, "pipelines", {"object_type": "deals", "pipeline_id": "p1"})
    assert [(m, p) for m, p, _ in http.calls] == [
        ("GET", "/crm/v3/pipelines/deals"),
        ("GET", "/crm/v3/pipelines/deals/p1"),
    ]


async def test_owners_reads_the_owners_endpoint_with_the_email_filter(server_mod, http):
    http.route("GET", "/crm/v3/owners?limit=100&email=rep%40example.com", {"results": [{"id": "11", "email": "rep@example.com"}]})
    result = await call(server_mod, "owners", {"email": "rep@example.com"})
    assert result.is_error is False, _text(result)
    assert _data(result)["result"]["results"][0]["id"] == "11"
    assert http.writes() == [], "owners is a read, so it must not reach the write gate"


async def test_associations_list_schema_and_the_missing_argument_errors(server_mod, http):
    await call(server_mod, "associations", {"action": "list", "from_object_type": "deals", "from_id": "101", "to_object_type": "notes"})
    await call(server_mod, "associations", {"action": "schema", "from_object_type": "notes", "to_object_type": "deals"})
    assert [(m, p) for m, p, _ in http.calls] == [
        ("GET", "/crm/v4/objects/deals/101/associations/notes"),
        ("GET", "/crm/v4/associations/notes/deals/labels"),
    ]
    before = len(http.calls)
    for args, needle in [
        ({"action": "list", "from_object_type": "deals", "to_object_type": "notes"}, "from_id"),
        ({"action": "create", "from_object_type": "deals", "from_id": "1", "to_object_type": "contacts"}, "to_id and association_type_id"),
    ]:
        result = await call(server_mod, "associations", args)
        assert result.is_error is True
        assert needle in _text(result)
    assert len(http.calls) == before


# --- writes: the same gate as /mcp ------------------------------------------------

ASSOC_TASK_TO_DEAL = [{"to": {"id": "101"}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 216}]}]


async def _preview(server_mod, tool: str, args: dict[str, Any], http: RoutingHubSpot) -> dict[str, Any]:
    result = await call(server_mod, tool, args)
    assert result.is_error is False, _text(result)
    data = _data(result)
    assert data.get("status") == "preview", data
    assert data.get("action_id")
    assert http.writes() == [], "nothing may be written before approval"
    return data


async def _approve(server_mod, preview: dict[str, Any]):
    args: dict[str, Any] = {"action": "approve", "action_id": preview["action_id"]}
    if preview.get("requires_count"):
        args["confirm_count"] = preview["impact_count"]
    return await call(server_mod, "write_actions", args)


def _normalise(data: dict[str, Any]) -> dict[str, Any]:
    """The response with the minted action id masked, so two calls compare equal."""
    text = json.dumps(data, sort_keys=True, default=str).replace(data["action_id"], "<id>")
    out = json.loads(text)
    out.pop("expires_at", None)
    out.pop("created_at", None)
    echoed = (out.get("preview") or {}).get("input")
    if isinstance(echoed, dict):
        # The /mcp wrapper echoes optional arguments it defaulted to None; the payload is the same.
        out["preview"]["input"] = {k: v for k, v in echoed.items() if v is not None}
    return out


GATE_CASES = {
    "update_deal": (
        ("manage_deal", {"action": "update", "id": "101", "properties": {"closedate": "2026-11-30"}}),
        ("hubspot_update_object", {"object_id": "101", "object_type": "deals", "properties": {"closedate": "2026-11-30"}}),
    ),
    "update_deal_amount": (
        ("manage_deal", {"action": "update", "id": "101", "properties": {"amount": "5"}}),
        ("hubspot_update_object", {"object_id": "101", "object_type": "deals", "properties": {"amount": "5"}}),
    ),
    "batch_update_deals": (
        ("manage_deal", {"action": "batch_update", "records": [{"id": "1", "properties": {"closedate": "2026-11-30"}}]}),
        (
            "hubspot_bulk_update_objects",
            {"object_type": "deals", "records": [{"id": "1", "properties": {"closedate": "2026-11-30"}}]},
        ),
    ),
    "create_contact": (
        ("manage_contact", {"action": "create", "properties": {"email": "ada@example.com"}}),
        ("hubspot_create_object", {"object_type": "contacts", "properties": {"email": "ada@example.com"}}),
    ),
    "create_task": (
        (
            "manage_engagement",
            {
                "action": "create",
                "object_type": "tasks",
                "subject": "Confirm timing",
                "status": "NOT_STARTED",
                "timestamp": "2026-11-06T09:00:00Z",
                "associations": ASSOC_TASK_TO_DEAL,
            },
        ),
        (
            "hubspot_create_task",
            {
                "subject": "Confirm timing",
                "status": "NOT_STARTED",
                "timestamp": "2026-11-06T09:00:00Z",
                "associations": ASSOC_TASK_TO_DEAL,
            },
        ),
    ),
    "create_association": (
        (
            "associations",
            {
                "action": "create",
                "from_object_type": "contacts",
                "from_id": "900",
                "to_object_type": "deals",
                "to_id": "101",
                "association_type_id": "4",
            },
        ),
        (
            "hubspot_associate_records",
            {
                "from_object_type": "contacts",
                "from_object_id": "900",
                "to_object_type": "deals",
                "to_object_id": "101",
                "association_type_id": "4",
            },
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(GATE_CASES))
async def test_every_write_gets_the_same_response_as_the_full_surface_apart_from_the_action_id(server_mod, http, case):
    http.route("GET", "/crm/v3/objects/deals/101?properties=closedate", {"id": "101", "properties": {"closedate": "2026-10-01"}})
    http.route("GET", "/crm/v3/objects/deals/101?properties=amount", {"id": "101", "properties": {"amount": "100"}})
    (grouped_tool, grouped_args), (full_tool, full_args) = GATE_CASES[case]

    full = await call(server_mod, full_tool, full_args, surface="mcp")
    grouped = await call(server_mod, grouped_tool, grouped_args)
    assert full.is_error is False, _text(full)
    assert grouped.is_error is False, _text(grouped)
    assert _data(grouped)["status"] in {"preview", "applied"}
    assert _normalise(_data(grouped)) == _normalise(_data(full))


async def test_manage_deal_update_returns_a_preview_and_writes_only_after_approval(server_mod, http):
    http.route("GET", "/crm/v3/objects/deals/101?properties=amount", {"id": "101", "properties": {"amount": "100"}})
    preview = await _preview(
        server_mod, "manage_deal", {"action": "update", "id": "101", "properties": {"amount": "5"}}, http
    )
    assert preview["approval_tier"] == "CONFIRM", "amount is sensitive, so it is never auto-applied"

    approved = await _approve(server_mod, preview)
    assert approved.is_error is False, _text(approved)
    assert http.writes() == [("PATCH", "/crm/v3/objects/deals/101", {"properties": {"amount": "5"}})]


async def test_an_update_that_cannot_be_undone_needs_the_impact_count_to_approve(server_mod, http):
    """No snapshot route, so the originals are not captured and the write is not undoable: full gate."""
    preview = await _preview(
        server_mod, "manage_deal", {"action": "update", "id": "101", "properties": {"closedate": "2026-11-30"}}, http
    )
    assert preview["approval_tier"] == "FULL_GATE"
    assert preview["requires_count"] is True

    bare = await call(server_mod, "write_actions", {"action": "approve", "action_id": preview["action_id"]})
    assert bare.is_error is True
    assert "impact count" in _text(bare)
    wrong = await call(
        server_mod, "write_actions", {"action": "approve", "action_id": preview["action_id"], "confirm_count": 7}
    )
    assert wrong.is_error is True
    assert http.writes() == []

    right = await _approve(server_mod, preview)
    assert right.is_error is False, _text(right)
    assert [(m, p) for m, p, _ in http.writes()] == [("PATCH", "/crm/v3/objects/deals/101")]


async def test_manage_deal_batch_update_previews_then_posts_one_batch_on_approval(server_mod, http):
    http.route("POST", "/crm/v3/objects/deals/batch/update", {"results": [{"id": "1"}, {"id": "2"}]})
    preview = await _preview(
        server_mod,
        "manage_deal",
        {
            "action": "batch_update",
            "records": [
                {"id": "1", "properties": {"closedate": "2026-11-30"}},
                {"id": 2, "properties": {"closedate": "2026-11-30"}},
            ],
        },
        http,
    )
    approved = await _approve(server_mod, preview)
    assert approved.is_error is False, _text(approved)
    writes = http.writes()
    assert [(m, p) for m, p, _ in writes] == [("POST", "/crm/v3/objects/deals/batch/update")]
    assert writes[0][2]["inputs"] == [
        {"id": "1", "properties": {"closedate": "2026-11-30"}},
        {"id": "2", "properties": {"closedate": "2026-11-30"}},
    ]


async def test_a_batch_update_captures_each_deals_current_values_so_it_can_be_undone(server_mod, http):
    """A batch write on the grouped surface must be as reversible as one on /mcp (hubspot_bulk_update_objects)."""
    http.route("GET", "/crm/v3/objects/deals/1?properties=closedate", {"id": "1", "properties": {"closedate": "2026-10-01"}})
    http.route("GET", "/crm/v3/objects/deals/2?properties=closedate", {"id": "2", "properties": {"closedate": "2026-10-02"}})
    http.route("POST", "/crm/v3/objects/deals/batch/update", {"results": [{"id": "1"}, {"id": "2"}]})
    result = await call(
        server_mod,
        "manage_deal",
        {
            "action": "batch_update",
            "records": [
                {"id": "1", "properties": {"closedate": "2026-11-30"}},
                {"id": "2", "properties": {"closedate": "2026-11-30"}},
            ],
        },
    )
    assert result.is_error is False, _text(result)
    preview = _data(result)
    # Undoable and small, so /mcp's bounded-autonomy rule applies it at once; the originals were read first.
    assert preview["status"] == "applied" and preview["tool"] == "hubspot_bulk_update_objects"
    assert [p for m, p, _ in http.calls if m == "GET"] == [
        "/crm/v3/objects/deals/1?properties=closedate",
        "/crm/v3/objects/deals/2?properties=closedate",
    ]
    undone = await call(server_mod, "write_actions", {"action": "undo", "action_id": preview["action_id"]})
    assert undone.is_error is False, _text(undone)
    restores = {p: b for m, p, b in http.writes() if m == "PATCH"}
    assert restores == {
        "/crm/v3/objects/deals/1": {"properties": {"closedate": "2026-10-01"}},
        "/crm/v3/objects/deals/2": {"properties": {"closedate": "2026-10-02"}},
    }


async def test_a_search_with_more_filters_than_one_group_allows_is_refused_before_any_request(server_mod, http):
    seven = [{"propertyName": f"p{n}", "operator": "HAS_PROPERTY"} for n in range(7)]
    result = await call(server_mod, "deals", {"filters": seven})
    assert result.is_error is True and "at most 6 filters" in _text(result)
    assert http.calls == []
    six = await call(server_mod, "deals", {"filters": seven[:6]})
    assert six.is_error is False, _text(six)


async def test_a_single_create_is_applied_by_the_same_bounded_autonomy_rule_as_the_full_surface(server_mod, http):
    """/mcp auto-applies a one-record create (act and notify). The grouped surface must not be stricter or looser."""
    http.route("POST", "/crm/v3/objects/contacts", {"id": "900", "properties": {}})
    result = await call(
        server_mod,
        "manage_contact",
        {"action": "create", "properties": {"firstname": "Ada", "lastname": "Example", "email": "ada@example.com"}},
    )
    assert result.is_error is False, _text(result)
    assert _data(result)["status"] == "applied"
    assert http.writes() == [
        ("POST", "/crm/v3/objects/contacts", {"properties": {"firstname": "Ada", "lastname": "Example", "email": "ada@example.com"}})
    ]
    pending = await call(server_mod, "write_actions", {"action": "list"})
    assert _data(pending)["pending"] == [], "an applied write leaves nothing pending"


async def test_manage_engagement_create_task_sends_its_associations_and_due_date(server_mod, http):
    http.route("POST", "/crm/v3/objects/tasks", {"id": "55", "properties": {}})
    result = await call(
        server_mod,
        "manage_engagement",
        {
            "action": "create",
            "object_type": "tasks",
            "subject": "Confirm timing",
            "status": "NOT_STARTED",
            "timestamp": "2026-11-06T09:00:00Z",
            "associations": ASSOC_TASK_TO_DEAL,
        },
    )
    assert result.is_error is False, _text(result)
    method, path, body = http.writes()[0]
    assert (method, path) == ("POST", "/crm/v3/objects/tasks")
    assert body["associations"] == ASSOC_TASK_TO_DEAL
    assert body["properties"]["hs_task_subject"] == "Confirm timing"
    assert body["properties"]["hs_timestamp"] == "2026-11-06T09:00:00Z"


async def test_associations_create_is_a_write_that_waits_for_approval(server_mod, http):
    preview = await _preview(
        server_mod,
        "associations",
        {
            "action": "create",
            "from_object_type": "contacts",
            "from_id": "900",
            "to_object_type": "deals",
            "to_id": "101",
            "association_type_id": "4",
        },
        http,
    )
    approved = await _approve(server_mod, preview)
    assert approved.is_error is False, _text(approved)
    assert [(m, p) for m, p, _ in http.writes()] == [("PUT", "/crm/v4/objects/contacts/900/associations/deals/101")]


async def test_reject_discards_a_preview_and_nothing_is_ever_written(server_mod, http):
    http.route("GET", "/crm/v3/objects/deals/101?properties=amount", {"id": "101", "properties": {"amount": "100"}})
    preview = await _preview(
        server_mod, "manage_deal", {"action": "update", "id": "101", "properties": {"amount": "5"}}, http
    )
    listed = await call(server_mod, "write_actions", {"action": "list"})
    assert preview["action_id"] in json.dumps(_data(listed))

    rejected = await call(server_mod, "write_actions", {"action": "reject", "action_id": preview["action_id"]})
    assert rejected.is_error is False, _text(rejected)
    again = await _approve(server_mod, preview)
    assert again.is_error is True, "a rejected preview cannot be approved"
    assert http.writes() == []


async def test_undo_restores_the_values_an_approved_update_replaced(server_mod, http):
    http.route("GET", "/crm/v3/objects/deals/101?properties=amount", {"id": "101", "properties": {"amount": "100"}})
    preview = await _preview(
        server_mod, "manage_deal", {"action": "update", "id": "101", "properties": {"amount": "5"}}, http
    )
    await _approve(server_mod, preview)
    undone = await call(server_mod, "write_actions", {"action": "undo", "action_id": preview["action_id"]})
    assert undone.is_error is False, _text(undone)
    patches = [b for m, _, b in http.writes() if m == "PATCH"]
    assert patches[0] == {"properties": {"amount": "5"}}
    assert patches[-1] == {"properties": {"amount": "100"}}


async def test_approving_an_unknown_action_id_is_an_error_without_a_write(server_mod, http):
    result = await call(server_mod, "write_actions", {"action": "approve", "action_id": "nope"})
    assert result.is_error is True
    assert http.writes() == []



# --- incomplete calls never reach HubSpot --------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args", "needle"),
    [
        ("manage_deal", {"action": "create"}, "properties"),
        ("manage_deal", {"action": "update", "properties": {"amount": "1"}}, "id and properties"),
        ("manage_deal", {"action": "batch_update"}, "records"),
        ("manage_deal", {"action": "batch_update", "records": [{"properties": {"amount": "1"}}]}, "id and a non-empty properties"),
            ("manage_deal", {"action": "batch_update", "records": [{"id": "1", "properties": {}}]}, "id and a non-empty properties"),
        ("manage_contact", {"action": "update", "id": "9"}, "id and properties"),
        ("manage_engagement", {"action": "create", "object_type": "notes"}, "body"),
        ("manage_engagement", {"action": "create", "object_type": "tasks", "subject": "x"}, "status, timestamp"),
        ("manage_engagement", {"action": "create", "object_type": "meetings", "title": "x"}, "start_time"),
        ("manage_engagement", {"action": "create", "object_type": "calls", "title": "x"}, "duration_ms"),
        ("manage_engagement", {"action": "update", "object_type": "notes"}, "id and properties"),
        ("write_actions", {"action": "approve"}, "action_id"),
        ("write_actions", {"action": "undo"}, "action_id"),
    ],
)
async def test_a_call_missing_what_its_action_needs_is_refused_before_any_request(server_mod, http, tool, args, needle):
    result = await call(server_mod, tool, args)
    assert result.is_error is True
    assert needle in _text(result)
    assert http.calls == []


# --- sales rules, same as arm C -------------------------------------------------------


async def test_get_sales_rules_returns_the_rules_their_markdown_and_the_hash(server_mod, http):
    from hubspot_mcp.intent import rules as rules_mod

    result = await call(server_mod, "get_sales_rules", {})
    assert result.is_error is False, _text(result)
    data = _data(result)
    loaded = rules_mod.load_rules(PORTAL)
    assert data["as_of"] == "2026-10-30"
    assert data["rules_hash"] == rules_mod.rules_hash(loaded)
    assert data["rules"]["silent_days"] == loaded.silent_days
    assert data["markdown"] == rules_mod.render_sales_rules_md(loaded)
    assert http.calls == [], "the rules are read from the rules file, not HubSpot"


# --- state of the session ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("deals", {}),
        ("owners", {}),
        ("manage_deal", {"action": "update", "id": "1", "properties": {"amount": "1"}}),
        ("write_actions", {"action": "list"}),
        ("write_actions", {"action": "approve", "action_id": "abc"}),
        ("get_sales_rules", {}),
    ],
)
async def test_not_connected_answers_with_guidance_and_touches_nothing(server_mod, lifespan, http, tool, args):
    lifespan["auth_error"] = "No HubSpot account is connected for this user."
    lifespan["connect_url"] = "https://example.com/connect?ticket=t"
    result = await call(server_mod, tool, args)
    assert result.is_error is False, _text(result)
    data = _data(result)
    assert data["status"] == "not_connected"
    assert data["connect_url"] == "https://example.com/connect?ticket=t"
    assert http.calls == []


async def test_a_broken_credential_without_a_connect_link_is_an_error(server_mod, lifespan, http):
    lifespan["auth_error"] = "Token revoked."
    result = await call(server_mod, "deals", {})
    assert result.is_error is True
    assert http.calls == []


async def test_grouped_calls_are_traced_as_grouped(server_mod, monkeypatch):
    from hubspot_mcp.trace import get_recent_traces

    monkeypatch.setenv("HUBSPOT_MCP_TRACE", "1")
    result = await call(server_mod, "deals", {"id": "101"})
    assert result.is_error is False, _text(result)
    events = [e for e in get_recent_traces(PORTAL) if e.event_type == "tool_call"]
    assert [(e.data["tool_name"], e.data["mode"]) for e in events] == [("hubspot_get_object", "grouped")]
