from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from hubspot_mcp.app_credentials import (
    get_client_id,
    get_client_secret,
    get_oauth_endpoints,
    get_token_fallback_url,
)
from hubspot_mcp.config import CONFIG_DIR, PortalConfig, load_portal_config, save_portal_config
from hubspot_mcp.fileio import write_private_json

_REFRESH_BUFFER_SECONDS = 300  # refresh if within 5 minutes of expiry
_STATE_EXPIRY_SECONDS = 600  # 10 minutes


def _build_code_verifier() -> str:
    return secrets.token_urlsafe(48)


def _build_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _oauth_state_dir() -> Path:
    path = CONFIG_DIR / "oauth_states"
    path.mkdir(parents=True, exist_ok=True)
    return path


# The OAuth ``state`` is attacker-influenceable: it arrives back verbatim from
# the callback URL, so it must be validated before it touches a filesystem
# path. "../<portal_id>" would resolve to the portal token file, which
# _clear_oauth_state unlinks — a forced-re-auth DoS.
_STATE_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _valid_state(state: str) -> bool:
    return bool(state and _STATE_RE.fullmatch(state))


def _oauth_state_file(state: str) -> Path:
    if not _valid_state(state):
        raise ValueError("Invalid OAuth state.")
    return _oauth_state_dir() / f"{state}.json"


def _save_oauth_state(
    state: str,
    portal_id: str,
    code_verifier: str,
    redirect_uri: str,
) -> None:
    path = _oauth_state_file(state)
    payload = {
        "portal_id": portal_id,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "expires_at": time.time() + _STATE_EXPIRY_SECONDS,
    }
    write_private_json(path, payload)


def _load_oauth_state(state: str) -> dict[str, Any] | None:
    if not _valid_state(state):
        return None
    path = _oauth_state_file(state)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() > data.get("expires_at", 0):
            _clear_oauth_state(state)
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _clear_oauth_state(state: str) -> None:
    if not _valid_state(state):
        return
    try:
        _oauth_state_file(state).unlink(missing_ok=True)
    except OSError:
        pass


def get_authorization_url(
    portal_id: str,
    scopes: list[str],
    redirect_uri: str = "http://localhost:3000/oauth/callback",
) -> str:
    client_id = get_client_id()
    if not client_id:
        raise ValueError("HubSpot app credentials not found. Run save_app_credentials() first.")

    authorize_url, _ = get_oauth_endpoints()

    state = secrets.token_urlsafe(32)
    code_verifier = _build_code_verifier()
    code_challenge = _build_code_challenge(code_verifier)

    _save_oauth_state(state, portal_id, code_verifier, redirect_uri)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "response_type": "code",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{authorize_url}?{urllib.parse.urlencode(params)}"


async def _post_token_request(data: dict[str, Any]) -> dict[str, Any]:
    """POST a token grant to the 2026-03 endpoint, falling back to legacy v3 on 404.

    The 2026-03 date-based endpoint is Phase 1's primary; if HubSpot ever returns
    404 for it (path retired ahead of schedule, regional routing quirk), retry the
    same payload against the legacy v3 endpoint before surfacing the error. Any
    non-404 failure is raised immediately so callers see the real status code.
    """
    _, primary_url = get_oauth_endpoints()
    fallback_url = get_token_fallback_url()
    async with httpx.AsyncClient() as client:
        resp = await client.post(primary_url, data=data)
        if resp.status_code == 404 and fallback_url:
            resp = await client.post(fallback_url, data=data)
        resp.raise_for_status()
        return resp.json()


async def exchange_code_for_token(
    portal_id: str,
    code: str,
    state: str,
    redirect_uri: str = "http://localhost:3000/oauth/callback",
) -> dict[str, Any]:
    client_id = get_client_id()
    client_secret = get_client_secret()
    if not client_id or not client_secret:
        raise ValueError("HubSpot app credentials not found.")

    state_data = _load_oauth_state(state)
    if not state_data:
        raise ValueError("Invalid or expired OAuth state. Restart the authorization flow.")
    if state_data["portal_id"] != portal_id:
        raise ValueError("OAuth state mismatch. Restart the authorization flow.")

    code_verifier = state_data["code_verifier"]
    _clear_oauth_state(state)

    body = await _post_token_request(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
            "code_verifier": code_verifier,
        }
    )

    _save_oauth_tokens(
        portal_id=portal_id,
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token"),
        expires_in=body.get("expires_in", 21600),
        scopes_granted=body.get("scope", "").split() or None,
    )
    return body


async def refresh_tokens_only(refresh_token: str) -> dict[str, Any]:
    """Exchange a refresh token for a new grant, persisting nothing.

    The network half of :func:`refresh_access_token`. Split out because the
    hosted path stores tokens per user in the connection store, not in the
    local portal file — but both must use one endpoint, one payload and one
    404-fallback rule, so there is only ever one thing to get right.
    """
    client_id = get_client_id()
    client_secret = get_client_secret()
    if not client_id or not client_secret:
        raise ValueError("HubSpot app credentials not found.")

    return await _post_token_request(
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }
    )


async def refresh_access_token(portal_id: str, refresh_token: str) -> dict[str, Any]:
    body = await refresh_tokens_only(refresh_token)

    _save_oauth_tokens(
        portal_id=portal_id,
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token", refresh_token),
        expires_in=body.get("expires_in", 21600),
        scopes_granted=body.get("scope", "").split() or None,
    )
    return body


async def get_valid_token(portal_id: str) -> str | None:
    portal = load_portal_config(portal_id)
    if not portal:
        return None

    if portal.auth_type == "oauth":
        if portal.expires_at and portal.expires_at - time.time() < _REFRESH_BUFFER_SECONDS:
            if portal.refresh_token:
                body = await refresh_access_token(portal_id, portal.refresh_token)
                return body["access_token"]
            return None
        return portal.token

    return portal.token


def _save_oauth_tokens(
    portal_id: str,
    access_token: str,
    refresh_token: str | None,
    expires_in: int,
    scopes_granted: list[str] | None = None,
) -> None:

    # A refresh response may omit the ``scope`` field; preserve the previously
    # granted scopes so the setup scope-gap report stays accurate across refresh.
    if scopes_granted is None:
        existing = load_portal_config(portal_id)
        scopes_granted = existing.scopes_granted if existing else None

    portal = PortalConfig(
        portal_id=portal_id,
        token=access_token,
        auth_type="oauth",
        refresh_token=refresh_token,
        expires_at=time.time() + expires_in,
        scopes_granted=scopes_granted,
    )
    save_portal_config(portal)
