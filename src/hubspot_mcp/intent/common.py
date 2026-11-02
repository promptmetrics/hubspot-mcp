"""Shared plumbing for the intent tools: session, throttled reads, lookups, deal lines.

Every job module (triage, forecast, bulk hygiene, ...) needs the same things: the
session with its not-connected handling, Search and batch reads that respect
HubSpot's limits, stage and owner lookups, and one compact deal shape. They live
here so a limit is handled once and every job returns the same fields.

Three limits this module absorbs, none of which ``client.py`` handles for POSTs:

* Search is capped at 5 requests/second per portal. ``client.py`` only has a
  100-requests-per-10-seconds window, so :func:`search_page` runs every call
  through a per-portal limiter.
* ``HubSpotClient`` retries a 429 on GET only; a POST raises on the first one.
  Search and batch/read are POSTs but idempotent, so :func:`read_post` retries
  them. A rejected write was never processed either, so :func:`write_post` retries
  those too, a few times, rather than reporting a rate limit as a failed change.
* Search returns at most 10,000 results per query and 200 per page. :func:`search_all`
  stops at the cap and says so (``truncated``) instead of paging into a 400.

Like the rest of this package, nothing here imports ``hubspot_mcp.server`` at
module level; the server imports the intent package while it is still loading.
"""
from __future__ import annotations

import asyncio
import functools
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from weakref import WeakKeyDictionary
from zoneinfo import ZoneInfo

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from hubspot_mcp.errors import HubSpotError, RateLimitError
from hubspot_mcp.intent.rules import SalesRules, normalize_category

ResponseFormat = Literal["concise", "detailed"]
ResponseFormatParam = Annotated[
    ResponseFormat,
    Field(description="concise returns the compact fields; detailed adds more fields (extra dates, ids, owners, longer text) and costs more tokens."),
]

# --- limits ----------------------------------------------------------------

SEARCH_MAX_PER_WINDOW = 5
# 1.1 s rather than 1.0 s: the window is measured on our clock, HubSpot's on theirs.
SEARCH_WINDOW_SECONDS = 1.1
SEARCH_PAGE_SIZE = 200
SEARCH_RESULT_CAP = 10_000
# HubSpot Search takes at most 6 filters in one group (and 5 groups, 18 filters in all); one AND group here.
SEARCH_MAX_FILTERS = 6
BATCH_READ_SIZE = 100
ASSOCIATION_BATCH_SIZE = 100
# A guard against a cursor that never ends, not a limit anyone should reach: pages are large.
MAX_ASSOCIATION_PAGES = 200
MAX_RETRY_WAIT_SECONDS = 30.0
READ_ATTEMPTS = 4
WRITE_ATTEMPTS = 3
CACHE_TTL_SECONDS = 300.0

DEAL_PROPERTIES: tuple[str, ...] = (
    "dealname",
    "amount",
    "deal_currency_code",
    "amount_in_home_currency",
    "closedate",
    "dealstage",
    "pipeline",
    "hubspot_owner_id",
    "hs_manual_forecast_category",
    "hs_forecast_probability",
    "hs_deal_stage_probability",
    "hs_is_closed",
    "hs_is_closed_won",
    "hs_next_step",
    "notes_last_contacted",
    "notes_next_activity_date",
    "hs_date_entered_current_stage",
    "hs_v2_date_entered_current_stage",
    "createdate",
)

OBJECT_TYPE_IDS = {
    "contacts": "0-1",
    "companies": "0-2",
    "deals": "0-3",
    "tickets": "0-5",
    "tasks": "0-27",
    "notes": "0-46",
    "meetings": "0-47",
    "calls": "0-48",
    "emails": "0-49",
}


# --- session ---------------------------------------------------------------


