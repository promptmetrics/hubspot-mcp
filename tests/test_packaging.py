"""Plugin packaging invariants.

These are the things that break a user's install while every functional test
stays green: a version that disagrees across the three manifests, a manifest in
the wrong place for the loader, a launcher that references a missing file, or a
tracked file that would ship someone else's repo inside this artifact.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _plugin() -> dict:
    return json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())


def _marketplace() -> dict:
    return json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())


def test_manifests_live_where_the_loader_looks():
    assert (REPO / ".claude-plugin" / "plugin.json").is_file()
    assert (REPO / ".claude-plugin" / "marketplace.json").is_file()
    assert not (REPO / "plugin.json").exists(), "stale root copy would shadow the real manifest"


def test_the_three_version_fields_agree():
    """pyproject, plugin.json and marketplace.json are edited separately and
    drift silently; a plugin advertising one version and installing another is
    the kind of thing only a user hits."""
    pyproject = re.search(r'^version = "([^"]+)"', (REPO / "pyproject.toml").read_text(), re.M)
    assert pyproject, "pyproject has no version"
    assert pyproject.group(1) == _plugin()["version"] == _marketplace()["plugins"][0]["version"]


def test_plugin_manifest_keeps_its_wiring():
    plugin = _plugin()
    assert set(plugin["userConfig"]) == {
        "hubspot_client_id",
        "hubspot_client_secret",
        "hubspot_portal_id",
        "hubspot_region",
    }
    assert plugin["mcpServers"]["hubspot"]["args"] == ["${CLAUDE_PLUGIN_ROOT}/bin/run-mcp.sh"]
    assert plugin["hooks"] == "./hooks/hooks.json"


def test_referenced_scripts_exist_and_are_executable():
    for rel in ("bin/run-mcp.sh", "bin/session-start.sh", "scripts/check-artifact-allowlist.sh"):
        path = REPO / rel
        assert path.is_file(), f"{rel} is referenced but missing"
        assert path.stat().st_mode & 0o111, f"{rel} is not executable"


def test_launcher_is_posix_sh_clean():
    assert subprocess.run(["sh", "-n", str(REPO / "bin" / "run-mcp.sh")]).returncode == 0


def test_license_file_exists():
    """MIT was declared in metadata with no LICENSE file in the repo."""
    assert (REPO / "LICENSE").is_file()
    assert "MIT License" in (REPO / "LICENSE").read_text()


def test_no_tracked_file_escapes_the_shipping_allowlist():
    """reference/hubspot-claude/ is a full clone of the source plugin sitting in
    the working tree; tracking it would ship another repo inside this one."""
    result = subprocess.run(
        ["bash", str(REPO / "scripts" / "check-artifact-allowlist.sh")],
        cwd=REPO, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
