"""The deploy ignore list must never strip part of the package.

`.vercelignore` uses gitignore syntax, where an unanchored `name/` matches a
directory of that name at any depth. `skills/`, meant for the plugin folder at
the root, also matched `src/hubspot_mcp/skills/` and the hosted server failed
to import. Every pattern here must be anchored, and none may match a directory
that exists under src/.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _patterns() -> list[str]:
    return [
        line.strip()
        for line in (ROOT / ".vercelignore").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_every_vercelignore_pattern_is_anchored_to_the_root():
    for pat in _patterns():
        assert pat.startswith("/"), f"{pat!r} is unanchored and would match that name anywhere under src/"


def test_no_vercelignore_pattern_names_a_directory_inside_the_package():
    package_dirs = {p.name for p in (ROOT / "src" / "hubspot_mcp").rglob("*") if p.is_dir()}
    for pat in _patterns():
        name = pat.strip("/").split("/")[0]
        assert name not in package_dirs or pat.startswith("/"), pat
