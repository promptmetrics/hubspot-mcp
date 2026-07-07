from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class PortalConfig:
    portal_id: str
    token: str
    tier: str = "unknown"
    scopes_granted: list[str] | None = None
    auth_type: str = "private_app"  # "oauth" or "private_app"
    refresh_token: str | None = None
    expires_at: float | None = None  # Unix timestamp


CONFIG_DIR = Path.home() / ".claude" / "hubspot"


def _ensure_config_dir() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def _validate_portal_id(portal_id: str) -> None:
    if not portal_id or not re.fullmatch(r"[0-9]+", portal_id):
        raise ValueError(f"Invalid portal_id: {portal_id}")


def detect_default_portal(working_dir: str) -> str | None:
    portal_file = Path(working_dir) / ".hubspot-portal"
    if portal_file.exists():
        return portal_file.read_text().strip().splitlines()[0].strip()
    return None


def _portal_json_file(portal_id: str) -> Path:
    _validate_portal_id(portal_id)
    return CONFIG_DIR / f"{portal_id}.json"


def _portal_token_file(portal_id: str) -> Path:
    _validate_portal_id(portal_id)
    return CONFIG_DIR / f"{portal_id}.token"


def load_portal_config(portal_id: str) -> PortalConfig | None:
    """Load portal config from JSON file, .token fallback, or environment."""
    # 1. Try new JSON config file
    json_file = _portal_json_file(portal_id)
    if json_file.exists():
        try:
            data = json.loads(json_file.read_text())
            scopes = data.get("scopes_granted")
            if isinstance(scopes, str):
                scopes = scopes.split(",") if scopes else []
            return PortalConfig(
                portal_id=data["portal_id"],
                token=data["token"],
                tier=data.get("tier", "unknown"),
                scopes_granted=scopes,
                auth_type=data.get("auth_type", "private_app"),
                refresh_token=data.get("refresh_token"),
                expires_at=data.get("expires_at"),
            )
        except (json.JSONDecodeError, KeyError):
            pass

    # 2. Fall back to plain .token file (treat as private_app)
    token = os.getenv(f"HUBSPOT_TOKEN_{portal_id}")
    if not token:
        token_file = _portal_token_file(portal_id)
        if token_file.exists():
            token = token_file.read_text().strip()
    if not token:
        return None

    return PortalConfig(
        portal_id=portal_id,
        token=token,
        tier=os.getenv(f"HUBSPOT_TIER_{portal_id}", "unknown"),
        scopes_granted=os.getenv(f"HUBSPOT_SCOPES_{portal_id}", "").split(",") if os.getenv(f"HUBSPOT_SCOPES_{portal_id}") else [],
        auth_type="private_app",
    )


def save_portal_config(portal: PortalConfig) -> None:
    _validate_portal_id(portal.portal_id)
    _ensure_config_dir()
    json_file = _portal_json_file(portal.portal_id)
    data: dict[str, Any] = {
        "portal_id": portal.portal_id,
        "token": portal.token,
        "tier": portal.tier,
        "auth_type": portal.auth_type,
    }
    if portal.scopes_granted:
        data["scopes_granted"] = ",".join(portal.scopes_granted) if isinstance(portal.scopes_granted, list) else portal.scopes_granted
    if portal.refresh_token:
        data["refresh_token"] = portal.refresh_token
    if portal.expires_at:
        data["expires_at"] = portal.expires_at
    json_file.write_text(json.dumps(data, indent=2))
    json_file.chmod(0o600)

    # Remove old .token file if present to avoid ambiguity
    token_file = _portal_token_file(portal.portal_id)
    if token_file.exists():
        token_file.unlink()
