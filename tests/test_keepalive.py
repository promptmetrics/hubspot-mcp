"""The weekly keep-alive that stops a free-tier Redis being deleted for inactivity."""
from __future__ import annotations

import json

import pytest
from starlette.requests import Request


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def set(self, k, v):
        self.data[k] = v

    def get(self, k):
        return self.data.get(k)


class _RedisBacked:
    def __init__(self) -> None:
        self._redis = _FakeRedis()


class _FileBacked:
    pass


def _request(auth: str | None) -> Request:
    headers = [(b"authorization", auth.encode())] if auth else []
    return Request({"type": "http", "method": "GET", "path": "/cron/keepalive", "headers": headers, "query_string": b""})


@pytest.fixture
def stores(monkeypatch):
    from hubspot_mcp import state
    from hubspot_mcp.state import connection_store

    state_store, conn_store = _RedisBacked(), _RedisBacked()
    monkeypatch.setattr(state, "get_store", lambda: state_store)
    monkeypatch.setattr(connection_store, "get_connection_store", lambda: conn_store)
    return state_store, conn_store


async def test_refuses_without_a_configured_secret(monkeypatch, stores):
    from hubspot_mcp.server import cron_keepalive

    monkeypatch.delenv("CRON_SECRET", raising=False)
    resp = await cron_keepalive(_request("Bearer anything"))
    assert resp.status_code == 503


async def test_rejects_a_wrong_bearer(monkeypatch, stores):
    from hubspot_mcp.server import cron_keepalive

    monkeypatch.setenv("CRON_SECRET", "s3cret")
    assert (await cron_keepalive(_request("Bearer nope"))).status_code == 401
    assert (await cron_keepalive(_request(None))).status_code == 401


async def test_touches_both_redis_stores(monkeypatch, stores):
    from hubspot_mcp.server import KEEPALIVE_KEY, cron_keepalive

    monkeypatch.setenv("CRON_SECRET", "s3cret")
    resp = await cron_keepalive(_request("Bearer s3cret"))
    body = json.loads(resp.body)
    assert resp.status_code == 200 and body["ok"] is True
    assert body["touched"] == ["state", "connections"]
    state_store, conn_store = stores
    assert KEEPALIVE_KEY in state_store._redis.data and KEEPALIVE_KEY in conn_store._redis.data


async def test_file_backed_stores_are_skipped(monkeypatch):
    from hubspot_mcp import state
    from hubspot_mcp.server import cron_keepalive
    from hubspot_mcp.state import connection_store

    monkeypatch.setattr(state, "get_store", lambda: _FileBacked())
    monkeypatch.setattr(connection_store, "get_connection_store", lambda: _FileBacked())
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    body = json.loads((await cron_keepalive(_request("Bearer s3cret"))).body)
    assert body == {"ok": True, "touched": [], "at": body["at"]}


def test_vercel_cron_targets_the_route_weekly():
    import pathlib

    cfg = json.loads((pathlib.Path(__file__).resolve().parent.parent / "vercel.json").read_text())
    assert cfg["crons"] == [{"path": "/cron/keepalive", "schedule": "0 6 * * 1"}]
