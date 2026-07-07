"""CLI entrypoint: ``hubspot-mcp`` server + ``hubspot-mcp auth`` subcommand.

Default invocation runs the MCP server (stdio by default). ``auth login`` runs
the interactive OAuth flow (or verifies a PAT); ``auth status`` reports the
current portal's auth state. Server transport/mode/portal come from CLI flags
or env (``HUBSPOT_PORTAL``, ``HUBSPOT_MODE``).
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from hubspot_mcp.config import load_portal_config


def _cmd_server(args: argparse.Namespace) -> int:
    from hubspot_mcp import server

    server.configure_server(portal_id=args.portal, mode=args.mode)
    server.run(transport=args.transport, host=args.host, port=args.port)
    return 0


def _cmd_auth_login(args: argparse.Namespace) -> int:
    portal_id = args.portal
    if not portal_id:
        print("error: --portal is required for auth login", file=sys.stderr)
        return 2

    if args.mode == "token":
        cfg = load_portal_config(portal_id)
        if cfg and cfg.token:
            print(f"Portal {portal_id}: private-app token present (auth_type={cfg.auth_type}).")
            if cfg.scopes_granted:
                print(f"scopes: {', '.join(cfg.scopes_granted)}")
            return 0
        print(
            f"No token found for portal {portal_id}. Set HUBSPOT_TOKEN_{portal_id} or "
            f"create ~/.claude/hubspot/{portal_id}.token with your private-app token.",
            file=sys.stderr,
        )
        return 1

    # oauth (default)
    from hubspot_mcp.auth.oauth_provider import run_oauth_login

    try:
        body = asyncio.run(
            run_oauth_login(portal_id, scopes=args.scopes, port=args.callback_port, open_browser=not args.no_browser)
        )
    except Exception as exc:  # noqa: BLE001 — top-level CLI surfaces the message
        print(f"OAuth login failed: {exc}", file=sys.stderr)
        return 1
    print(f"OAuth login succeeded for portal {portal_id}.")
    granted = (body.get("scope") or "").split()
    if granted:
        print(f"scopes granted: {', '.join(granted)}")
    return 0


def _cmd_auth_status(args: argparse.Namespace) -> int:
    portal_id = args.portal
    if not portal_id:
        print("error: --portal is required for auth status", file=sys.stderr)
        return 2
    cfg = load_portal_config(portal_id)
    if cfg is None:
        print(f"Portal {portal_id}: not configured.")
        return 1
    print(f"Portal {portal_id}: auth_type={cfg.auth_type}, tier={cfg.tier}")
    if cfg.scopes_granted:
        print(f"scopes: {', '.join(cfg.scopes_granted)}")
    if cfg.expires_at:
        print(f"expires_at: {cfg.expires_at}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hubspot-mcp", description="HubSpot MCP server")
    p.add_argument("--portal", help="HubSpot portal ID (or set HUBSPOT_PORTAL)")
    p.add_argument("--mode", choices=("oauth", "token"), default="oauth", help="Auth mode (default: oauth)")

    sub = p.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run the MCP server (default)")
    run_p.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    run_p.add_argument("--host", default=None)
    run_p.add_argument("--port", type=int, default=None)
    run_p.set_defaults(func=_cmd_server)

    auth = sub.add_parser("auth", help="Authentication subcommands")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)

    login = auth_sub.add_parser("login", help="Authenticate a portal")
    login.add_argument("--portal", help="HubSpot portal ID (or set HUBSPOT_PORTAL)")
    login.add_argument("--mode", choices=("oauth", "token"), default="oauth", help="Auth mode (default: oauth)")
    login.add_argument("--scopes", nargs="*", default=None, help="Exact OAuth scope set (or set HUBSPOT_SCOPES)")
    login.add_argument("--callback-port", type=int, default=3000)
    login.add_argument("--no-browser", action="store_true", help="Print the authorize URL instead of opening a browser")
    login.set_defaults(func=_cmd_auth_login)

    status = auth_sub.add_parser("status", help="Show a portal's auth state")
    status.add_argument("--portal", help="HubSpot portal ID (or set HUBSPOT_PORTAL)")
    status.set_defaults(func=_cmd_auth_status)
    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if getattr(args, "auth_command", None):
        return args.func(args)
    if args.command == "run":
        return args.func(args)
    # No subcommand → default to running the server.
    args.transport = "stdio"
    args.host = None
    args.port = None
    args.func = _cmd_server
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())