class NotConnected(Exception):
    """The caller has not linked a HubSpot portal; ``result`` is the guidance to return as-is."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("not connected")
        self.result = result


async def session(ctx: Any) -> dict[str, Any]:
    """Resolve the HubSpot session for this request, or stop the tool cleanly.

    Returns the session dict (``client``, ``portal_id``, ``portal_config``, ...).
    Not connected with a connect link is a normal first-run state, not a failure:
    this raises :class:`NotConnected`, and :func:`answers_when_not_connected`
    turns that into the guidance result. Any other auth failure raises ``ToolError``.
    """
    from hubspot_mcp import server

    lf = await server._session(ctx)
    if lf.get("auth_error"):
        guidance = server._not_connected_result(lf)
        if guidance is not None:
            raise NotConnected(guidance)
        raise ToolError(str(lf["auth_error"]))
    return lf


def answers_when_not_connected(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool so :class:`NotConnected` becomes its guidance result.

    ``functools.wraps`` keeps the real signature, so the MCP SDK still builds the
    input schema from ``fn`` and still injects ``ctx``. Applying it twice is a no-op.
    """
    if getattr(fn, "_answers_when_not_connected", False):
        return fn

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except NotConnected as exc:
            return exc.result

    wrapper._answers_when_not_connected = True  # type: ignore[attr-defined]
    return wrapper


def guard_tools(tools: Iterable[tuple[str, Any, str]]) -> list[tuple[str, Any, str]]:
    """Apply :func:`answers_when_not_connected` to every ``(name, fn, description)``."""
    return [(name, answers_when_not_connected(fn), desc) for name, fn, desc in tools]


# --- HTTP with limits ------------------------------------------------------


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class SlidingWindowLimiter:
    """At most ``max_calls`` acquisitions in any ``window_seconds``; waiters queue in order."""

    def __init__(
        self,
        max_calls: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._stamps and now - self._stamps[0] >= self.window:
                    self._stamps.popleft()
                if len(self._stamps) < self.max_calls:
                    self._stamps.append(now)
                    return
                wait = self.window - (now - self._stamps[0])
                await (self._sleep or _sleep)(max(wait, 0.001))


# One limiter per (event loop, portal): an asyncio.Lock binds to the loop it is
# first contended on, and the test suite runs each test on its own loop.
_search_limiters: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, SlidingWindowLimiter]] = (
    WeakKeyDictionary()
)


def search_limiter(portal_id: str) -> SlidingWindowLimiter:
    per_loop = _search_limiters.setdefault(asyncio.get_running_loop(), {})
    limiter = per_loop.get(portal_id)
    if limiter is None:
        limiter = per_loop[portal_id] = SlidingWindowLimiter(SEARCH_MAX_PER_WINDOW, SEARCH_WINDOW_SECONDS)
    return limiter


def describe_error(exc: HubSpotError) -> str:
    """One readable line for a HubSpot failure, with the body trimmed."""
    text = " ".join(str(exc.message).split())
    if len(text) > 300:
        text = text[:300] + "..."
    return f"HTTP {exc.status_code}: {text}" if exc.status_code else text


async def _retrying(
    call: Callable[[], Awaitable[Any]], *, attempts: int, limiter: SlidingWindowLimiter | None = None
) -> Any:
    for attempt in range(attempts):
        if limiter is not None:
            await limiter.acquire()
        try:
            return await call()
        except RateLimitError as exc:
            if attempt == attempts - 1:
                raise
            await _sleep(min(float(exc.retry_after or 2**attempt), MAX_RETRY_WAIT_SECONDS))
    raise AssertionError("unreachable")  # pragma: no cover


async def post_raw(
    lf: dict[str, Any], path: str, body: Any, *, search: bool = False, attempts: int = READ_ATTEMPTS
) -> dict[str, Any]:
    """POST through the session client, retrying 429s; raises ``HubSpotError`` as-is."""
    client, portal_id = lf["client"], lf["portal_id"]
    limiter = search_limiter(portal_id) if search else None
    resp = await _retrying(lambda: client.post(path, portal_id, body), attempts=attempts, limiter=limiter)
    return resp.body or {}


async def delete_raw(lf: dict[str, Any], path: str, *, attempts: int = WRITE_ATTEMPTS) -> dict[str, Any]:
    """DELETE (archive) through the session client, retrying 429s; raises ``HubSpotError`` as-is."""
    client, portal_id = lf["client"], lf["portal_id"]
    resp = await _retrying(lambda: client.delete(path, portal_id), attempts=attempts)
    return resp.body or {}


async def get_raw(lf: dict[str, Any], path: str) -> dict[str, Any]:
    client, portal_id = lf["client"], lf["portal_id"]
    resp = await client.get(path, portal_id)
    return resp.body or {}


async def read_post(
    lf: dict[str, Any], path: str, body: Any, *, search: bool = False
) -> dict[str, Any]:
    """Read-style POST (search, batch/read) that turns a HubSpot failure into a ``ToolError``."""
    try:
        return await post_raw(lf, path, body, search=search)
    except HubSpotError as exc:
        raise ToolError(f"HubSpot rejected POST {path}. {describe_error(exc)}") from exc


