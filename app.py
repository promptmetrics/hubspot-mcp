"""Vercel entrypoint — the hosted MCP server as an ASGI app.

Vercel imports this module and serves ``app``; it never calls
``hubspot_mcp.server.run``. That is why the bearer wrapper, the tenancy guard
and the OAuth resource-server wiring all live in ``build_http_app`` rather than
in ``run`` — a check that only fires under uvicorn fires nowhere in production.

``0.0.0.0`` is passed deliberately. It is not the bind address here (Vercel owns
that), it is the signal that this deployment is internet-reachable, which puts
every guard into its strict mode rather than the permissive one loopback gets.
"""

from hubspot_mcp.server import build_http_app

app = build_http_app("0.0.0.0")  # noqa: S104 — see the module docstring
