"""Minimal Streamable HTTP client for the bench scripts.

Speaks JSON-RPC over HTTP directly with httpx so a bearer header is one line;
handles both JSON and SSE-framed responses. Used for tools/list measurements,
post-session state checks (pending previews, audit) and cleanup (reject, undo).
Not a general MCP client: no notifications, no sessions beyond what the server
hands back in ``Mcp-Session-Id``.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

PROTOCOL = "2026-07-28"


class Surface:
    def __init__(self, url: str, secret: str | None = None, timeout: float = 120.0) -> None:
        self.url = url
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if secret:
            self.headers["Authorization"] = f"Bearer {secret}"
        self.timeout = timeout
        self._session_id: str | None = None
        self._next_id = 1

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        body = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        self._next_id += 1
        headers = dict(self.headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        with httpx.Client(timeout=self.timeout) as http:
            response = http.post(self.url, headers=headers, json=body)
        if sid := response.headers.get("mcp-session-id"):
            self._session_id = sid
        if response.status_code >= 400:
            raise RuntimeError(f"{method}: HTTP {response.status_code}: {response.text[:300]}")
        ctype = response.headers.get("content-type", "")
        if ctype.startswith("text/event-stream"):
            payload = _last_sse_json(response.text)
        else:
            payload = response.json()
        if "error" in payload:
            raise RuntimeError(f"{method}: {payload['error']}")
        return payload.get("result", {})

    def initialize(self) -> dict[str, Any]:
        """Handshake for servers that still want one; harmless otherwise."""
        try:
            result = self._rpc(
                "initialize",
                {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "hubspot-mcp-bench", "version": "0.1"},
                },
            )
        except RuntimeError:
            return {}
        try:
            self._notify("notifications/initialized")
        except RuntimeError:
            pass
        return result

    def _notify(self, method: str) -> None:
        headers = dict(self.headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        with httpx.Client(timeout=self.timeout) as http:
            http.post(self.url, headers=headers, json={"jsonrpc": "2.0", "method": method})

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            result = self._rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> tuple[Any, bool]:
        """Return ``(data, is_error)``; ``data`` is parsed JSON when the text is JSON."""
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        is_error = bool(result.get("isError"))
        if (sc := result.get("structuredContent")) is not None:
            return sc, is_error
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        joined = "\n".join(texts)
        try:
            return json.loads(joined), is_error
        except ValueError:
            return joined, is_error


def _last_sse_json(text: str) -> dict[str, Any]:
    last: dict[str, Any] | None = None
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                last = json.loads(line[5:].strip())
            except ValueError:
                continue
    if last is None:
        raise RuntimeError("SSE response carried no JSON data frame")
    return last


def measure_tools_list(tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Bytes and a rough token estimate (chars/4). The exact figure comes from the
    control-vs-arm first-call input tokens in the session runs."""
    blob = json.dumps(tools, separators=(",", ":"))
    return {"tools": len(tools), "bytes": len(blob), "approx_tokens_chars_div_4": len(blob) // 4}
