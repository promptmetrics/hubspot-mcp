from pathlib import Path

import pytest

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.config import PortalConfig


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path_factory, monkeypatch):
    """Point ``Path.home`` at a per-test temp dir so tests never read the real
    ``~/.claude/hubspot`` config (app_credentials.json, portal configs).

    This keeps ``HubSpotClient``'s region-aware base URL deterministic: with no
    credentials file present, ``get_region()`` defaults to ``"us"`` so the US
    API base is used (matching the US-mocked respx routes). Tests that exercise
    the EU region save credentials under their own patched ``Path.home``,
    which overrides this fixture's patch for the duration of the test.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setattr(Path, "home", lambda: home)
    yield


@pytest.fixture
def mock_portal():
    return PortalConfig(portal_id="123", token="test-token", tier="Professional")


@pytest.fixture
async def test_client(mock_portal):
    client = HubSpotClient(mock_portal)
    yield client
    await client.close()


@pytest.fixture(autouse=True)
def _no_live_docs_fetches(monkeypatch, request):
    """Keep the docs backend off the network unless a test mocks it.

    ``hubspot_docs_search`` now falls back to the built-in keyless backend, so
    any test that calls it without mocking would fetch developers.hubspot.com --
    slow, and a CI failure the day HubSpot has an outage. Tests that exercise
    the backend use respx and opt out via the ``live_docs_backend`` marker.
    """
    if request.node.get_closest_marker("live_docs_backend"):
        return

    async def _refuse(query, domain, limit):
        raise AssertionError(
            "docs backend reached the network in a test — mock it with respx, "
            "or mark the test with @pytest.mark.live_docs_backend"
        )

    monkeypatch.setattr("hubspot_mcp.tools.docs._builtin_backend", lambda: _refuse)
