import os
import stat
from pathlib import Path


def test_save_portal_config_sets_0o600(tmp_path, monkeypatch):
    # M2: the portal JSON carries token + refresh_token and must be 0600 from
    # birth (atomic replace), never briefly world-readable.
    from hubspot_mcp.config import PortalConfig, save_portal_config

    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    save_portal_config(PortalConfig(portal_id="123", token="t"))
    assert stat.S_IMODE(os.stat(tmp_path / "123.json").st_mode) == 0o600


def test_detect_portal_from_file(tmp_path):
    from hubspot_mcp.config import detect_default_portal
    portal_file = tmp_path / ".hubspot-portal"
    portal_file.write_text("1234567\n")
    result = detect_default_portal(str(tmp_path))
    assert result == "1234567"


def test_detect_portal_no_file(tmp_path):
    from hubspot_mcp.config import detect_default_portal
    result = detect_default_portal(str(tmp_path))
    assert result is None


def test_load_portal_config_from_env(monkeypatch, tmp_path):
    from hubspot_mcp.config import load_portal_config
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path)
    monkeypatch.setenv("HUBSPOT_TOKEN_123", "token-123")
    monkeypatch.setenv("HUBSPOT_TIER_123", "Professional")
    config = load_portal_config("123")
    assert config is not None
    assert config.token == "token-123"
    assert config.tier == "Professional"


def test_save_and_load_portal_config(tmp_path, monkeypatch):
    from hubspot_mcp.config import save_portal_config, load_portal_config, PortalConfig
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path / "hubspot")
    portal = PortalConfig(portal_id="456", token="secret-token")
    save_portal_config(portal)
    loaded = load_portal_config("456")
    assert loaded is not None
    assert loaded.token == "secret-token"


def test_json_config_with_oauth_fields(tmp_path, monkeypatch):
    from hubspot_mcp.config import save_portal_config, load_portal_config, PortalConfig
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path / "hubspot")
    portal = PortalConfig(
        portal_id="789",
        token="oauth-access-token",
        auth_type="oauth",
        refresh_token="refresh-123",
        expires_at=1700000000.0,
        tier="Enterprise",
    )
    save_portal_config(portal)
    loaded = load_portal_config("789")
    assert loaded is not None
    assert loaded.token == "oauth-access-token"
    assert loaded.auth_type == "oauth"
    assert loaded.refresh_token == "refresh-123"
    assert loaded.expires_at == 1700000000.0
    assert loaded.tier == "Enterprise"


def test_backward_compat_token_file(tmp_path, monkeypatch):
    from hubspot_mcp.config import CONFIG_DIR, load_portal_config
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", tmp_path / "hubspot")
    token_file = (tmp_path / "hubspot") / "12345.token"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("legacy-token-value")
    loaded = load_portal_config("12345")
    assert loaded is not None
    assert loaded.token == "legacy-token-value"
    assert loaded.auth_type == "private_app"
