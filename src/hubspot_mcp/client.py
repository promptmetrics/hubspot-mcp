from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from hubspot_mcp.app_credentials import get_api_base_url
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.errors import (
    ErrorCategory,
    HubSpotError,
    RateLimitError,
    ScopeError,
)


@dataclass
class APIResponse:
    status_code: int
    body: dict[str, Any]
    headers: dict[str, str]


# Phase 3 PR-B (back-pressure): last-seen HubSpot rate-limit state per portal.
# The durable loop builds a fresh HubSpotClient per step (dispatch closes its
# client immediately), so a per-instance attribute never survives to the loop
# level. We keep a module-level per-portal snapshot that ``_request`` refreshes
# from every response; ``_drive_loop`` reads it between steps to decide pacing.
_LAST_RATE_STATE: dict[str, tuple[int | None, float | None]] = {}


def parse_rate_limit(headers: dict[str, str]) -> tuple[int | None, float | None]:
    """Extract ``(remaining, seconds_until_reset)`` from HubSpot rate headers.

    Reads ``X-HubSpot-RateLimit-Remaining`` and the interval header
    (``X-HubSpot-RateLimit-Interval-Milliseconds``, milliseconds). Header keys
    are matched case-insensitively (httpx lowercases them via ``dict()``).
    Absent or malformed values degrade to ``None`` for that slot; a fully
    absent/empty mapping returns ``(None, None)``. Pure and side-effect free.
    """
    if not headers:
        return (None, None)
    lower = {str(k).lower(): v for k, v in headers.items()}
    remaining: int | None = None
    reset_seconds: float | None = None
    remaining_raw = lower.get("x-hubspot-ratelimit-remaining")
    if remaining_raw is not None:
        try:
            remaining = int(remaining_raw)
        except (TypeError, ValueError):
            remaining = None
    interval_raw = lower.get("x-hubspot-ratelimit-interval-milliseconds")
    if interval_raw is not None:
        try:
            reset_seconds = float(interval_raw) / 1000.0
        except (TypeError, ValueError):
            reset_seconds = None
    return (remaining, reset_seconds)


def record_rate_state(portal_id: str, headers: dict[str, str]) -> tuple[int | None, float | None]:
    """Parse ``headers`` and remember the last-seen rate state for ``portal_id``.

    Converts the interval (seconds-until-reset) into an absolute reset epoch so
    the loop can pace ``sleep until reset``. Stores nothing when both slots are
    ``None`` (unparseable headers), preserving any prior good snapshot. Returns
    the stored ``(remaining, reset_epoch)`` tuple.
    """
    remaining, reset_seconds = parse_rate_limit(headers)
    reset_at = time.time() + reset_seconds if reset_seconds is not None else None
    if remaining is not None or reset_at is not None:
        _LAST_RATE_STATE[portal_id] = (remaining, reset_at)
    return (remaining, reset_at)


def get_last_rate_state(portal_id: str) -> tuple[int | None, float | None]:
    """Return the last-seen ``(remaining, reset_epoch)`` for a portal, or Nones."""
    return _LAST_RATE_STATE.get(portal_id, (None, None))


