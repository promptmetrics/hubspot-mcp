"""The Vercel entrypoint (Phase 3, stage 1).

Vercel imports `app.py` and serves `app`; it never calls `server.run`. So every
guard has to be reachable from `build_http_app` — one that only fires under
uvicorn fires nowhere in production. These tests import the entrypoint the way
the platform does.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

ISSUER = "https://tolerant-climb-38-staging.authkit.app"
PUBLIC_URL = "https://hubspot-mcp-promptmetrics.vercel.app"
# Never connected to — importing the entrypoint only checks the variable exists.
REDIS_URL = "redis://localhost:6379/0"
ROOT = pathlib.Path(__file__).resolve().parent.parent


def _import_entrypoint(**env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "import app; print(type(app.app).__name__)"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "REDIS_URL": REDIS_URL, **env},
    )


def test_the_entrypoint_builds_a_hosted_app():
    result = _import_entrypoint(
        HUBSPOT_MCP_OAUTH_ISSUER=ISSUER,
        HUBSPOT_MCP_PUBLIC_URL=PUBLIC_URL,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Starlette"


def test_the_entrypoint_refuses_an_unconfigured_deployment():
    """Importing must fail loudly rather than serve something unguarded."""
    result = _import_entrypoint()
    assert result.returncode != 0
    assert "Refusing to serve" in result.stderr


def test_the_entrypoint_refuses_an_ambient_portal():
    """A process-wide portal on a multi-tenant deployment is another customer's CRM."""
    result = _import_entrypoint(
        HUBSPOT_MCP_OAUTH_ISSUER=ISSUER,
        HUBSPOT_MCP_PUBLIC_URL=PUBLIC_URL,
        HUBSPOT_PORTAL="99999999",
    )
    assert result.returncode != 0
    assert "ambient portal" in result.stderr


def test_the_entrypoint_refuses_static_portal_tokens():
    result = _import_entrypoint(
        HUBSPOT_MCP_OAUTH_ISSUER=ISSUER,
        HUBSPOT_MCP_PUBLIC_URL=PUBLIC_URL,
        HUBSPOT_TOKEN_99999999="pat-na1-xxx",
    )
    assert result.returncode != 0
    assert "HUBSPOT_TOKEN_" in result.stderr


# --------------------------------------------------------------------------- #
# Deployment configuration
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def vercel_config() -> dict:
    return json.loads((ROOT / "vercel.json").read_text())


def test_the_function_key_matches_the_entrypoint_file(vercel_config):
    """A key naming a file that does not exist silently applies no config."""
    for path in vercel_config["functions"]:
        assert (ROOT / path).exists(), f"vercel.json configures {path}, which does not exist"


def test_the_declared_entrypoint_matches_the_file(vercel_config):
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    module, _, attribute = pyproject["tool"]["vercel"]["entrypoint"].partition(":")

    assert (ROOT / f"{module}.py").exists()
    assert f"{module}.py" in vercel_config["functions"]
    assert attribute == "app"


def test_functions_run_in_the_same_region_as_redis(vercel_config):
    """Vercel defaults to Washington DC. An approve makes ~6 Redis round trips,
    so functions an ocean away from the store cost half a second per write."""
    assert vercel_config["regions"] == ["fra1"]


def test_no_reserved_env_keys_in_the_config():
    """Vercel refuses the WHOLE deployment on a reserved key, before any build.

    `HOME` was set here once; every deployment was rejected at config validation
    and the symptom was a production domain serving 404s, which looks like a
    routing problem and is not one.
    """
    import json
    import pathlib

    config = json.loads((pathlib.Path(__file__).resolve().parent.parent / "vercel.json").read_text())
    reserved = {"HOME", "PATH", "PORT", "NOW_REGION", "VERCEL", "VERCEL_ENV", "VERCEL_URL", "AWS_REGION"}
    declared = set(config.get("env", {}))
    assert not (declared & reserved), f"reserved keys in vercel.json env: {declared & reserved}"


def test_home_is_redirected_in_process_not_in_config():
    """`Path.home()` is resolved by nine modules; $HOME covers all of them."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, app; print(os.environ['HOME'])",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/nonexistent-readonly",
            "VERCEL": "1",
            "REDIS_URL": REDIS_URL,
            "HUBSPOT_MCP_OAUTH_ISSUER": ISSUER,
            "HUBSPOT_MCP_PUBLIC_URL": PUBLIC_URL,
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/tmp"


def test_home_is_left_alone_off_vercel():
    """Nothing should rewrite $HOME on a developer's machine."""
    result = subprocess.run(
        [sys.executable, "-c", "import os, app; print(os.environ['HOME'])"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/tmp/not-vercel",
            "REDIS_URL": REDIS_URL,
            "HUBSPOT_MCP_OAUTH_ISSUER": ISSUER,
            "HUBSPOT_MCP_PUBLIC_URL": PUBLIC_URL,
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/tmp/not-vercel"


def test_tests_are_excluded_from_the_bundle(vercel_config):
    exclude = vercel_config["functions"]["app.py"]["excludeFiles"]
    for directory in ("tests", "docs", "reference"):
        assert directory in exclude, f"{directory} would ship in the function bundle"


def test_the_reference_clone_can_never_ship(vercel_config):
    """`reference/` is a full clone of another repo, gitignored and never deployed."""
    assert "reference/**" in vercel_config["functions"]["app.py"]["excludeFiles"]


def test_requirements_installs_the_hosted_extra():
    """Without the extra there is no redis, no cryptography and no JWT verification."""
    requirements = (ROOT / "requirements.txt").read_text()
    assert ".[hosted]" in requirements


def test_the_entrypoint_refuses_a_hosted_deployment_with_no_redis():
    """Falling back to local disk here loses previews between instances, silently."""
    result = _import_entrypoint(
        HUBSPOT_MCP_OAUTH_ISSUER=ISSUER,
        HUBSPOT_MCP_PUBLIC_URL=PUBLIC_URL,
        REDIS_URL="",
    )
    assert result.returncode != 0
    assert "REDIS_URL" in result.stderr


def test_a_prefixed_redis_url_is_named_in_the_refusal():
    """A Vercel Redis integration connected with a custom prefix renames it."""
    result = _import_entrypoint(
        HUBSPOT_MCP_OAUTH_ISSUER=ISSUER,
        HUBSPOT_MCP_PUBLIC_URL=PUBLIC_URL,
        REDIS_URL="",
        HSMCP_REDIS_URL="redis://localhost:6379/0",
    )
    assert result.returncode != 0
    assert "HSMCP_REDIS_URL" in result.stderr
