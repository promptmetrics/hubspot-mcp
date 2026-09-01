"""Single-tenant enforcement for network-reachable deployments (Phase 2).

Phase 2 serves exactly one HubSpot portal per deployment: one set of
credentials in the environment, one shared bearer secret, no per-user identity.
Three things now depend on that, and one of them breaks loudly if it stops
being true:

1. ``app_lifespan`` builds **one** ``HubSpotClient`` and probes **one**
   capability matrix.
2. ``client._LAST_RATE_STATE`` is a module-level per-portal rate snapshot.
3. ``_unadvertise_unavailable_tools`` calls ``mcp.remove_tool(...)`` on the
   **module-level server object**. If one deployment served two portals, a
   portal without Workflows would unadvertise the workflow tools for every other
   portal on that instance, permanently, until it recycled.

(3) is a correctness bug, not an inefficiency, so the boundary is enforced here
rather than left as an assumption. Multi-tenancy is Phase 3, where per-user
OAuth makes the portal a per-request property and all three become
request-scoped.

The policy matches :mod:`hubspot_mcp.auth.bearer_middleware`: permissive on a
loopback bind, strict on anything the internet can reach.
"""
from __future__ import annotations

import os
import re
import sys

PORTAL_ENV = "HUBSPOT_PORTAL"
_TOKEN_ENV_RE = re.compile(r"^HUBSPOT_TOKEN_(\d+)$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", ""})


class MultiTenantConfiguration(RuntimeError):
    """Raised when a network-reachable deployment is not unambiguously single-portal."""


def configured_token_portals(env: dict[str, str] | None = None) -> set[str]:
    """Portal ids that have a ``HUBSPOT_TOKEN_<id>`` variable set."""
    source = env if env is not None else os.environ
    return {m.group(1) for name in source if (m := _TOKEN_ENV_RE.match(name))}


def is_loopback(host: str) -> bool:
    return host.lower() in _LOOPBACK_HOSTS


def enforce_single_tenant(
    host: str,
    portal_id: str | None,
    source: str,
    *,
    env: dict[str, str] | None = None,
) -> str | None:
    """Verify the deployment resolves exactly one portal, or refuse to start.

    ``source`` is where ``portal_id`` came from: ``"flag"``, ``"env"``,
    ``"file"`` (a ``.hubspot-portal`` in the working directory) or ``"none"``.

    On a loopback bind this only warns — a developer running the HTTP transport
    against their own machine is not serving anyone. On any other bind each
    condition below is fatal, because the failure it prevents is silent.
    """
    problems: list[str] = []

    if portal_id is None:
        problems.append(
            f"No HubSpot portal is configured. Set {PORTAL_ENV}. Over stdio an unresolved "
            "portal is recoverable — the user runs auth and retries — but a hosted "
            "deployment has no one to prompt."
        )
    elif source == "file":
        problems.append(
            f"The portal was read from a .hubspot-portal file in the working directory. "
            f"On a hosted deployment the working directory is a build artifact, so a "
            f"stray committed file would decide which HubSpot portal this writes to. "
            f"Set {PORTAL_ENV}={portal_id} explicitly instead."
        )

    token_portals = configured_token_portals(env)
    if len(token_portals) > 1:
        problems.append(
            f"Credentials for {len(token_portals)} portals are present "
            f"(HUBSPOT_TOKEN_* for {', '.join(sorted(token_portals))}), but this server "
            "serves one portal per deployment. Capability gating mutates the shared "
            "server object, so a second portal would change which tools the first one "
            "advertises. Deploy one instance per portal until Phase 3."
        )
    elif portal_id is not None and token_portals and portal_id not in token_portals:
        problems.append(
            f"The configured portal is {portal_id}, but the only credentials present are "
            f"for {next(iter(token_portals))}. One of the two is wrong."
        )

    if not problems:
        return portal_id

    detail = "\n".join(f"  - {p}" for p in problems)
    if is_loopback(host):
        print(
            f"hubspot_mcp: serving {host} with an ambiguous portal configuration:\n{detail}\n"
            "  Loopback only; a non-local bind will refuse to start.",
            file=sys.stderr,
        )
        return portal_id
    raise MultiTenantConfiguration(f"Refusing to serve on {host}:\n{detail}")