async def read_get(lf: dict[str, Any], path: str) -> dict[str, Any]:
    try:
        return await get_raw(lf, path)
    except HubSpotError as exc:
        raise ToolError(f"HubSpot rejected GET {path}. {describe_error(exc)}") from exc


def chunked(items: Sequence[Any], size: int) -> list[list[Any]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


# --- search ----------------------------------------------------------------


@dataclass
class SearchResult:
    results: list[dict[str, Any]]
    total: int | None = None
    truncated: bool = False
    after: str | None = None  # set when truncated: pass it back to continue


def epoch_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)


def prop_filter(name: str, operator: str, value: Any = None, high_value: Any = None) -> dict[str, Any]:
    flt: dict[str, Any] = {"propertyName": name, "operator": operator}
    if value is not None:
        flt["value"] = str(value)
    if high_value is not None:
        flt["highValue"] = str(high_value)
    return flt


def deal_filter_groups(
    *,
    owner_id: str | None = None,
    open_only: bool = True,
    closedate_before: date | None = None,
    closedate_from: date | None = None,
    closedate_to: date | None = None,
) -> list[dict[str, Any]]:
    """One AND group of deal filters. ``closedate_to`` is inclusive; ``closedate_before`` is exclusive."""
    filters: list[dict[str, Any]] = []
    if owner_id:
        filters.append(prop_filter("hubspot_owner_id", "EQ", owner_id))
    if open_only:
        filters.append(prop_filter("hs_is_closed", "EQ", "false"))
    if closedate_before is not None:
        filters.append(prop_filter("closedate", "LT", epoch_ms(closedate_before)))
    if closedate_from is not None:
        filters.append(prop_filter("closedate", "GTE", epoch_ms(closedate_from)))
    if closedate_to is not None:
        # Strictly before the next midnight, so a close date at any time on the last day counts.
        filters.append(prop_filter("closedate", "LT", epoch_ms(closedate_to) + 86_400_000))
    return [{"filters": filters}] if filters else []


def _next_after(body: dict[str, Any]) -> str | None:
    nxt = (body.get("paging") or {}).get("next") or {}
    after = nxt.get("after")
    return str(after) if after not in (None, "") else None


async def search_page(
    lf: dict[str, Any],
    object_type: str,
    *,
    filter_groups: list[dict[str, Any]] | None = None,
    properties: Sequence[str] | None = None,
    sorts: list[dict[str, str]] | None = None,
    query: str | None = None,
    limit: int = 100,
    after: str | None = None,
) -> dict[str, Any]:
    """One throttled, retried Search page; returns HubSpot's body (``results``, ``paging``, ``total``)."""
    if not 1 <= limit <= SEARCH_PAGE_SIZE:
        raise ToolError(f"limit must be between 1 and {SEARCH_PAGE_SIZE}")
    body: dict[str, Any] = {"limit": limit}
    if filter_groups:
        body["filterGroups"] = filter_groups
    if properties:
        body["properties"] = list(properties)
    if sorts:
        body["sorts"] = sorts
    if query:
        body["query"] = query
    if after:
        body["after"] = after
    return await read_post(lf, f"/crm/v3/objects/{object_type}/search", body, search=True)


async def search_all(
    lf: dict[str, Any],
    object_type: str,
    *,
    filter_groups: list[dict[str, Any]] | None = None,
    properties: Sequence[str] | None = None,
    sorts: list[dict[str, str]] | None = None,
    query: str | None = None,
    page_size: int = SEARCH_PAGE_SIZE,
    max_results: int = SEARCH_RESULT_CAP,
    after: str | None = None,
) -> SearchResult:
    """Page through a search until the end or ``max_results`` (never past HubSpot's 10,000 cap).

    Default sort is ``hs_object_id`` ascending, so a re-run over unchanged data
    returns the same order and paging does not reshuffle.
    """
    max_results = min(max_results, SEARCH_RESULT_CAP)
    sorts = sorts or [{"propertyName": "hs_object_id", "direction": "ASCENDING"}]
    out = SearchResult(results=[])
    seen_after: set[str] = set()
    while True:
        want = min(page_size, max_results - len(out.results))
        body = await search_page(
            lf,
            object_type,
            filter_groups=filter_groups,
            properties=properties,
            sorts=sorts,
            query=query,
            limit=max(want, 1),
            after=after,
        )
        out.results.extend(body.get("results") or [])
        if body.get("total") is not None:
            out.total = int(body["total"])
        after = _next_after(body)
        if after is None:
            return out
        if len(out.results) >= max_results:
            out.truncated = True
            out.after = after
            return out
        if after in seen_after:
            raise ToolError("HubSpot returned the same paging cursor twice; stopping to avoid a loop.")
        seen_after.add(after)


