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
