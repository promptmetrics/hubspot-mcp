"""HubSpot MCP server — tools + write-safety state machine on FastMCP.

A standalone repackage of the `hubspot-claude` Claude Code plugin's tool and
safety layer as a Model Context Protocol server. Phase 1: local-stdio,
bring-your-own-app OAuth (default) or private-app token (fallback).
"""
from __future__ import annotations

__version__ = "0.1.0"