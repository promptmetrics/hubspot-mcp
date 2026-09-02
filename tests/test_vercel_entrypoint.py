"""The Vercel entrypoint (Phase 3, stage 1).

Vercel imports `api/index.py` and serves `app`; it never calls `server.run`. So every
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
        [sys.executable, "-c", "import importlib; m = importlib.import_module('api.index'); print(type(m.app).__name__)"],
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


def test_no_reserved_env_keys_in_the_config(vercel_config):
    """Vercel refuses the WHOLE deployment on a reserved key, before any build.

    `HOME` was set here once; every deployment was rejected at config validation
    and the symptom was a production domain serving 404s, which looks like a
    routing problem and is not one.
    """
    reserved = {"HOME", "PATH", "PORT", "NOW_REGION", "VERCEL", "VERCEL_ENV", "VERCEL_URL", "AWS_REGION"}
    declared = set(vercel_config.get("env", {}))
    assert not (declared & reserved), f"reserved keys in vercel.json env: {declared & reserved}"


def test_every_function_pattern_targets_the_api_directory(vercel_config):
    """A pattern outside `api/` fails the build outright.

    "The pattern "app.py" defined in `functions` doesn't match any Serverless
    Functions inside the `api` directory."
    """
    patterns = vercel_config.get("functions", {})
    assert patterns, "no functions configured; maxDuration would fall back to the default"
    for path in patterns:
        assert path.startswith(("api/", "pages/api/")), (
            f"vercel.json configures {path}, which is not under api/ and will fail the build"
        )
        assert (ROOT / path).exists(), f"vercel.json configures {path}, which does not exist"


def test_all_paths_route_to_the_entrypoint(vercel_config):
    """Without a catch-all rewrite only `/api/index` reaches the app — not
    `/mcp`, `/healthz` or the connect routes."""
    rewrites = vercel_config.get("rewrites", [])
    assert any(
        r["source"] == "/(.*)" and r["destination"] == "/api/index" for r in rewrites
    ), "no catch-all rewrite; every route except /api/index would 404"


def test_functions_run_in_the_same_region_as_redis(vercel_config):
    """Vercel defaults to Washington DC. An approve makes ~6 Redis round trips,
    so functions an ocean away from the store cost half a second per write."""
    assert vercel_config["regions"] == ["fra1"]


def test_the_bundle_excludes_what_it_does_not_need():
    """`excludeFiles` needs a `functions` block we cannot have, so this is
    `.vercelignore` instead — same job, valid config."""
    ignored = (ROOT / ".vercelignore").read_text()
    for directory in ("tests/", "docs/", "reference/"):
        assert directory in ignored, f"{directory} would ship in the function bundle"


def test_the_bundle_keeps_what_it_does_need():
    """Over-excluding is the other failure: a bundle that builds and cannot run."""
    ignored = [
        line.strip()
        for line in (ROOT / ".vercelignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    for required in ("src/", "app.py", "pyproject.toml", "requirements.txt"):
        assert required not in ignored


def test_runtime_dependencies_are_not_optional():
    """Vercel installs `[project.dependencies]` from pyproject.toml — not the
    extras, and not requirements.txt.

    They were an optional `hosted` extra once. The deployment authenticated
    correctly and then failed every tool call on `No module named 'redis'`,
    because an extra is exactly the thing Vercel does not install.
    """
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    declared = " ".join(pyproject["project"]["dependencies"])
    for package in ("redis", "cryptography", "pyjwt"):
        assert package in declared, f"{package} is not a runtime dependency; the deploy will not have it"


def test_no_requirements_txt_implying_otherwise():
    """It was ignored in favour of pyproject.toml, so its presence only misled."""
    assert not (ROOT / "requirements.txt").exists()


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
