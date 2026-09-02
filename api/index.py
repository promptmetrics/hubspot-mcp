"""Vercel entrypoint — the hosted MCP server as an ASGI app.

Lives in ``api/`` because that is the only layout Vercel's Python runtime picks
up without a detected backend framework. With Framework Preset "Other" the
project is treated as Node — install command ``npm install``, build command
``npm run build`` — so a root-level entrypoint plus ``[tool.vercel] entrypoint``
is never consulted, ``pip`` never runs, and the build "succeeds" in ~100ms
having produced nothing. A ``functions`` key must target this directory too.

Vercel imports this module and serves ``app``; it never calls
``hubspot_mcp.server.run``. That is why the bearer wrapper, the tenancy guard
and the OAuth resource-server wiring all live in ``build_http_app`` rather than
in ``run`` — a check that only fires under uvicorn fires nowhere in production.

``0.0.0.0`` is passed deliberately. It is not the bind address here (Vercel owns
that), it is the signal that this deployment is internet-reachable, which puts
every guard into its strict mode rather than the permissive one loopback gets.
"""

import os

# Redirect the home directory to the one writable path on a serverless host,
# BEFORE importing anything from `hubspot_mcp`.
#
# Nine modules resolve `Path.home()` for the schema cache, checkpoints, progress
# and blueprint files, and `config.CONFIG_DIR` binds it at import. Setting $HOME
# here covers all of them at once — `Path.home()` reads it — where a per-module
# override would not, and would be nine chances to miss one.
#
# It has to happen in the process rather than in `vercel.json`, because Vercel
# rejects `HOME` as a reserved key in its env configuration and refuses the
# whole deployment.
if os.environ.get("VERCEL"):
    os.environ["HOME"] = "/tmp"  # noqa: S108 — the one writable path on the host

from hubspot_mcp.server import build_http_app  # noqa: E402 — must follow the $HOME redirect

app = build_http_app("0.0.0.0")  # noqa: S104 — see the module docstring
