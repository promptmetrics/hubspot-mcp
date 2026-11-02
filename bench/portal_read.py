"""Read-only view of the seeded benchmark portal: baseline, end state after a write task, read check.

``PortalReader`` issues GETs, ``/search`` POSTs and ``/batch/read`` POSTs and nothing else; any other
request raises ``WriteAttempt`` before it leaves the process. The runner reads the portal this way after a
write session (before restore.py puts the seed state back) and after a read session (to confirm the arm
did not write). Records are keyed by natural keys (deal name, contact email, company name), so the result does
not depend on HubSpot ids. The seed tag and the base URL come from ``demo/seed/seed.py`` (read, not imported).

The token is sent as a bearer header and never logged; error text is scrubbed of it.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

API = "https://api.hubapi.com"
TAG_PROPERTY = "seed_tag"
TAG = "hubspot-forecast-demo"
TZ = "Europe/Berlin"
SEARCH_GAP = 0.25  # at most 4 search requests per second
CLOCK_SKEW = timedelta(seconds=60)
MAX_ROUNDS = 6
RETRIES = 3

OBJ_TYPE = {"deals": "0-3", "contacts": "0-1", "companies": "0-2", "notes": "0-46", "tasks": "0-27",
            "meetings": "0-47", "calls": "0-48", "emails": "0-49"}
ASSOC_PATH = "/crm/associations/2026-09/{src}/{dst}/batch/read"

DEAL_PROPS = ["dealname", "amount", "deal_currency_code", "closedate", "pipeline", "dealstage", "hubspot_owner_id",
              "hs_manual_forecast_category", "hs_next_step", "description", "dealtype"]
CONTACT_PROPS = ["email", "firstname", "lastname", "jobtitle", "phone", "company", "hubspot_owner_id"]
COMPANY_PROPS = ["name", "domain", "hubspot_owner_id"]
TABLE_PROPS = {"deals": DEAL_PROPS, "contacts": CONTACT_PROPS, "companies": COMPANY_PROPS}
# What demo/seed/restore.py puts back (seed.py SNAPSHOT_*_PROPERTIES). verify_baseline compares only these: a stray
# write to any other property (deal description, contact owner) would otherwise fail every later session as
# stale_state, since a restore cannot clear it. The wider TABLE_PROPS still feed baseline, end_state and the grader.
RESTORED_PROPS = {
    "deals": frozenset({"dealname", "amount", "closedate", "dealstage", "pipeline", "hs_manual_forecast_category",
                        "hubspot_owner_id", "hs_next_step", "deal_currency_code"}),
    "contacts": frozenset({"firstname", "lastname", "email", "phone", "jobtitle"}),
    "companies": frozenset({"name", "domain"}),
}
ENGAGEMENT_PROPS = {
    "tasks": ["hs_task_subject", "hs_task_body", "hs_task_status", "hs_task_priority", "hs_timestamp", "hubspot_owner_id"],
    "notes": ["hs_note_body", "hs_timestamp"],
    "meetings": ["hs_meeting_title", "hs_meeting_body", "hs_meeting_outcome", "hs_meeting_start_time",
                 "hs_meeting_end_time", "hs_timestamp"],
    "calls": ["hs_call_title", "hs_call_body", "hs_call_status", "hs_call_direction", "hs_timestamp"],
    "emails": ["hs_email_subject", "hs_email_text", "hs_email_direction", "hs_timestamp"],
}
ENGAGEMENT_KINDS = tuple(ENGAGEMENT_PROPS)
ASSOC_TARGETS = ("deals", "companies", "contacts")


class WriteAttempt(RuntimeError):
    """A request that could change the portal was issued through the read-only client."""


class ReadError(RuntimeError):
    """A read failed after its retries; the message carries no token."""


def _is_read(method: str, path: str) -> bool:
    if method == "GET":
        return True
    return method == "POST" and path.split("?")[0].endswith(("/search", "/batch/read"))


def same_value(a: Any, b: Any) -> bool:
    """HubSpot returns an unset property as null or ""; amounts may come back as "24000" or "24000.0"."""
    if not a and not b:
        return True
    if a is None or b is None:
        return False
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def diff_props(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key in sorted(set(before) | set(after)):
        if not same_value(before.get(key), after.get(key)):
            out[key] = {"from": before.get(key) or None, "to": after.get(key) or None}
    return out


def _epoch_ms(moment: datetime) -> str:
    return str(int(moment.timestamp() * 1000))


def _parse_since(since: str) -> datetime:
    parsed = datetime.fromisoformat(since.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _full_name(props: dict[str, Any]) -> str:
    return " ".join(p for p in (props.get("firstname"), props.get("lastname")) if p)


def _engagement_row(kind: str, rec: dict[str, Any], assoc: dict[str, list[str]]) -> dict[str, Any]:
    p = rec["properties"]
    row: dict[str, Any] = {"id": rec["id"]}
    if kind == "tasks":
        row.update(title=p.get("hs_task_subject"), body=p.get("hs_task_body"), status=p.get("hs_task_status"),
                   priority=p.get("hs_task_priority"), due=p.get("hs_timestamp"), owner_id=p.get("hubspot_owner_id"))
    elif kind == "notes":
        row.update(body=p.get("hs_note_body"), timestamp=p.get("hs_timestamp"))
    elif kind == "meetings":
        row.update(title=p.get("hs_meeting_title"), body=p.get("hs_meeting_body"), outcome=p.get("hs_meeting_outcome"),
                   start=p.get("hs_meeting_start_time"), end=p.get("hs_meeting_end_time"), timestamp=p.get("hs_timestamp"))
    elif kind == "calls":
        row.update(title=p.get("hs_call_title"), body=p.get("hs_call_body"), status=p.get("hs_call_status"),
                   direction=p.get("hs_call_direction"), timestamp=p.get("hs_timestamp"))
    else:
        row.update(subject=p.get("hs_email_subject"), body=p.get("hs_email_text"), direction=p.get("hs_email_direction"),
                   timestamp=p.get("hs_timestamp"))
    row["associations"] = assoc
    return row


class PortalReader:
    def __init__(self, token: str, *, base: str = API, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep, clock=time.monotonic, settle_seconds: float = 8.0) -> None:
        self._token = token
        self._http = httpx.Client(base_url=base, transport=transport, timeout=60.0,
                                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        self._sleep = sleep
        self._clock = clock
        self._last_search = float("-inf")
        self.settle_seconds = settle_seconds
        self.calls = 0
        self.baseline_data: dict[str, Any] | None = None
        self._labels: dict[str, str] | None = None

    # -- transport ---------------------------------------------------------------------------------------

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "<redacted>") if self._token else text

    def _request(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        if not _is_read(method, path):
            raise WriteAttempt(f"{method} {path} is not a read; PortalReader never writes")
        for attempt in range(RETRIES + 1):
            if path.endswith("/search"):
                wait = SEARCH_GAP - (self._clock() - self._last_search)
                if wait > 0:
                    self._sleep(wait)
                self._last_search = self._clock()
            self.calls += 1
            try:
                response = self._http.request(method, path, json=body)
            except httpx.HTTPError as exc:
                if attempt == RETRIES:
                    raise ReadError(f"{method} {path}: {self._scrub(str(exc))[:200]}") from None
                self._sleep(2.0 ** attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == RETRIES:
                    raise ReadError(f"{method} {path}: HTTP {response.status_code}")
                try:
                    delay = float(response.headers.get("Retry-After", ""))
                except ValueError:
                    delay = 2.0 ** attempt
                self._sleep(min(30.0, delay))
                continue
            if response.status_code >= 400:
                raise ReadError(f"{method} {path}: HTTP {response.status_code}: {self._scrub(response.text)[:200]}")
            return response.json() if response.content else {}
        raise ReadError(f"{method} {path}: retries exhausted")  # pragma: no cover

    # -- primitives --------------------------------------------------------------------------------------

    def search(self, obj: str, props: list[str], filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            body: dict[str, Any] = {"filterGroups": [{"filters": filters}], "properties": props, "limit": 100}
            if after:
                body["after"] = after
            page = self._request("POST", f"/crm/v3/objects/{obj}/search", body)
            out += page.get("results", [])
            after = (page.get("paging") or {}).get("next", {}).get("after")
            if not after:
                return out

    def search_tagged(self, obj: str, props: list[str]) -> list[dict[str, Any]]:
        return self.search(obj, props, [{"propertyName": TAG_PROPERTY, "operator": "EQ", "value": TAG}])

    def search_created_since(self, obj: str, props: list[str], since: datetime) -> list[dict[str, Any]]:
        field = "createdate" if obj in TABLE_PROPS else "hs_createdate"
        return self.search(obj, props, [{"propertyName": field, "operator": "GTE", "value": _epoch_ms(since - CLOCK_SKEW)}])

    def batch_read(self, obj: str, ids: list[str], props: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        ordered = sorted({str(i) for i in ids})
        for start in range(0, len(ordered), 100):
            chunk = ordered[start:start + 100]
            result = self._request("POST", f"/crm/v3/objects/{obj}/batch/read",
                                   {"properties": props, "inputs": [{"id": i} for i in chunk]})
            for rec in result.get("results", []):
                out[str(rec["id"])] = rec
        return out

    def associations(self, src: str, dst: str, ids: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {str(i): [] for i in ids}
        ordered = sorted(out)
        for start in range(0, len(ordered), 1000):
            chunk = ordered[start:start + 1000]
            result = self._request("POST", ASSOC_PATH.format(src=OBJ_TYPE[src], dst=OBJ_TYPE[dst]),
                                   {"inputs": [{"id": i} for i in chunk]})
            for row in result.get("results", []):
                out[str(row["from"]["id"])] = sorted(str(t["toObjectId"]) for t in row.get("to", []))
        return out

    def _label_map(self) -> dict[str, str]:
        if self._labels is None:
            labels: dict[str, str] = {}
            for pipe in self._request("GET", "/crm/v3/pipelines/deals").get("results", []):
                labels[pipe["id"]] = pipe["label"]
                for stage in pipe.get("stages", []):
                    labels[stage["id"]] = stage["label"]
            self._labels = labels
        return self._labels

    def _props(self, table: str, raw: dict[str, Any]) -> dict[str, Any]:
        out = {k: raw.get(k) for k in TABLE_PROPS[table]}
        if table == "deals":
            labels = self._label_map()
            for key in ("pipeline", "dealstage"):
                out[key] = labels.get(out[key], out[key])
        return out

    # -- baseline ----------------------------------------------------------------------------------------

    def baseline(self) -> dict[str, Any]:
        """Every seeded deal, contact and company, keyed by dealname, email and company name."""
        key_prop = {"deals": "dealname", "contacts": "email", "companies": "name"}
        data: dict[str, dict[str, Any]] = {"deals": {}, "contacts": {}, "companies": {}}
        for table, key_name in key_prop.items():
            for rec in self.search_tagged(table, TABLE_PROPS[table]):
                props = self._props(table, rec["properties"])
                key = str(props[key_name] or rec["id"])
                if key in data[table]:
                    key = f"{key} #{rec['id']}"
                data[table][key] = {"id": rec["id"], "props": props}
        self.baseline_data = data
        return data

    def load_baseline(self, data: dict[str, Any]) -> None:
        self.baseline_data = data

    def _base(self) -> dict[str, Any]:
        if self.baseline_data is None:
            raise ReadError("baseline() has not run")
        return self.baseline_data

    # -- comparison by baseline id (index independent) ---------------------------------------------------

    def _changes(self, table: str) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
        """(changes by name, current props by name, names missing)."""
        base = self._base()[table]
        found = self.batch_read(table, [v["id"] for v in base.values()], TABLE_PROPS[table])
        changed: dict[str, dict[str, Any]] = {}
        now: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        for name, entry in base.items():
            rec = found.get(str(entry["id"]))
            if rec is None:
                missing.append(name)
                continue
            now[name] = self._props(table, rec["properties"])
            if diff := diff_props(entry["props"], now[name]):
                changed[name] = diff
        return changed, now, missing

    def verify_baseline(self) -> dict[str, Any]:
        """Do the seeded records still hold their baseline values? No search, so no index lag.

        Only the properties restore.py rewrites count (RESTORED_PROPS). Differences in the other tracked properties
        are listed under ``unrestorable`` for information and never fail the check."""
        differs: list[str] = []
        unrestorable: list[str] = []
        for table in TABLE_PROPS:
            changed, _, missing = self._changes(table)
            differs += [f"{table}:{n}" for n in missing]
            for name, diff in changed.items():
                if set(diff) & RESTORED_PROPS[table]:
                    differs.append(f"{table}:{name}")
                else:
                    unrestorable.append(f"{table}:{name}")
        return {"ok": not differs, "differs": differs, "unrestorable": unrestorable}

    # -- search index (what the arms' tools see) ----------------------------------------------------------

    def search_index_differs(self, absent_contacts: tuple[tuple[str, str], ...] = (),
                             as_of: str | None = None) -> list[str]:
        """Records the Search API still shows differently from the baseline. verify_baseline reads by id and so
        cannot see index lag, but every arm finds records through search: right after restore.py rewrites the 31
        past-due close dates, search still returns the benchmark's values for a minute or more (smoke run
        2026-10-30: the intent tool found 22, then 9, then 0 of 31). ``absent_contacts`` are (first, last) names a
        restore archives, such as the contact job 3 creates; search must no longer return them.

        Search returns current property values but matches filters against its index, and the two lag apart: in the
        second smoke run the values matched 0.7 s after a restore while a ``closedate < as_of`` filter still found
        none of the 31 past-due deals. With ``as_of`` the check also runs that filter and needs exactly the seeded
        deals whose baseline close date is before it."""
        base = self._base()
        differs: list[str] = []
        if as_of:
            cutoff = datetime.fromisoformat(as_of).replace(tzinfo=UTC)
            want = {name for name, v in base["deals"].items()
                    if v["props"].get("closedate") and str(v["props"]["closedate"])[:10] < as_of}
            hits = self.search("deals", ["dealname"], [
                {"propertyName": TAG_PROPERTY, "operator": "EQ", "value": TAG},
                {"propertyName": "closedate", "operator": "LT", "value": _epoch_ms(cutoff)}])
            names = {self._names()["deals"].get(str(h["id"]), str(h["id"])) for h in hits}
            if names != want:
                differs.append(f"deals:closedate<{as_of} filter finds {len(names)} of {len(want)} "
                               f"({len(want - names)} missing, {len(names - want)} extra)")
        for table in ("deals", "contacts"):
            by_id = {str(v["id"]): (name, v["props"]) for name, v in base[table].items()}
            seen: set[str] = set()
            for rec in self.search_tagged(table, TABLE_PROPS[table]):
                entry = by_id.get(str(rec["id"]))
                if entry is None:
                    continue
                seen.add(str(rec["id"]))
                name, props = entry
                now = self._props(table, rec["properties"])
                if set(diff_props(props, now)) & RESTORED_PROPS[table]:
                    differs.append(f"{table}:{name}")
            differs += [f"{table}:{name} (not in search)" for i, (name, _) in by_id.items() if i not in seen]
        for first, last in absent_contacts:
            hits = self.search("contacts", ["firstname", "lastname"], [
                {"propertyName": "firstname", "operator": "EQ", "value": first},
                {"propertyName": "lastname", "operator": "EQ", "value": last}])
            if hits:
                differs.append(f"contacts:{first} {last} still in search")
        return differs

    def wait_search_index(self, timeout: float, interval: float = 10.0,
                          absent_contacts: tuple[tuple[str, str], ...] = (), as_of: str | None = None) -> dict[str, Any]:
        """Poll search_index_differs until it is empty or ``timeout`` seconds pass."""
        started = self._clock()
        while True:
            differs = self.search_index_differs(absent_contacts, as_of)
            waited = round(self._clock() - started, 1)
            if not differs or waited >= timeout:
                return {"ok": not differs, "waited_s": waited, "differs": differs[:20]}
            self._sleep(interval)

    # -- end state ---------------------------------------------------------------------------------------

    def _names(self) -> dict[str, dict[str, str]]:
        base = self._base()
        return {
            "deals": {str(v["id"]): k for k, v in base["deals"].items()},
            "companies": {str(v["id"]): v["props"].get("name") or k for k, v in base["companies"].items()},
            "contacts": {str(v["id"]): _full_name(v["props"]) or k for k, v in base["contacts"].items()},
        }

    def _created(self, since: datetime, names: dict[str, dict[str, str]],
                 new_contacts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        def translate(links: dict[str, list[str]], dst: str, rec_id: str) -> list[str]:
            return sorted(names[dst].get(t, f"unknown:{t}") for t in links.get(str(rec_id), []))

        created: dict[str, list[dict[str, Any]]] = {}
        for kind in ENGAGEMENT_KINDS:
            recs = self.search_created_since(kind, ENGAGEMENT_PROPS[kind], since)
            ids = [r["id"] for r in recs]
            links = {dst: self.associations(kind, dst, ids) for dst in ASSOC_TARGETS} if ids else {}
            rows = [_engagement_row(kind, rec, {dst: translate(links[dst], dst, rec["id"]) for dst in ASSOC_TARGETS})
                    for rec in recs]
            created[kind] = sorted(rows, key=lambda r: r["id"])
        ids = [r["id"] for r in new_contacts]
        to_company = self.associations("contacts", "companies", ids) if ids else {}
        to_deal = self.associations("contacts", "deals", ids) if ids else {}
        created["contacts"] = sorted(
            ({"id": rec["id"], **{k: rec["properties"].get(k) for k in CONTACT_PROPS if k != "hubspot_owner_id"},
              "associations": {"companies": translate(to_company, "companies", rec["id"]),
                               "deals": translate(to_deal, "deals", rec["id"])}} for rec in new_contacts),
            key=lambda r: r["id"])
        return created

    def _round(self, since: datetime, targets: list[str]) -> dict[str, Any]:
        base = self._base()
        names = self._names()
        deals_changed, deals_now, deals_missing = self._changes("deals")
        contacts_changed, _, _ = self._changes("contacts")
        companies_changed, _, _ = self._changes("companies")

        new_deals = [r for r in self.search_created_since("deals", DEAL_PROPS, since) if str(r["id"]) not in names["deals"]]
        new_contacts = [r for r in self.search_created_since("contacts", CONTACT_PROPS, since)
                        if str(r["id"]) not in names["contacts"]]
        for rec in new_deals:
            names["deals"][str(rec["id"])] = rec["properties"].get("dealname") or str(rec["id"])
        for rec in new_contacts:
            names["contacts"][str(rec["id"])] = _full_name(rec["properties"]) or str(rec["id"])
        created = self._created(since, names, new_contacts)

        contact_info = {}
        for email, diff in contacts_changed.items():
            props = base["contacts"][email]["props"]
            contact_info[email] = {"name": _full_name(props), "company": props.get("company"), "changes": diff}
        counts = {kind: len(created[kind]) for kind in (*ENGAGEMENT_KINDS, "contacts")}
        counts["deals_created"] = len(new_deals)
        return {
            "target_names": list(targets),
            "deals_target": {n: deals_now[n] for n in targets if n in deals_now},
            "deals_changed": {n: {"changes": d} for n, d in deals_changed.items()},
            "deals_missing": deals_missing,
            "deals_created": [{"dealname": r["properties"].get("dealname"), "props": self._props("deals", r["properties"])}
                              for r in new_deals],
            "contacts_changed": contact_info,
            "companies_changed": {n: {"changes": d} for n, d in companies_changed.items()},
            "created": created,
            "counts": counts,
        }

    def end_state(self, since: str, targets: list[str]) -> dict[str, Any]:
        """Read until two consecutive rounds give the same counts (search is eventually consistent)."""
        moment = _parse_since(since)
        previous: dict[str, int] | None = None
        state: dict[str, Any] = {}
        rounds = 0
        stable = False
        while rounds < MAX_ROUNDS:
            self._sleep(self.settle_seconds)
            state = self._round(moment, targets)
            rounds += 1
            if previous is not None and state["counts"] == previous:
                stable = True
                break
            previous = state["counts"]
        return {"read_at": datetime.now(UTC).isoformat(), "tz": TZ, "rounds": rounds, "stable": stable, **state}

    def read_check(self, since: str) -> dict[str, Any]:
        """After a read task: did anything change? Property sets only; engagement associations are not read."""
        moment = _parse_since(since)
        base = self._base()
        self._sleep(self.settle_seconds)
        counts: dict[str, int] = {}
        changed_names: list[str] = []
        for table in TABLE_PROPS:
            changed, _, missing = self._changes(table)
            counts[table] = len(changed) + len(missing)
            changed_names += [f"{table}:{n}" for n in [*changed, *missing]]
        known = {t: {str(v["id"]) for v in base[t].values()} for t in ("deals", "contacts")}
        created = {kind: len(self.search_created_since(kind, ["hs_createdate"], moment)) for kind in ENGAGEMENT_KINDS}
        new_contacts = [r for r in self.search_created_since("contacts", ["email"], moment) if str(r["id"]) not in known["contacts"]]
        new_deals = [r for r in self.search_created_since("deals", ["dealname"], moment) if str(r["id"]) not in known["deals"]]
        created["contacts"] = len(new_contacts)
        changed_names += [f"deals_created:{r['properties'].get('dealname') or r['id']}" for r in new_deals]
        ok = not any(counts.values()) and not any(created.values()) and not new_deals
        return {"ok": ok, "deals_changed": counts["deals"], "contacts_changed": counts["contacts"],
                "companies_changed": counts["companies"], "created": created, "changed_names": changed_names}