async def search_deals(
    lf: dict[str, Any],
    *,
    owner_id: str | None = None,
    open_only: bool = True,
    closedate_before: date | None = None,
    closedate_from: date | None = None,
    closedate_to: date | None = None,
    properties: Sequence[str] = DEAL_PROPERTIES,
    max_results: int = SEARCH_RESULT_CAP,
) -> SearchResult:
    return await search_all(
        lf,
        "deals",
        filter_groups=deal_filter_groups(
            owner_id=owner_id,
            open_only=open_only,
            closedate_before=closedate_before,
            closedate_from=closedate_from,
            closedate_to=closedate_to,
        ),
        properties=properties,
        max_results=max_results,
    )


# --- batch reads -----------------------------------------------------------


async def batch_read(
    lf: dict[str, Any],
    object_type: str,
    ids: Iterable[Any],
    properties: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Read records by id, 100 per call. Unknown or archived ids are simply absent from the result."""
    unique = list(dict.fromkeys(str(i) for i in ids))
    results: list[dict[str, Any]] = []
    for chunk in chunked(unique, BATCH_READ_SIZE):
        body: dict[str, Any] = {"inputs": [{"id": i} for i in chunk]}
        if properties:
            body["properties"] = list(properties)
        resp = await read_post(lf, f"/crm/v3/objects/{object_type}/batch/read", body)
        results.extend(resp.get("results") or [])
    return results


async def batch_read_associations(
    lf: dict[str, Any], from_type: str, to_type: str, ids: Iterable[Any]
) -> dict[str, list[str]]:
    """``{from_id: [to_id, ...]}`` for every id asked about; ids with no associations map to ``[]``.

    HubSpot pages each record's associations: a row carries ``paging.next.after`` when more
    exist, and the next read passes it back as ``after`` for that record. Every cursor is
    followed, so a long email history is not silently cut at the first page.
    """
    unique = list(dict.fromkeys(str(i) for i in ids))
    found: dict[str, list[str]] = {i: [] for i in unique}
    seen: dict[str, set[str]] = {i: set() for i in unique}
    path = f"/crm/v4/associations/{from_type}/{to_type}/batch/read"
    for chunk in chunked(unique, ASSOCIATION_BATCH_SIZE):
        inputs: list[dict[str, Any]] = [{"id": i} for i in chunk]
        for _ in range(MAX_ASSOCIATION_PAGES):
            resp = await read_post(lf, path, {"inputs": inputs})
            inputs = []
            for row in resp.get("results") or []:
                from_id = str((row.get("from") or {}).get("id"))
                bucket, known = found.setdefault(from_id, []), seen.setdefault(from_id, set())
                for target in row.get("to") or []:
                    to_id = target.get("toObjectId")
                    if to_id is not None and str(to_id) not in known:
                        known.add(str(to_id))
                        bucket.append(str(to_id))
                after = ((row.get("paging") or {}).get("next") or {}).get("after")
                if after not in (None, ""):
                    inputs.append({"id": from_id, "after": str(after)})
            if not inputs:
                break
    return found


# --- values ----------------------------------------------------------------


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_DIGITS = re.compile(r"-?\d+")


def to_datetime(value: Any) -> datetime | None:
    """HubSpot date values (epoch ms, ISO datetime, ISO date) -> aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    text = str(value).strip()
    try:
        if _DIGITS.fullmatch(text):
            return datetime.fromtimestamp(int(text) / 1000, tz=UTC)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def to_date(value: Any, tz: str | None = None) -> date | None:
    """Calendar date of a HubSpot value.

    ``tz=None`` gives the UTC date: right for ``closedate``, which HubSpot stores as
    midnight UTC of the picked day. Pass the portal timezone for real timestamps
    such as ``notes_last_contacted``.
    """
    moment = to_datetime(value)
    if moment is None:
        return None
    return (moment.astimezone(ZoneInfo(tz)) if tz else moment).date()


def days_between(later: date, earlier: date) -> int:
    return (later - earlier).days


def stage_entered(props: dict[str, Any]) -> Any:
    """The "entered current stage" timestamp; HubSpot has exposed it under two names."""
    return props.get("hs_date_entered_current_stage") or props.get("hs_v2_date_entered_current_stage")


def as_data(text: Any, max_chars: int = 2000) -> dict[str, Any] | None:
    """Wrap CRM free text so it reads as data, never as an instruction to the model."""
    if text is None or text == "":
        return None
    value = str(text)
    if len(value) > max_chars:
        return {"untrusted_text": value[:max_chars], "truncated": True}
    return {"untrusted_text": value}


def concise_or_detailed(
    response_format: str, concise: dict[str, Any], detailed: dict[str, Any] | None = None
) -> dict[str, Any]:
    """``concise`` always; ``concise`` plus ``detailed`` when the caller asked for it."""
    if response_format not in ("concise", "detailed"):
        raise ToolError("response_format must be 'concise' or 'detailed'")
    return {**concise, **(detailed or {})} if response_format == "detailed" else dict(concise)


# --- links -----------------------------------------------------------------


def object_type_id(object_type: str) -> str:
    """``deals`` -> ``0-3``; ids pass through."""
    return OBJECT_TYPE_IDS.get(object_type, object_type)


def record_url(portal_id: str, object_type_id_or_name: str, record_id: Any, region: str | None = None) -> str:
    """Link to a record in the HubSpot UI.

    The host follows the *app* region (``get_region()``), because the repo does not
    carry a portal's own hublet; an EU portal reached through a US-region app gets
    a US link until the region is configured.
    """
    if region is None:
        from hubspot_mcp.app_credentials import get_region

        region = get_region()
    host = "app-eu1.hubspot.com" if region == "eu" else "app.hubspot.com"
    return f"https://{host}/contacts/{portal_id}/record/{object_type_id(object_type_id_or_name)}/{record_id}"


# --- pipelines and stages --------------------------------------------------


@dataclass(frozen=True)
class Stage:
    id: str
    label: str
    pipeline_id: str
    pipeline_label: str
    position: int
    total: int
    probability: float | None
    is_closed: bool
    is_won: bool

    @property
    def position_text(self) -> str:
        return f"Stage {self.position} of {self.total}"


@dataclass
class PipelineIndex:
    stages: dict[str, Stage] = field(default_factory=dict)

    def stage(self, stage_id: Any) -> Stage | None:
        return self.stages.get(str(stage_id)) if stage_id is not None else None

    def label(self, stage_id: Any) -> str | None:
        stage = self.stage(stage_id)
        return stage.label if stage else None

    def stage_position(self, stage_id: Any) -> str | None:
        """``"Stage N of M"``: N is the stage's place on the pipeline board, closed stages included in M."""
        stage = self.stage(stage_id)
        return stage.position_text if stage else None


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def parse_pipelines(body: dict[str, Any]) -> PipelineIndex:
    index = PipelineIndex()
    for pipeline in body.get("results") or []:
        raw_stages = sorted(
            pipeline.get("stages") or [],
            key=lambda s: (s.get("displayOrder") if s.get("displayOrder") is not None else 0, str(s.get("id"))),
        )
        for position, raw in enumerate(raw_stages, start=1):
            meta = raw.get("metadata") or {}
            probability = parse_float(meta.get("probability"))
            is_closed = _truthy(meta.get("isClosed"))
            stage = Stage(
                id=str(raw.get("id")),
                label=str(raw.get("label") or raw.get("id")),
                pipeline_id=str(pipeline.get("id")),
                pipeline_label=str(pipeline.get("label") or pipeline.get("id")),
                position=position,
                total=len(raw_stages),
                probability=probability,
                is_closed=is_closed,
                is_won=is_closed and probability is not None and probability >= 1.0,
            )
            index.stages[stage.id] = stage
    return index


_cache: dict[tuple[str, str], tuple[float, Any]] = {}


def _now() -> float:
    return time.monotonic()


def clear_caches() -> None:
    """Drop the pipeline and owner caches (tests, and after a pipeline edit)."""
    _cache.clear()


def _cached(key: tuple[str, str]) -> Any | None:
    hit = _cache.get(key)
    if hit and _now() < hit[0]:
        return hit[1]
    return None


async def load_pipelines(lf: dict[str, Any], object_type: str = "deals") -> PipelineIndex:
    """Stage lookup for a portal, cached ``CACHE_TTL_SECONDS``."""
    key = (lf["portal_id"], f"pipelines:{object_type}")
    cached = _cached(key)
    if cached is not None:
        return cached
    index = parse_pipelines(await read_get(lf, f"/crm/v3/pipelines/{object_type}"))
    _cache[key] = (_now() + CACHE_TTL_SECONDS, index)
    return index


def stage_probability(stage: Stage | None, rules: SalesRules) -> float | None:
    """Rules override, else HubSpot's stage metadata."""
    if stage is None:
        return None
    override = rules.probability_override(stage.label)
    return override if override is not None else stage.probability


def _fraction(value: Any) -> float | None:
    p = parse_float(value)
    if p is None:
        return None
    # A fraction above 1 cannot be one; HubSpot portals have returned 0-100 here.
    return p / 100 if p > 1 else p


def deal_probability(props: dict[str, Any], stage: Stage | None, rules: SalesRules) -> float | None:
    """Rules override > hs_forecast_probability > hs_deal_stage_probability > stage metadata."""
    if stage is not None:
        override = rules.probability_override(stage.label)
        if override is not None:
            return override
    for name in ("hs_forecast_probability", "hs_deal_stage_probability"):
        p = _fraction(props.get(name))
        if p is not None:
            return p
    return stage.probability if stage is not None else None


# --- owners ----------------------------------------------------------------


@dataclass(frozen=True)
class Owner:
    id: str
    email: str | None
    first_name: str | None
    last_name: str | None
    user_id: str | None = None

    @property
    def name(self) -> str:
        full = " ".join(p for p in (self.first_name, self.last_name) if p)
        return full or self.email or self.id

    def describe(self) -> str:
        return f"{self.name} ({self.email or 'no email'}, id {self.id})"


@dataclass
class OwnerIndex:
    owners: dict[str, Owner] = field(default_factory=dict)

    def get(self, owner_id: Any) -> Owner | None:
        return self.owners.get(str(owner_id)) if owner_id not in (None, "") else None

    def display(self, owner_id: Any) -> str | None:
        if owner_id in (None, ""):
            return None
        owner = self.get(owner_id)
        return owner.name if owner else str(owner_id)

    def find(self, arg: str) -> list[Owner]:
        """Owners matching ``arg``: id, then exact email, then exact name, then a name or email fragment."""
        needle = arg.strip().casefold()
        everyone = list(self.owners.values())
        for matches in (
            [o for o in everyone if needle in (o.id, (o.user_id or "").casefold()) and needle],
            [o for o in everyone if (o.email or "").casefold() == needle],
            [o for o in everyone if o.name.casefold() == needle],
            [o for o in everyone if needle in o.name.casefold() or needle in (o.email or "").casefold()],
        ):
            if matches:
                return matches
        return []


async def load_owners(lf: dict[str, Any]) -> OwnerIndex:
    """Owner lookup for a portal, cached ``CACHE_TTL_SECONDS``."""
    key = (lf["portal_id"], "owners")
    cached = _cached(key)
    if cached is not None:
        return cached
    index = OwnerIndex()
    after: str | None = None
    for _ in range(50):
        path = "/crm/v3/owners?limit=500" + (f"&after={after}" if after else "")
        body = await read_get(lf, path)
        for raw in body.get("results") or []:
            if raw.get("archived"):
                continue
            owner = Owner(
                id=str(raw.get("id")),
                email=raw.get("email"),
                first_name=raw.get("firstName"),
                last_name=raw.get("lastName"),
                user_id=str(raw["userId"]) if raw.get("userId") is not None else None,
            )
            index.owners[owner.id] = owner
        after = _next_after(body)
        if after is None:
            break
    _cache[key] = (_now() + CACHE_TTL_SECONDS, index)
    return index


_SELF_WORDS = {"me", "my", "mine", "myself", "self"}


async def resolve_owner(lf: dict[str, Any], rules: SalesRules, owner_arg: str | None = None) -> str | None:
    """Owner id for an id, email or name; ``None`` means no owner filter.

    ``me``/``my`` and an omitted argument both mean ``rules.default_owner``. An
    ambiguous or unknown owner raises ``ToolError`` listing the candidates, so the
    model asks the user instead of picking one.
    """
    arg = (owner_arg or "").strip()
    if arg.casefold() in _SELF_WORDS:
        if not rules.default_owner:
            raise ToolError("No default owner is set in the sales rules; pass the owner's email or name.")
        arg = rules.default_owner
    if not arg:
        arg = rules.default_owner or ""
    if not arg:
        return None
    index = await load_owners(lf)
    matches = index.find(arg)
    if len(matches) == 1:
        return matches[0].id
    if not matches:
        raise ToolError(f"No HubSpot owner matches {arg!r}. Ask the user for the owner's email.")
    listed = "; ".join(o.describe() for o in matches[:5])
    raise ToolError(f"{len(matches)} owners match {arg!r}: {listed}. Ask the user which one.")


# --- deal lines ------------------------------------------------------------


@dataclass
class DealContext:
    """Everything :func:`deal_line` needs, fetched once per tool call."""

    portal_id: str
    region: str
    rules: SalesRules
    pipelines: PipelineIndex
    owners: OwnerIndex


async def deal_context(lf: dict[str, Any], rules: SalesRules) -> DealContext:
    from hubspot_mcp.app_credentials import get_region

    return DealContext(
        portal_id=lf["portal_id"],
        region=get_region(),
        rules=rules,
        pipelines=await load_pipelines(lf, "deals"),
        owners=await load_owners(lf),
    )


def amount_home(props: dict[str, Any], rules: SalesRules) -> float | None:
    """Amount in the home currency, never converted by us.

    HubSpot's ``amount_in_home_currency`` when present. Otherwise the plain amount,
    but only when the deal has no currency or its currency is the home currency;
    a foreign-currency deal with no home amount returns ``None`` (no invented FX).
    """
    home = parse_float(props.get("amount_in_home_currency"))
    if home is not None:
        return home
    amount = parse_float(props.get("amount"))
    currency = (props.get("deal_currency_code") or "").upper()
    if amount is not None and (not currency or (rules.home_currency and currency == rules.home_currency.upper())):
        return amount
    return None


def deal_line(
    deal: dict[str, Any],
    dctx: DealContext,
    *,
    reasons: list[dict[str, Any]] | None = None,
    response_format: str = "concise",
) -> dict[str, Any]:
    """The compact per-deal dict every job returns. ``deal`` is a raw HubSpot record."""
    props = deal.get("properties") or {}
    deal_id = str(deal.get("id") or props.get("hs_object_id") or "")
    stage = dctx.pipelines.stage(props.get("dealstage"))
    tz = dctx.rules.timezone
    close = to_date(props.get("closedate"))
    concise: dict[str, Any] = {
        "id": deal_id,
        "name": props.get("dealname"),
        "stage": stage.label if stage else props.get("dealstage"),
        "stage_position": stage.position_text if stage else None,
        "pipeline": stage.pipeline_label if stage else props.get("pipeline"),
        "amount": parse_float(props.get("amount")),
        "currency": props.get("deal_currency_code") or None,
        "amount_home": amount_home(props, dctx.rules),
        "close_date": close.isoformat() if close else None,
        "owner": dctx.owners.display(props.get("hubspot_owner_id")),
        "forecast_category": normalize_category(props.get("hs_manual_forecast_category")),
        "url": record_url(dctx.portal_id, "deals", deal_id, dctx.region),
    }
    if reasons is not None:
        concise["reasons"] = reasons
    contacted = to_date(props.get("notes_last_contacted"), tz)
    next_activity = to_date(props.get("notes_next_activity_date"), tz)
    entered = to_date(stage_entered(props), tz)
    detailed = {
        "probability": deal_probability(props, stage, dctx.rules),
        "last_contacted": contacted.isoformat() if contacted else None,
        "next_activity_date": next_activity.isoformat() if next_activity else None,
        "next_step": as_data(props.get("hs_next_step")),
        "stage_entered": entered.isoformat() if entered else None,
        "is_closed": stage.is_closed if stage else _truthy(props.get("hs_is_closed")),
        "owner_id": props.get("hubspot_owner_id") or None,
        "stage_id": props.get("dealstage"),
        "pipeline_id": props.get("pipeline"),
    }
    return concise_or_detailed(response_format, concise, detailed)
