"""An in-memory HubSpot for the intent tests: stateful records and associations, no network.

Stands in for ``HubSpotClient`` (``get/post/put/patch/delete`` returning
``APIResponse``). It implements only the endpoints the intent foundation uses:
object batch read/update, single create, archive, and v4 association batch
read/associate/create/archive. Search, pipelines and owners are scripted per test.

``script(method, path_part, *responses)`` queues responses for the next matching
calls (an exception is raised, a dict is returned as the body, a callable is
called with the request body and its return value is the body); once the queue is
empty the stateful handler answers. Every call is recorded in ``calls``.
"""
from __future__ import annotations

import re
from typing import Any

from hubspot_mcp.client import APIResponse

PORTAL = "99999999"

_OBJECT = re.compile(r"^/crm/v3/objects/(?P<type>[a-z_]+)(?:/(?P<id>[0-9]+))?$")
_OBJECT_BATCH = re.compile(r"^/crm/v3/objects/(?P<type>[a-z_]+)/batch/(?P<action>read|update|archive)$")
_ASSOC_BATCH = re.compile(
    r"^/crm/v4/associations/(?P<src>[a-z_]+)/(?P<dst>[a-z_]+)/batch/(?P<action>read|associate/default|create|archive)$"
)


class FakeHubSpot:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.associations: set[tuple[str, str, str, str]] = set()
        self.calls: list[tuple[str, str, Any]] = []
        self._scripts: list[tuple[str, str, list[Any]]] = []
        self._next_id = 5000

    # --- setup -------------------------------------------------------------

    def add(self, object_type: str, record_id: str, **props: Any) -> None:
        self.objects[(object_type, str(record_id))] = dict(props)

    def associate(self, src: tuple[str, str], dst: tuple[str, str]) -> None:
        self.associations.add((src[0], src[1], dst[0], dst[1]))

    def script(self, method: str, path_part: str, *responses: Any) -> None:
        self._scripts.append((method.upper(), path_part, list(responses)))

    # --- inspection --------------------------------------------------------

    def writes(self) -> list[tuple[str, str, Any]]:
        """Calls that change HubSpot: everything except reads, which are GETs and the read-style POSTs."""
        reads = ("/search", "/batch/read")
        return [c for c in self.calls if c[0] != "GET" and not c[1].endswith(reads)]

    def paths(self, method: str | None = None) -> list[str]:
        return [p for m, p, _ in self.calls if method is None or m == method]

    # --- client surface ----------------------------------------------------

    async def get(self, path: str, portal_id: str | None = None, expected_scopes: Any = None) -> APIResponse:
        return await self._call("GET", path, None)

    async def post(self, path: str, portal_id: str | None = None, body: Any = None, expected_scopes: Any = None) -> APIResponse:
        return await self._call("POST", path, body)

    async def put(self, path: str, portal_id: str | None = None, body: Any = None, expected_scopes: Any = None) -> APIResponse:
        return await self._call("PUT", path, body)

    async def patch(self, path: str, portal_id: str | None = None, body: Any = None, expected_scopes: Any = None) -> APIResponse:
        return await self._call("PATCH", path, body)

    async def delete(self, path: str, portal_id: str | None = None, expected_scopes: Any = None) -> APIResponse:
        return await self._call("DELETE", path, None)

    async def close(self) -> None:
        pass

    # --- dispatch ----------------------------------------------------------

    async def _call(self, method: str, path: str, body: Any) -> APIResponse:
        self.calls.append((method, path, body))
        for m, part, queue in self._scripts:
            if m == method and part in path and queue:
                nxt = queue.pop(0)
                if isinstance(nxt, Exception):
                    raise nxt
                if callable(nxt):
                    nxt = nxt(body)
                return APIResponse(status_code=200, body=nxt, headers={})
        return APIResponse(status_code=200, body=self._handle(method, path, body), headers={})

    def _handle(self, method: str, path: str, body: Any) -> dict[str, Any]:
        if (m := _OBJECT_BATCH.match(path)) and method == "POST":
            return getattr(self, f"_batch_{m['action']}")(m["type"], body)
        if (m := _ASSOC_BATCH.match(path)) and method == "POST":
            return self._assoc(m["src"], m["dst"], m["action"], body)
        if m := _OBJECT.match(path):
            if method == "POST" and not m["id"]:
                self._next_id += 1
                self.objects[(m["type"], str(self._next_id))] = dict(body["properties"])
                return {"id": str(self._next_id), "properties": dict(body["properties"])}
            if method == "DELETE" and m["id"]:
                self.objects.pop((m["type"], m["id"]), None)
                self.associations = {a for a in self.associations if (m["type"], m["id"]) not in ((a[0], a[1]), (a[2], a[3]))}
                return {}
        raise AssertionError(f"FakeHubSpot has no handler for {method} {path}")

    def _batch_read(self, object_type: str, body: dict[str, Any]) -> dict[str, Any]:
        wanted = body.get("properties") or []
        results = []
        for item in body["inputs"]:
            props = self.objects.get((object_type, str(item["id"])))
            if props is not None:
                results.append({"id": str(item["id"]), "properties": {p: props.get(p) for p in wanted}})
        return {"status": "COMPLETE", "results": results}

    def _batch_update(self, object_type: str, body: dict[str, Any]) -> dict[str, Any]:
        results, errors = [], []
        for item in body["inputs"]:
            key = (object_type, str(item["id"]))
            if key in self.objects:
                self.objects[key].update(item["properties"])
                results.append({"id": key[1], "properties": dict(self.objects[key])})
            else:
                errors.append({"message": "Resource not found", "context": {"ids": [key[1]]}})
        return {"status": "COMPLETE_WITH_ERRORS" if errors else "COMPLETE", "results": results, "errors": errors}

    def _batch_archive(self, object_type: str, body: dict[str, Any]) -> dict[str, Any]:
        for item in body["inputs"]:
            key = (object_type, str(item["id"]))
            self.objects.pop(key, None)
            # Archiving removes a record's associations in HubSpot, as the single DELETE above does.
            self.associations = {a for a in self.associations if key not in ((a[0], a[1]), (a[2], a[3]))}
        return {}

    def _assoc(self, src: str, dst: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        if action == "read":
            rows = []
            for item in body["inputs"]:
                fid = str(item["id"])
                targets = [{"toObjectId": int(a[3]), "associationTypes": []} for a in sorted(self.associations) if a[:2] == (src, fid) and a[2] == dst]
                rows.append({"from": {"id": fid}, "to": targets})
            return {"status": "COMPLETE", "results": rows}
        if action == "archive":
            for item in body["inputs"]:
                for target in item["to"]:
                    self.associations.discard((src, str(item["from"]["id"]), dst, str(target["id"])))
            return {}
        for item in body["inputs"]:
            self.associations.add((src, str(item["from"]["id"]), dst, str(item["to"]["id"])))
        return {"status": "COMPLETE", "results": []}