class HubSpotClient:
    BASE_URL = "https://api.hubapi.com"
    _RATE_LIMIT = 100  # requests per 10 seconds
    _BATCH_CONCURRENT = 4
    _WINDOW_SECONDS = 10
    _REFRESH_BUFFER_SECONDS = 300
    # Upper bound on the per-attempt 429 retry sleep. The server's Retry-After
    # is honored up to this cap; an unreasonable value (e.g. a misconfigured
    # proxy injecting ``Retry-After: 3600``) must not hang the coroutine/loop
    # silently. The raised RateLimitError still carries the raw server value so
    # the loop's pause/approve surface can act on the real backoff.
    _MAX_RETRY_AFTER_SECONDS = 60

    def __init__(self, portal: PortalConfig):
        self.portal = portal
        self._client = httpx.AsyncClient(
            base_url=get_api_base_url(),
            headers={"Authorization": f"Bearer {portal.token}"},
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
        self._semaphore = asyncio.Semaphore(self._RATE_LIMIT)
        self._batch_semaphore = asyncio.Semaphore(self._BATCH_CONCURRENT)
        self._request_times: list[float] = []
        self._token_refresh_lock = asyncio.Lock()

    async def _enforce_rate_limit(self) -> None:
        now = asyncio.get_event_loop().time()
        cutoff = now - self._WINDOW_SECONDS
        self._request_times = [t for t in self._request_times if t > cutoff]
        if len(self._request_times) >= self._RATE_LIMIT:
            sleep_for = self._request_times[0] - cutoff
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
        self._request_times.append(asyncio.get_event_loop().time())

    async def _get_fresh_token(self, force: bool = False) -> str:
        if self.portal.auth_type != "oauth" or not self.portal.refresh_token:
            return self.portal.token

        needs_refresh = force or (
            self.portal.expires_at
            and self.portal.expires_at - time.time() < self._REFRESH_BUFFER_SECONDS
        )
        if not needs_refresh:
            return self.portal.token

        async with self._token_refresh_lock:
            # Re-check inside the lock in case another task already refreshed
            if not force and self.portal.expires_at and self.portal.expires_at - time.time() >= self._REFRESH_BUFFER_SECONDS:
                return self.portal.token
            from hubspot_mcp.oauth_flow import refresh_access_token
            body = await refresh_access_token(self.portal.portal_id, self.portal.refresh_token)
            self.portal.token = body["access_token"]
            self.portal.refresh_token = body.get("refresh_token", self.portal.refresh_token)
            self.portal.expires_at = time.time() + body.get("expires_in", 21600)
            self._client.headers["Authorization"] = f"Bearer {self.portal.token}"
        return self.portal.token

    async def _request(
        self,
        method: str,
        path: str,
        portal_id: str,
        body: Any | None = None,
        expected_scopes: list[str] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> APIResponse:
        method_upper = method.upper()
        is_read = method_upper in ("GET", "HEAD")
        kwargs: dict[str, Any] = {}
        if body is not None:
            kwargs["json"] = body
        if data is not None:
            kwargs["data"] = data
        if files is not None:
            kwargs["files"] = files

        # Reads retry on 429 (bounded) and on 401 (after token refresh); writes
        # raise immediately on 429 so the loop's pause/approve path stays the
        # safe surface for non-idempotent side effects. On a 429 we sleep
        # Retry-After and clear the local sliding window so the next call paces
        # off the server's signal instead of re-sleeping against a stale, full
        # local window.
        max_attempts = 3 if is_read else 1
        for attempt in range(max_attempts):
            await self._enforce_rate_limit()
            await self._get_fresh_token()
            resp = await self._client.request(method, path, **kwargs)
            if resp.status_code == 401:
                await self._get_fresh_token(force=True)
                # Only retry safe (read-only) methods automatically to avoid
                # duplicate side effects if the original was processed before
                # the 401.
                if is_read:
                    resp = await self._client.request(method, path, **kwargs)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 10))
                if is_read and attempt < max_attempts - 1:
                    self._request_times = []
                    await asyncio.sleep(min(retry_after, self._MAX_RETRY_AFTER_SECONDS))
                    continue
                raise RateLimitError("Rate limit exceeded", retry_after=retry_after)
            break
        if resp.status_code == 403:
            if expected_scopes:
                raise ScopeError(
                    f"Missing required scopes: {expected_scopes}",
                    required_scopes=expected_scopes,
                )
            raise HubSpotError(
                resp.text or "Forbidden",
                status_code=403,
                category=ErrorCategory.SCOPE,
            )
        if resp.status_code == 401:
            raise HubSpotError(
                "Token invalid after refresh",
                status_code=401,
                category=ErrorCategory.AUTH,
            )
        if resp.status_code >= 400:
            try:
                error_body = resp.json() if resp.text else {}
            except Exception:
                error_body = {}
            category = ErrorCategory.UNKNOWN
            field_errors = None
            if resp.status_code == 400:
                category = ErrorCategory.VALIDATION
                field_errors = error_body.get("errors")
            elif resp.status_code == 404:
                category = ErrorCategory.NOT_FOUND
            elif resp.status_code == 409:
                category = ErrorCategory.CONFLICT
            elif resp.status_code >= 500:
                category = ErrorCategory.SERVER
            raise HubSpotError(
                resp.text or f"HTTP {resp.status_code}",
                status_code=resp.status_code,
                category=category,
                field_errors=field_errors,
            )
        headers = dict(resp.headers)
        # PR-B back-pressure: remember the server's rate-limit signal so the
        # durable loop can pace between steps. Purely additive — request/retry
        # semantics above are untouched.
        record_rate_state(portal_id, headers)
        return APIResponse(
            status_code=resp.status_code,
            body=resp.json() if resp.text else {},
            headers=headers,
        )

    async def get(
        self, path: str, portal_id: str, expected_scopes: list[str] | None = None
    ) -> APIResponse:
        return await self._request("GET", path, portal_id, expected_scopes=expected_scopes)

    async def post(
        self,
        path: str,
        portal_id: str,
        body: Any | None = None,
        expected_scopes: list[str] | None = None,
    ) -> APIResponse:
        return await self._request("POST", path, portal_id, body, expected_scopes)

    async def patch(
        self,
        path: str,
        portal_id: str,
        body: Any | None = None,
        expected_scopes: list[str] | None = None,
    ) -> APIResponse:
        return await self._request("PATCH", path, portal_id, body, expected_scopes)

    async def put(
        self,
        path: str,
        portal_id: str,
        body: Any | None = None,
        expected_scopes: list[str] | None = None,
    ) -> APIResponse:
        return await self._request("PUT", path, portal_id, body, expected_scopes)

    async def delete(
        self, path: str, portal_id: str, expected_scopes: list[str] | None = None
    ) -> APIResponse:
        return await self._request("DELETE", path, portal_id, expected_scopes=expected_scopes)

    async def post_files(
        self,
        path: str,
        portal_id: str,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        expected_scopes: list[str] | None = None,
    ) -> APIResponse:
        return await self._request("POST", path, portal_id, data=data, files=files, expected_scopes=expected_scopes)

    async def close(self) -> None:
        await self._client.aclose()
