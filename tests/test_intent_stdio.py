"""``hubspot-mcp --surface intent`` serves only the pronova_* tools over stdio.

One stdio process has one stdin, so it serves one surface. These tests pin the
CLI flag and env var that choose it, and run the real console script over stdio
to check what a local MCP client would see.
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

from hubspot_mcp import __main__ as cli
from hubspot_mcp import server


@pytest.fixture
def ran(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(server, "configure_server", lambda **kw: None)
    monkeypatch.setattr(server, "run", lambda **kw: calls.append(kw))
    monkeypatch.delenv("HUBSPOT_MCP_SURFACE", raising=False)
    return calls


@pytest.mark.parametrize(
    ("argv", "env", "want"),
    [
        ([], None, "full"),
        (["--surface", "intent"], None, "intent"),
        (["run", "--surface", "grouped"], None, "grouped"),
        (["--surface", "intent", "run"], None, "intent"),
        ([], "intent", "intent"),
        (["--surface", "routed"], "intent", "routed"),
    ],
)
def test_the_surface_comes_from_the_flag_then_the_env(ran, monkeypatch, argv, env, want):
    if env:
        monkeypatch.setenv("HUBSPOT_MCP_SURFACE", env)
    monkeypatch.setattr(sys, "argv", ["hubspot-mcp", *argv])
    assert cli.main() == 0
    assert ran[-1]["surface"] == want
    assert ran[-1]["transport"] == "stdio"


def test_an_unknown_surface_is_refused_before_the_server_starts(ran, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["hubspot-mcp", "--surface", "intnet"])
    assert cli.main() == 2
    assert ran == []
    assert "intnet" in capsys.readouterr().err


def test_run_refuses_an_unknown_surface():
    with pytest.raises(ValueError, match="Unknown surface"):
        server.run(surface="nope")


@pytest.mark.parametrize(("surface", "count", "prefix"), [("intent", 13, "pronova_"), ("grouped", 12, "")])
async def test_the_console_script_serves_only_that_surface_over_stdio(tmp_path, surface, count, prefix):
    exe = shutil.which("hubspot-mcp", path=os.path.dirname(sys.executable))
    if exe is None:
        pytest.skip("hubspot-mcp console script not installed in this environment")
    # A portal with no token: tools/list still answers, and nothing reaches HubSpot.
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("HUBSPOT_TOKEN_", "HUBSPOT_PRIVATE_APP_TOKEN"))
    }
    env.update({"HOME": str(tmp_path), "HUBSPOT_MCP_TRACE": "0"})
    params = StdioServerParameters(
        command=exe, args=["--portal", "99999999", "--mode", "token", "--surface", surface], env=env
    )
    async with Client(stdio_client(params)) as client:
        names = [t.name for t in (await client.list_tools()).tools]
    assert len(names) == count
    assert all(n.startswith(prefix) for n in names)
    assert not any(n.startswith("hubspot_") for n in names)
