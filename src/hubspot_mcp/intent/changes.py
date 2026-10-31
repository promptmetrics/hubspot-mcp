"""The intent surface's write path: propose a changeset, apply it once, undo it.

Every write on ``/mcp/intent`` is a *changeset*: an ordered list of ops (update,
create, associate) that a job tool builds and :func:`propose` stores. Nothing
reaches HubSpot until the user confirms and the model calls
``pronova_apply_changes(confirmation_id, expected_count)``. That is the same
promise the ``/mcp`` write gate makes (preview, then approve), but a changeset
can mix several objects (a meeting, a note, a deal update, new contacts) in one
approval, which the single-tool pending previews cannot express.

Where it is stored, and why ``hubspot_approve_write`` never touches it
---------------------------------------------------------------------
Changesets live in the ordinary pending-preview namespace of the state store
(``store_pending``): it is the only per-portal store that works on both the file
and the Redis backend, with TTL and permissions already handled. The record has
no ``tool_name``, ``agent_name``, ``intent`` or ``proposed_payload``, so
``hubspot_approve_write`` on a ``chg_`` id fails closed at "Pending preview has no
tool name" before any HubSpot call, and ``hubspot_undo_write`` refuses its snapshot
(``undoable: False``). ``kind: "intent_changeset"`` is the marker this module keys on.

State machine: ``pending`` -> ``applying`` -> ``applied`` -> ``undone``. The store
has no compare-and-set, so two concurrent applies from different processes can
both pass the status check; the ``expected_count`` refusal and the per-record
drift check bound the damage. Within one process the id is claimed before the first
await, so a parallel or retried call is refused while the first is still working.

Apply compares every record it is about to update with the value captured at
preview time and skips (reports as ``drifted``) any that someone changed since.

An apply that stops partway (a timeout, a dropped connection, a cancelled request) does
not strand the changeset in ``applying``: it records what each op did (``unconfirmed``
where a write was sent and its outcome is unknown, ``failed`` where nothing was sent),
saves the undo snapshot including every record already created, and ends ``applied`` with
``interrupted`` set. Created ids are also flushed to the snapshot every few creates, so
even a killed process leaves records undo can find. A process that dies leaves
``applying``; ``pronova_undo_changes`` accepts that state when a snapshot exists.
"""
from __future__ import annotations

import asyncio
import os
import re
import secrets
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

import anyio
import httpx
import mcp_types as T
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from hubspot_mcp.errors import HubSpotError
from hubspot_mcp.intent import common
from hubspot_mcp.intent.rules import RulesError, load_rules
from hubspot_mcp.state import get_store
from hubspot_mcp.validation import filter_writable_properties

KIND = "intent_changeset"
VERSION = 1
ID_PREFIX = "chg_"
MAX_OPS = 5000
PREVIEW_ALL_UP_TO = 25
PREVIEW_SAMPLE = 10
PROBLEMS_SHOWN = 50
CREATE_CONCURRENCY = 4
ARCHIVE_BATCH_SIZE = 100
CREATE_CHECKPOINT = 25  # created ids are flushed to the undo snapshot this often during an apply
# Letters only: the audit log redacts digit runs that look like phone numbers, and a hex id
# such as chg_2888815d88c0 would be rewritten there, breaking the apply/undo trail. For the
# same reason the id travels in the audit summary, not in the action name: a long
# letters-and-underscores action string is hashed as "name-like" by the redactor.
_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz"
_ID_RE = re.compile(r"chg_[a-z]{12}")
_ASSOC_CATEGORIES = ("HUBSPOT_DEFINED", "USER_DEFINED", "INTEGRATOR_DEFINED")

_inflight: set[str] = set()


# --- op normalization ------------------------------------------------------


def _fail(message: str) -> ToolError:
    return ToolError(message)


def _scalar(value: Any, where: str) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return value
    raise _fail(f"{where}: property values must be text, numbers or booleans, got {type(value).__name__}.")


def _properties(raw: Any, where: str, *, required: bool) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise _fail(f"{where}: properties must be an object.")
    if required and not raw:
        raise _fail(f"{where}: properties must not be empty.")
    return {str(k): _scalar(v, f"{where} property {k!r}") for k, v in raw.items()}


def _object_type(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_\-]+", raw):
        raise _fail(f"{where}: object_type must be a HubSpot object name such as 'deals'.")
    return raw


def _endpoint(raw: Any, where: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise _fail(f"{where}: expected {{object_type, id}} or {{ref}}.")
    if raw.get("ref"):
        return {"ref": str(raw["ref"])}
    if raw.get("id") in (None, ""):
        raise _fail(f"{where}: needs an id, or a ref to a create op in the same changeset.")
    return {"object_type": _object_type(raw.get("object_type"), where), "id": str(raw["id"])}


def _assoc_type(raw: Any, where: str) -> Any:
    if raw in (None, "", "default"):
        return "default"
    if isinstance(raw, bool):
        raise _fail(f"{where}: invalid association type.")
    if isinstance(raw, int):
        return {"category": "HUBSPOT_DEFINED", "id": raw}
    if isinstance(raw, dict) and isinstance(raw.get("id"), int):
        category = str(raw.get("category", "HUBSPOT_DEFINED")).upper()
        if category not in _ASSOC_CATEGORIES:
            raise _fail(f"{where}: association category must be one of {list(_ASSOC_CATEGORIES)}.")
        return {"category": category, "id": raw["id"]}
    raise _fail(f"{where}: association type must be 'default', an integer type id, or {{category, id}}.")


def _label(endpoint: dict[str, str]) -> str:
    return f"{{{endpoint['ref']}}}" if "ref" in endpoint else f"{endpoint['object_type']} {endpoint['id']}"


def normalize_ops(ops: Sequence[dict[str, Any]], portal_id: str | None = None) -> list[dict[str, Any]]:
    """Validate ops and return them in canonical form. Raises ``ToolError`` naming the bad op.

    Canonical shapes (see the module docstring for what each does)::

        {"op": "update", "object_type", "id", "properties", "summary"}
        {"op": "create", "object_type", "ref"?, "depends_on"?, "properties", "associations": [{"to", "type"}], "summary"}
        {"op": "associate", "from", "to", "type", "summary"}

    ``from``/``to`` are ``{object_type, id}`` or ``{ref}`` (a ``ref`` names a create
    op in the same changeset). ``depends_on`` is the 0-based index of an earlier update op:
    apply creates the record only if that update was written. ``type`` is ``"default"``, an integer HubSpot-defined
    type id, or ``{category, id}``.
    """
    if not isinstance(ops, (list, tuple)) or not ops:
        raise _fail("A changeset needs at least one op.")
    if len(ops) > MAX_OPS:
        raise _fail(f"A changeset holds at most {MAX_OPS} ops; split it.")
    out: list[dict[str, Any]] = []
    refs: set[str] = set()
    updated: set[tuple[str, str]] = set()
    for i, raw in enumerate(ops):
        where = f"op {i + 1}"
        if not isinstance(raw, dict):
            raise _fail(f"{where}: must be an object.")
        kind = raw.get("op")
        summary = raw.get("summary")
        if kind == "update":
            object_type = _object_type(raw.get("object_type"), where)
            if raw.get("id") in (None, ""):
                raise _fail(f"{where}: update needs the record id.")
            record_id = str(raw["id"])
            if (object_type, record_id) in updated:
                raise _fail(f"{where}: {object_type} {record_id} is updated twice; merge the properties into one op.")
            updated.add((object_type, record_id))
            props = _properties(raw.get("properties"), where, required=True)
            op = {
                "op": "update",
                "object_type": object_type,
                "id": record_id,
                "properties": props,
                "summary": summary or f"Update {object_type} {record_id}: {', '.join(props)}",
            }
        elif kind == "create":
            object_type = _object_type(raw.get("object_type"), where)
            props = _properties(raw.get("properties"), where, required=True)
            ref = raw.get("ref")
            if ref is not None:
                ref = str(ref)
                if ref in refs:
                    raise _fail(f"{where}: ref {ref!r} is used twice.")
                refs.add(ref)
            assocs = [
                {
                    "to": _endpoint(a.get("to") if "to" in a else a, f"{where} association {j + 1}"),
                    "type": _assoc_type(a.get("type"), f"{where} association {j + 1}"),
                }
                for j, a in enumerate(raw.get("associations") or [])
            ]
            op = {
                "op": "create",
                "object_type": object_type,
                "properties": props,
                "associations": assocs,
                "summary": summary or f"Create {object_type}" + (f" ({', '.join(props)})" if props else ""),
            }
            if ref is not None:
                op["ref"] = ref
            depends_on = raw.get("depends_on")
            if depends_on is not None:
                if isinstance(depends_on, bool) or not isinstance(depends_on, int) or not 0 <= depends_on < i:
                    raise _fail(f"{where}: depends_on must be the index of an earlier update op.")
                if out[depends_on]["op"] != "update":
                    raise _fail(f"{where}: depends_on points at op {depends_on + 1}, which is not an update.")
                op["depends_on"] = depends_on
        elif kind == "associate":
            src = _endpoint(raw.get("from"), f"{where} from")
            dst = _endpoint(raw.get("to"), f"{where} to")
            op = {
                "op": "associate",
                "from": src,
                "to": dst,
                "type": _assoc_type(raw.get("type"), where),
                "summary": summary or f"Associate {_label(src)} with {_label(dst)}",
            }
        else:
            raise _fail(f"{where}: op must be 'update', 'create' or 'associate', got {kind!r}.")
        out.append(op)
    for i, op in enumerate(out):
        targets = [op["from"], op["to"]] if op["op"] == "associate" else [a["to"] for a in op.get("associations", [])]
        for endpoint in targets:
            if "ref" in endpoint and endpoint["ref"] not in refs:
                raise _fail(f"op {i + 1}: ref {endpoint['ref']!r} matches no create op in this changeset.")
        if op["op"] == "associate" and "ref" in op["from"] and "ref" in op["to"] and op["from"] == op["to"]:
            raise _fail(f"op {i + 1}: cannot associate a record with itself.")
    if portal_id is not None:
        for i, op in enumerate(out):
            if op["op"] in ("update", "create"):
                _, stripped = filter_writable_properties(op["object_type"], op["properties"], portal_id)
                if stripped:
                    raise _fail(f"op {i + 1}: {', '.join(sorted(stripped))} is read-only in HubSpot and cannot be written.")
    return out


# --- value comparison ------------------------------------------------------

_NUMBER = re.compile(r"-?(0|[1-9]\d*)(\.\d+)?")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}(T.*)?")


def _norm(value: Any) -> str:
    """Canonical text for comparing a HubSpot value with a captured one.

    HubSpot echoes ``1000`` as ``"1000.0"`` and a date as epoch-ms or ISO depending
    on the read path; both must compare equal or every record looks drifted.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    if _ISO.fullmatch(text):
        moment = common.to_datetime(text)
        if moment is not None:
            text = str(int(moment.timestamp() * 1000))
    if _NUMBER.fullmatch(text):
        try:
            return format(Decimal(text).normalize(), "f")
        except InvalidOperation:
            return text
    return text


def same_value(a: Any, b: Any) -> bool:
    return _norm(a) == _norm(b)


# --- persistence -----------------------------------------------------------


def _key(object_type: str, record_id: str) -> str:
    return f"{object_type}:{record_id}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _load(portal_id: str, confirmation_id: str) -> dict[str, Any]:
    if not _ID_RE.fullmatch(confirmation_id or ""):
        raise _fail(f"{confirmation_id!r} is not a changeset id. Changeset ids look like chg_abcdefghjkmn.")
    record = await get_store().load_pending(portal_id, confirmation_id)
    if record is None:
        raise _fail(
            f"No changeset {confirmation_id}. It expired (previews last 24 hours), was already undone, "
            "or never existed; build the changes again if you still need them."
        )
    if record.get("kind") != KIND:
        raise _fail(f"{confirmation_id} is not an intent changeset.")
    return record


async def _audit(portal_id: str, action: str, summary: dict[str, Any]) -> None:
    # The write already happened; a failing audit log must not turn it into an error.
    try:
        await get_store().log_write(portal_id, action=action, agent="intent", result_summary=summary)
    except Exception:  # noqa: BLE001
        pass


# --- propose ---------------------------------------------------------------


async def capture_original_values(
    lf: dict[str, Any], ops: Sequence[dict[str, Any]], have: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    """Current values of the properties each update op will change, read from HubSpot.

    Ops already covered by ``have`` are not re-read. A missing record raises
    ``ToolError``: proposing an update to a record that does not exist is a mistake
    worth surfacing at preview time.
    """
    originals: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (have or {}).items()}
    wanted: dict[str, list[dict[str, Any]]] = {}
    for op in ops:
        if op["op"] == "update" and _key(op["object_type"], op["id"]) not in originals:
            wanted.setdefault(op["object_type"], []).append(op)
    for object_type, group in wanted.items():
        props = sorted({p for op in group for p in op["properties"]})
        records = await common.batch_read(lf, object_type, [op["id"] for op in group], props)
        by_id = {str(r.get("id")): r.get("properties") or {} for r in records}
        for op in group:
            current = by_id.get(op["id"])
            if current is None:
                raise _fail(f"{object_type} {op['id']} was not found; it may be archived or the id is wrong.")
            originals[_key(object_type, op["id"])] = {p: current.get(p) for p in op["properties"]}
    return originals


def _by_kind(ops: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter(
        f"{op['op']} {op.get('object_type', 'association')}" for op in ops
    )
    return dict(sorted(counts.items()))


async def propose(
    lf: dict[str, Any],
    *,
    title: str,
    ops: Sequence[dict[str, Any]],
    original_values: dict[str, dict[str, Any]] | None = None,
    rules_hash: str | None = None,
) -> dict[str, Any]:
    """Validate, store and return the preview for a changeset. Writes nothing to HubSpot.

    ``original_values`` maps ``"<object_type>:<id>"`` to the properties' current
    values; any update op without an entry is read from HubSpot here. The preview::

        {"status": "preview", "confirmation_id", "title", "change_count", "by_kind",
         "changes": [summary, ...], "sample_only"?, "omitted"?, "next"}

    ``changes`` lists every summary up to 25 ops; beyond that it holds the first
    10 and ``sample_only`` is true.
    """
    if not isinstance(title, str) or not title.strip():
        raise _fail("A changeset needs a title.")
    portal_id = lf["portal_id"]
    canonical = normalize_ops(ops, portal_id)
    originals = await capture_original_values(lf, canonical, original_values)
    confirmation_id = ID_PREFIX + "".join(secrets.choice(_ID_ALPHABET) for _ in range(12))
    record = {
        "kind": KIND,
        "version": VERSION,
        "status": "pending",
        "title": title.strip()[:200],
        "ops": canonical,
        "original_values": originals,
        "change_count": len(canonical),
        "rules_hash": rules_hash,
        "created_at": _now(),
    }
    await get_store().store_pending(portal_id, confirmation_id, record)
    summaries = [op["summary"] for op in canonical]
    many = len(summaries) > PREVIEW_ALL_UP_TO
    preview: dict[str, Any] = {
        "status": "preview",
        "confirmation_id": confirmation_id,
        "title": record["title"],
        "change_count": len(canonical),
        "by_kind": _by_kind(canonical),
        "changes": summaries[:PREVIEW_SAMPLE] if many else summaries,
        "next": (
            "Show this to the user. After they confirm, call "
            f"pronova_apply_changes(confirmation_id='{confirmation_id}', expected_count={len(canonical)}). "
            "Nothing has been written to HubSpot."
        ),
    }
    if many:
        preview["sample_only"] = True
        preview["omitted"] = len(summaries) - PREVIEW_SAMPLE
    return preview


async def propose_from_ops(
    lf: dict[str, Any], *, title: str, changes: Sequence[dict[str, Any]], rules_hash: str | None = None
) -> dict[str, Any]:
    """:func:`propose` for the looser shape ``pronova_propose_changes`` accepts.

    Each change is ``{op, object_type, id?, properties?, associate_to?, ref?, summary?}``.
    ``associate_to`` is a list of ``{object_type, id | ref, type?}``: on ``create`` they
    become the new record's associations; on ``associate`` they are the targets, one
    association op each, from ``object_type``/``id``. Original values are read from HubSpot.
    """
    ops: list[dict[str, Any]] = []
    for i, change in enumerate(changes):
        if not isinstance(change, dict):
            raise _fail(f"change {i + 1}: must be an object.")
        kind = change.get("op")
        targets = change.get("associate_to") or []
        if kind == "associate":
            if not targets:
                raise _fail(f"change {i + 1}: associate needs associate_to.")
            for target in targets:
                ops.append(
                    {
                        "op": "associate",
                        "from": {"object_type": change.get("object_type"), "id": change.get("id")},
                        "to": target,
                        "type": target.get("type") if isinstance(target, dict) else None,
                    }
                )
        else:
            op = {k: v for k, v in change.items() if k not in ("associate_to",)}
            if kind == "create" and targets:
                op["associations"] = targets
            ops.append(op)
    return await propose(lf, title=title, ops=ops, rules_hash=rules_hash)


# --- apply -----------------------------------------------------------------

# A timeout or a dropped connection reaches us as an httpx error, not a HubSpotError (the client
# does not wrap them). Every write path catches both, so a transport failure is reported per op
# as "unconfirmed" instead of escaping with the changeset stuck in "applying".
_TRANSPORT_ERRORS = (HubSpotError, httpx.HTTPError)


def _unsure(exc: Exception) -> bool:
    """A 5xx or a transport-level failure may have been applied; a 4xx was not."""
    if isinstance(exc, HubSpotError):
        return exc.status_code is None or exc.status_code >= 500
    return True


def _describe(exc: BaseException) -> str:
    if isinstance(exc, HubSpotError):
        return common.describe_error(exc)
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text}"[:300] if text else type(exc).__name__


def _error_ids(errors: Sequence[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for err in errors or []:
        ctx = err.get("context") or {}
        ids = ctx.get("ids") or ctx.get("id") or []
        for i in ids if isinstance(ids, list) else [ids]:
            out[str(i)] = str(err.get("message") or "rejected")
    return out


async def _write_batch(
    lf: dict[str, Any], path: str, inputs: list[dict[str, Any]], ids: list[str]
) -> dict[str, tuple[str, str]]:
    """POST one batch write; return ``{id: (status, detail)}`` with status ok, failed or unconfirmed."""
    try:
        body = await common.post_raw(lf, path, {"inputs": inputs}, attempts=common.WRITE_ATTEMPTS)
    except _TRANSPORT_ERRORS as exc:
        status = "unconfirmed" if _unsure(exc) else "failed"
        return {i: (status, _describe(exc)) for i in ids}
    confirmed = {str(r.get("id")) for r in body.get("results") or []}
    rejected = _error_ids(body.get("errors") or [])
    out: dict[str, tuple[str, str]] = {}
    for i in ids:
        if i in rejected:
            out[i] = ("failed", rejected[i])
        elif i in confirmed:
            out[i] = ("ok", "")
        else:
            out[i] = ("unconfirmed", "HubSpot's response did not list this record")
    return out


def _drift_detail(props: dict[str, Any], original: dict[str, Any], current: dict[str, Any]) -> str:
    changed = [p for p in props if p in original and not same_value(current.get(p), original[p])]
    return "; ".join(f"{p}: previewed {original[p]!r}, now {current.get(p)!r}" for p in changed)


async def _classify_updates(
    lf: dict[str, Any], ops: Sequence[dict[str, Any]], originals: dict[str, dict[str, Any]]
) -> dict[int, tuple[str, str]]:
    """Per update op index: ok, unchanged, drifted or failed (record gone), from a fresh read."""
    verdicts: dict[int, tuple[str, str]] = {}
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for i, op in enumerate(ops):
        if op["op"] == "update":
            groups.setdefault(op["object_type"], []).append((i, op))
    for object_type, group in groups.items():
        props = sorted({p for _, op in group for p in op["properties"]})
        records = await common.batch_read(lf, object_type, [op["id"] for _, op in group], props)
        current_by_id = {str(r.get("id")): r.get("properties") or {} for r in records}
        for i, op in group:
            current = current_by_id.get(op["id"])
            if current is None:
                verdicts[i] = ("failed", "record not found; it may have been archived since the preview")
                continue
            original = originals.get(_key(object_type, op["id"]), {})
            if all(same_value(current.get(p), v) for p, v in op["properties"].items()):
                verdicts[i] = ("unchanged", "already has the proposed values")
            elif all(same_value(current.get(p), original[p]) for p in op["properties"] if p in original):
                verdicts[i] = ("ok", "")
            else:
                verdicts[i] = ("drifted", _drift_detail(op["properties"], original, current))
    return verdicts


@dataclass
class _Run:
    """What one apply has done so far, kept where an interrupted apply can still read it.

    ``results`` holds one entry per op and is updated as each write is answered, ``meta`` is
    the undo snapshot's metadata (the created records and new associations), and ``attempted``
    and ``recorded`` say which ops had a write sent and which have an outcome. After a crash
    or a cancellation these tell "sent, outcome unknown" (unconfirmed) from "never sent".
    """

    lf: dict[str, Any]
    confirmation_id: str
    ops: list[dict[str, Any]]
    originals: dict[str, dict[str, Any]]
    results: list[dict[str, Any]]
    meta: dict[str, Any]
    pending_updates: set[int] = field(default_factory=set)
    attempted: set[int] = field(default_factory=set)
    recorded: set[int] = field(default_factory=set)
    created_ids: dict[str, tuple[str, str]] = field(default_factory=dict)  # ref -> (object_type, id)
    pairs: list[dict[str, Any]] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def created(self) -> list[dict[str, str]]:
        return self.meta["created"]

    def snapshot_originals(self) -> dict[str, Any]:
        return {
            _key(op["object_type"], op["id"]): {
                p: self.originals.get(_key(op["object_type"], op["id"]), {}).get(p) for p in op["properties"]
            }
            for i, op in enumerate(self.ops)
            if op["op"] == "update" and self.results[i]["status"] in ("ok", "unconfirmed")
        }

    async def save_snapshot(self) -> None:
        await get_store().save_undo_snapshot(
            self.lf["portal_id"], self.confirmation_id, self.snapshot_originals(), self.meta
        )

    async def checkpoint_created(self) -> None:
        """Persist the created ids so far; a crash after this still leaves undo able to archive them."""
        async with self.lock:
            await get_store().update_undo_snapshot(
                self.lf["portal_id"], self.confirmation_id, metadata={"created": list(self.created)}
            )


async def _create_records(run: _Run, indexed: Sequence[tuple[int, dict[str, Any]]]) -> None:
    """Create each record with its own POST, so a returned id maps to exactly one op.

    Batch create returns results without a guaranteed order and without echoing any
    key of ours, so ids could not be tied back to ops (refs, undo). One POST per record
    costs more calls but is unambiguous; the client's rate window paces them.

    Each id is recorded in ``run`` the moment its POST returns, and flushed to the undo
    snapshot every :data:`CREATE_CHECKPOINT` creates, so an apply cut short midway leaves
    no record that undo cannot find.
    """
    gate = asyncio.Semaphore(CREATE_CONCURRENCY)

    async def one(i: int, op: dict[str, Any]) -> None:
        entry = run.results[i]
        async with gate:
            run.attempted.add(i)
            try:
                body = await common.post_raw(
                    run.lf,
                    f"/crm/v3/objects/{op['object_type']}",
                    {"properties": op["properties"]},
                    attempts=common.WRITE_ATTEMPTS,
                )
            except _TRANSPORT_ERRORS as exc:
                entry["status"] = "unconfirmed" if _unsure(exc) else "failed"
                entry["detail"] = _describe(exc)
                run.recorded.add(i)
                return
            record_id = body.get("id")
            if record_id in (None, ""):
                entry["status"], entry["detail"] = "unconfirmed", "HubSpot's response carried no id"
                run.recorded.add(i)
                return
            entry["status"], entry["detail"], entry["id"] = "ok", "", str(record_id)
            run.recorded.add(i)
            run.created.append({"object_type": op["object_type"], "id": str(record_id)})
            if op.get("ref"):
                run.created_ids[op["ref"]] = (op["object_type"], str(record_id))
            if len(run.created) % CREATE_CHECKPOINT == 0:
                await run.checkpoint_created()

    outcomes = await asyncio.gather(*(one(i, op) for i, op in indexed), return_exceptions=True)
    for (i, _), outcome in zip(indexed, outcomes, strict=True):
        if isinstance(outcome, BaseException) and i not in run.recorded:
            entry = run.results[i]
            entry["status"], entry["detail"] = "unconfirmed", _describe(outcome)
            run.recorded.add(i)


def _spec_key(spec: Any) -> str:
    return "default" if spec == "default" else f"{spec['category']}:{spec['id']}"


async def _associate(
    lf: dict[str, Any], pairs: list[dict[str, Any]], batch_size: int
) -> None:
    """Create each pair's association; sets ``status``/``detail`` on every pair in place."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for pair in pairs:
        if pair["status"] == "pending":
            groups.setdefault((pair["from_type"], pair["to_type"], _spec_key(pair["type"])), []).append(pair)
    for (from_type, to_type, _), group in groups.items():
        existing_from = [p["from_id"] for p in group if p["both_existing"]]
        already: dict[str, list[str]] = {}
        if existing_from:
            try:
                already = await common.batch_read_associations(lf, from_type, to_type, existing_from)
            except (ToolError, httpx.HTTPError):
                already = {}
        todo: list[dict[str, Any]] = []
        for pair in group:
            if pair["both_existing"] and pair["to_id"] in already.get(pair["from_id"], []):
                pair["pre_existing"] = True
                if pair["type"] == "default":
                    pair["status"], pair["detail"] = "skipped", "already associated"
                    continue
            todo.append(pair)
        for chunk in common.chunked(todo, batch_size):
            await _write_association_chunk(lf, from_type, to_type, chunk)


async def _write_association_chunk(
    lf: dict[str, Any], from_type: str, to_type: str, chunk: list[dict[str, Any]]
) -> None:
    spec = chunk[0]["type"]
    base = f"/crm/v4/associations/{from_type}/{to_type}/batch"
    if spec == "default":
        path = base + "/associate/default"
        inputs: list[dict[str, Any]] = [{"from": {"id": p["from_id"]}, "to": {"id": p["to_id"]}} for p in chunk]
    else:
        path = base + "/create"
        types = [{"associationCategory": spec["category"], "associationTypeId": spec["id"]}]
        inputs = [{"from": {"id": p["from_id"]}, "to": {"id": p["to_id"]}, "types": types} for p in chunk]  # noqa: E501
    try:
        body = await common.post_raw(lf, path, {"inputs": inputs}, attempts=common.WRITE_ATTEMPTS)
    except _TRANSPORT_ERRORS as exc:
        status = "unconfirmed" if _unsure(exc) else "failed"
        for p in chunk:
            p["status"], p["detail"] = status, _describe(exc)
        return
    if not body.get("errors"):
        for p in chunk:
            p["status"], p["detail"] = "ok", ""
        return
    # A partial failure names no pair; read the associations back to see which landed.
    try:
        landed = await common.batch_read_associations(lf, from_type, to_type, [p["from_id"] for p in chunk])
    except (ToolError, httpx.HTTPError):
        landed = None
    message = str(body["errors"][0].get("message") or "rejected")
    for p in chunk:
        if landed is None:
            p["status"], p["detail"] = "unconfirmed", message
        elif p["to_id"] in landed.get(p["from_id"], []):
            p["status"], p["detail"] = "ok", ""
        else:
            p["status"], p["detail"] = "failed", message


def _summarize(record: dict[str, Any]) -> dict[str, Any]:
    results = record["result"]["ops"]
    counts = Counter(r["status"] for r in results)
    problems = [r for r in results if r["status"] not in ("ok", "unchanged", "skipped")]
    shown = problems[:PROBLEMS_SHOWN]
    out: dict[str, Any] = {
        "confirmation_id": record["confirmation_id"],
        "title": record["title"],
        "change_count": record["change_count"],
        "succeeded": counts["ok"],
        "unchanged": counts["unchanged"] + counts["skipped"],
        "failed": counts["failed"] + counts["unconfirmed"] + counts["partial"] + counts["blocked"],
        "unconfirmed": counts["unconfirmed"],
        "partial": counts["partial"],
        "drifted": counts["drifted"],
        "created": record["result"]["created"],
        "problems": shown,
    }
    if len(problems) > len(shown):
        out["problems_omitted"] = len(problems) - len(shown)
    dependents = sum(1 for r in results if r["status"] == "skipped" and r["op"] == "create")
    if dependents:
        out["skipped_dependents"] = dependents
    return out


def _next_note(summary: dict[str, Any]) -> str:
    """What the model should do about each kind of problem; they are not all "build it again"."""
    parts: list[str] = []
    if summary.get("interrupted"):
        parts.append(
            "The apply stopped before it finished (see error). Everything already written is recorded and "
            "can be undone."
        )
    if summary.get("skipped_dependents"):
        parts.append(
            f"{summary['skipped_dependents']} record(s) were not created because the change they belong to "
            "was not written."
        )
    if summary["unconfirmed"]:
        parts.append(
            f"{summary['unconfirmed']} change(s) are unconfirmed: HubSpot did not answer cleanly, so they may "
            "already be written. Check HubSpot for them before building a new changeset; rebuilding can "
            "create duplicates."
        )
    if summary["partial"]:
        parts.append(
            f"{summary['partial']} record(s) were created but a link to them was not made. The record exists: "
            "add only the missing association, do not create it again."
        )
    not_written = summary["failed"] - summary["unconfirmed"] - summary["partial"] + summary["drifted"]
    if not_written > 0:
        parts.append(
            "Failed, blocked and drifted changes were not written; build a new changeset for them if the "
            "user still wants them."
        )
    return " ".join(parts) + (" " if parts else "")


async def apply_changeset(lf: dict[str, Any], confirmation_id: str, expected_count: int) -> dict[str, Any]:
    """Apply a stored changeset once. Reports partial failure instead of raising it."""
    portal_id = lf["portal_id"]
    # Claim the id before the first await. Everything below suspends on I/O, and a second call
    # (a parallel tool call, a retry after a client timeout) must not get past this point while
    # the first is still reading or writing.
    if confirmation_id in _inflight:
        raise _fail(
            f"{confirmation_id} is being applied right now. Do not retry it: wait for that call's result, "
            "or check HubSpot for what landed."
        )
    _inflight.add(confirmation_id)
    try:
        record = await _load(portal_id, confirmation_id)
        status = record.get("status")
        if status == "applied":
            return {"status": "already_applied", **record["summary"],
                    "note": "This changeset was already applied; nothing was written again."}
        if status == "undone":
            raise _fail(f"{confirmation_id} was applied and then undone. Build the changes again to redo them.")
        if status == "applying":
            raise _fail(
                f"{confirmation_id} was being applied and an earlier apply stopped partway. Do not retry it: "
                "check HubSpot for what landed, or call pronova_undo_changes to revert what was recorded."
            )
        if expected_count != record["change_count"]:
            raise _fail(
                f"expected_count {expected_count} does not match the {record['change_count']} changes in "
                f"{confirmation_id}. Show the user the preview again and pass its change_count."
            )
        try:
            rules = load_rules(portal_id)
        except RulesError as exc:
            raise _fail(str(exc)) from exc
        batch_size = rules.bulk_write_batch_size

        ops: list[dict[str, Any]] = record["ops"]
        originals: dict[str, dict[str, Any]] = record.get("original_values") or {}
        # Read before flipping status: a failed read leaves the changeset untouched and retryable.
        verdicts = await _classify_updates(lf, ops, originals)

        record["status"] = "applying"
        record["applying_at"] = _now()
        await get_store().store_pending(portal_id, confirmation_id, record)
        return await _run_apply(lf, confirmation_id, record, ops, originals, verdicts, batch_size)
    finally:
        _inflight.discard(confirmation_id)


async def _release(portal_id: str, confirmation_id: str, record: dict[str, Any]) -> None:
    """Put a changeset that failed before any write back to ``pending`` so it can be applied again."""
    record["status"] = "pending"
    record.pop("applying_at", None)
    try:
        await get_store().store_pending(portal_id, confirmation_id, record)
    except Exception:  # noqa: BLE001
        pass  # the store is what failed; the original error is the one worth raising


async def _run_apply(
    lf: dict[str, Any],
    confirmation_id: str,
    record: dict[str, Any],
    ops: list[dict[str, Any]],
    originals: dict[str, dict[str, Any]],
    verdicts: dict[int, tuple[str, str]],
    batch_size: int,
) -> dict[str, Any]:
    portal_id = lf["portal_id"]
    results: list[dict[str, Any]] = []
    for i, op in enumerate(ops):
        entry: dict[str, Any] = {"index": i, "op": op["op"], "summary": op["summary"], "status": "pending", "detail": ""}
        if op["op"] in ("update", "create"):
            entry["object_type"] = op["object_type"]
        if op["op"] == "update":
            entry["id"] = op["id"]
            entry["status"], entry["detail"] = verdicts[i]
        results.append(entry)

    meta: dict[str, Any] = {
        "kind": KIND,
        "intent_type": KIND,
        "undoable": False,
        "created": [],
        "associated": [],
    }
    run = _Run(lf, confirmation_id, ops, originals, results, meta)
    run.pending_updates = {i for i, r in enumerate(results) if r["op"] == "update" and r["status"] == "ok"}

    writing = False
    try:
        # Snapshot first, so a crash mid-write still leaves a way back for the updates.
        await run.save_snapshot()
        writing = True
        await _execute(run, batch_size)
    except BaseException as exc:  # noqa: BLE001 - re-raised below unless it is an ordinary error after writes began
        # Shielded: a cancelled request must still get to record what it already wrote.
        with anyio.CancelScope(shield=True):
            if not writing:
                await _release(portal_id, confirmation_id, record)
                raise
            report = await _conclude(run, record, interrupted=_describe(exc))
        if not isinstance(exc, Exception):
            raise
        return report
    return await _conclude(run, record, interrupted=None)


async def _execute(run: _Run, batch_size: int) -> None:
    lf, ops, results = run.lf, run.ops, run.results

    # 1. updates, batched per object type
    by_type: dict[str, list[int]] = {}
    for i in sorted(run.pending_updates):
        by_type.setdefault(ops[i]["object_type"], []).append(i)
    for object_type, indexes in by_type.items():
        for chunk in common.chunked(indexes, batch_size):
            run.attempted.update(chunk)
            inputs = [{"id": ops[i]["id"], "properties": ops[i]["properties"]} for i in chunk]
            outcome = await _write_batch(
                lf, f"/crm/v3/objects/{object_type}/batch/update", inputs, [ops[i]["id"] for i in chunk]
            )
            for i in chunk:
                results[i]["status"], results[i]["detail"] = outcome[ops[i]["id"]]
                run.recorded.add(i)

    # 2. creates. One that depends on a deal update is skipped unless that update was written:
    # a "Confirm timing" task for a deal whose date did not move would be wrong, and on a re-run
    # after search lag it would duplicate the first run's tasks.
    create_idx: list[tuple[int, dict[str, Any]]] = []
    for i, op in enumerate(ops):
        if op["op"] != "create":
            continue
        dep = op.get("depends_on")
        if dep is not None and results[dep]["status"] != "ok":
            results[i]["status"] = "skipped"
            results[i]["detail"] = f"not created: the update it belongs to (op {dep + 1}) was {results[dep]['status']}"
            run.recorded.add(i)
            continue
        create_idx.append((i, op))
    await _create_records(run, create_idx)
    await run.checkpoint_created()

    # 3. associations: explicit ops, then those nested in creates
    pairs: list[dict[str, Any]] = []

    def resolve(endpoint: dict[str, str]) -> tuple[str, str, bool] | None:
        if "ref" in endpoint:
            hit = run.created_ids.get(endpoint["ref"])
            return (hit[0], hit[1], False) if hit else None
        return endpoint["object_type"], endpoint["id"], not endpoint.get("new")

    for i, op in enumerate(ops):
        sources: list[tuple[dict[str, str], dict[str, str], Any]] = []
        if op["op"] == "associate":
            sources.append((op["from"], op["to"], op["type"]))
        elif op["op"] == "create":
            if results[i]["status"] == "ok" and results[i].get("id"):
                me = {"object_type": op["object_type"], "id": results[i]["id"], "new": True}
                sources.extend((me, a["to"], a["type"]) for a in op["associations"])
            elif op["associations"] and results[i]["status"] != "skipped":
                results[i]["detail"] = (results[i]["detail"] + "; " if results[i]["detail"] else "") + "associations not attempted"
        for src, dst, spec in sources:
            a, b = resolve(src), resolve(dst)
            pair: dict[str, Any] = {"index": i, "type": spec, "status": "pending", "detail": "", "pre_existing": False}
            if a is None or b is None:
                pair.update(from_type="", to_type="", from_id="", to_id="", both_existing=False,
                            status="blocked", detail="a record it depends on was not created")
            else:
                pair.update(from_type=a[0], from_id=a[1], to_type=b[0], to_id=b[1], both_existing=a[2] and b[2])
            pairs.append(pair)
    run.pairs = pairs
    await _associate(lf, pairs, batch_size)


def _apply_pair_results(run: _Run) -> None:
    """Fold the association pairs' outcomes into the per-op results and the undo metadata."""
    pairs = run.pairs or []
    run.meta["associated"] = [
        {"from": {"object_type": p["from_type"], "id": p["from_id"]}, "to": {"object_type": p["to_type"], "id": p["to_id"]}}
        for p in pairs
        if p["status"] in ("ok", "unconfirmed") and p["both_existing"] and not p["pre_existing"]
    ]
    for p in pairs:
        entry = run.results[p["index"]]
        if entry["op"] == "associate":
            entry["status"], entry["detail"] = p["status"], p["detail"]
        elif p["status"] in ("failed", "unconfirmed", "blocked") and entry["status"] == "ok":
            entry["status"] = "partial"
            entry["detail"] = (entry["detail"] + "; " if entry["detail"] else "") + f"association {p['status']}: {p['detail']}"


def _mark_interrupted(run: _Run, reason: str) -> None:
    """Give every op an honest status after an apply that did not run to the end.

    A write that was sent but has no recorded outcome may have landed: ``unconfirmed``.
    One that was never sent was not written: ``failed``.
    """
    unsent = f"not attempted: the apply stopped ({reason})"
    unknown = f"the apply stopped before HubSpot's answer was recorded ({reason}); it may have been written"
    for i, entry in enumerate(run.results):
        if i in run.recorded:
            continue
        op = run.ops[i]
        if op["op"] == "update" and i in run.pending_updates:
            entry["status"], entry["detail"] = ("unconfirmed", unknown) if i in run.attempted else ("failed", unsent)
        elif op["op"] == "create":
            entry["status"], entry["detail"] = ("unconfirmed", unknown) if i in run.attempted else ("failed", unsent)
        elif op["op"] == "associate" and run.pairs is None:
            entry["status"], entry["detail"] = "failed", unsent
    if run.pairs is None:
        for i, op in enumerate(run.ops):
            if op["op"] == "create" and op["associations"] and run.results[i]["status"] == "ok":
                run.results[i]["status"] = "partial"
                run.results[i]["detail"] = f"associations not attempted: the apply stopped ({reason})"
    else:
        for p in run.pairs:
            if p["status"] == "pending":
                p["status"], p["detail"] = "unconfirmed", unknown


async def _conclude(run: _Run, record: dict[str, Any], interrupted: str | None) -> dict[str, Any]:
    """Persist the outcome (undo snapshot, record, audit) and build the tool's answer."""
    portal_id, confirmation_id = run.lf["portal_id"], run.confirmation_id
    store = get_store()
    if interrupted:
        _mark_interrupted(run, interrupted)
    _apply_pair_results(run)
    await run.save_snapshot()

    record["status"] = "applied"
    record["applied_at"] = _now()
    record["confirmation_id"] = confirmation_id
    record["result"] = {"ops": run.results, "created": len(run.created)}
    record["summary"] = _summarize(record)
    if interrupted:
        record["summary"]["interrupted"] = True
        record["summary"]["error"] = interrupted
    await store.store_pending(portal_id, confirmation_id, record)
    summary = record["summary"]
    await _audit(
        portal_id,
        "apply_changes",
        {"confirmation_id": confirmation_id}
        | {k: summary[k] for k in ("change_count", "succeeded", "unchanged", "failed", "drifted", "created")},
    )
    if interrupted:
        state = "partially_applied"
    elif not (
        summary["succeeded"] or summary["unchanged"] or summary["created"] or summary["unconfirmed"] or summary["partial"]
    ):
        # "failed" means nothing was written and nothing may have been; a record that was created
        # (even if its link failed) or a write with an unknown outcome is a partial apply.
        state = "failed"
    elif summary["failed"] or summary["drifted"]:
        state = "partially_applied"
    else:
        state = "applied"
    return {
        "status": state,
        **summary,
        "next": _next_note(summary) + f"To revert what was written, call pronova_undo_changes('{confirmation_id}') after the user agrees.",
    }


# --- undo ------------------------------------------------------------------


async def undo_changeset(lf: dict[str, Any], confirmation_id: str) -> dict[str, Any]:
    """Revert an applied changeset: restore updated values, archive created records, drop new associations."""
    portal_id = lf["portal_id"]
    store = get_store()
    record: dict[str, Any] | None
    try:
        record = await _load(portal_id, confirmation_id)
    except ToolError:
        # The pending record expires after 24 hours; the snapshot is kept for 7 days.
        # Undo must still work in that window, so the snapshot alone is enough.
        if not _ID_RE.fullmatch(confirmation_id or "") or await store.load_undo_snapshot(portal_id, confirmation_id) is None:
            raise
        record = None
    if record is not None:
        status = record.get("status")
        if status == "undone":
            return {"status": "already_undone", "confirmation_id": confirmation_id,
                    "note": "This changeset was already undone; nothing was changed."}
        if confirmation_id in _inflight:
            raise _fail(f"{confirmation_id} is being applied right now; wait for it to finish before undoing it.")
        # "applying" with no apply running means an earlier one died partway; its snapshot is all there is.
        if status not in ("applied", "applying"):
            raise _fail(f"{confirmation_id} is {status}, not applied, so there is nothing to undo.")
    snap = await store.load_undo_snapshot(portal_id, confirmation_id)
    if snap is None or (snap.get("metadata") or {}).get("kind") != KIND:
        raise _fail(f"The undo snapshot for {confirmation_id} is gone (they are kept 7 days). Nothing was changed.")
    meta = snap.get("metadata") or {}
    failures: list[dict[str, str]] = []

    removed = 0
    pair_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for pair in meta.get("associated") or []:
        pair_groups.setdefault((pair["from"]["object_type"], pair["to"]["object_type"]), []).append(pair)
    for (from_type, to_type), group in pair_groups.items():
        for chunk in common.chunked(group, common.ASSOCIATION_BATCH_SIZE):
            inputs = [{"from": {"id": p["from"]["id"]}, "to": [{"id": p["to"]["id"]}]} for p in chunk]
            try:
                await common.post_raw(
                    lf, f"/crm/v4/associations/{from_type}/{to_type}/batch/archive", {"inputs": inputs},
                    attempts=common.WRITE_ATTEMPTS,
                )
                removed += len(chunk)
            except _TRANSPORT_ERRORS as exc:
                failures.append({"what": f"remove {len(chunk)} {from_type}-{to_type} associations", "error": _describe(exc)})

    archived = 0
    created_by_type: dict[str, list[str]] = {}
    for item in meta.get("created") or []:
        created_by_type.setdefault(item["object_type"], []).append(item["id"])
    for object_type, created_ids in created_by_type.items():
        for chunk in common.chunked(created_ids, ARCHIVE_BATCH_SIZE):
            try:
                await common.post_raw(
                    lf, f"/crm/v3/objects/{object_type}/batch/archive", {"inputs": [{"id": i} for i in chunk]},
                    attempts=common.WRITE_ATTEMPTS,
                )
                archived += len(chunk)
            except HubSpotError as exc:
                if exc.status_code != 404:
                    failures.append({"what": f"archive {len(chunk)} {object_type}", "error": common.describe_error(exc)})
                    continue
                # 404: something in the chunk is already gone. Archive one by one; a 404 there means done.
                for record_id in chunk:
                    try:
                        await common.delete_raw(lf, f"/crm/v3/objects/{object_type}/{record_id}")
                        archived += 1
                    except HubSpotError as single:
                        if single.status_code == 404:
                            archived += 1
                        else:
                            failures.append({"what": f"archive {object_type} {record_id}", "error": common.describe_error(single)})
                    except httpx.HTTPError as single_transport:
                        failures.append({"what": f"archive {object_type} {record_id}", "error": _describe(single_transport)})
            except httpx.HTTPError as exc:
                failures.append({"what": f"archive {len(chunk)} {object_type}", "error": _describe(exc)})

    restored = 0
    by_type: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for key, props in (snap.get("original_values") or {}).items():
        object_type, record_id = key.split(":", 1)
        writable, _ = filter_writable_properties(object_type, props, portal_id)
        if not writable:
            restored += 1
            continue
        by_type.setdefault(object_type, []).append((record_id, {k: ("" if v is None else v) for k, v in writable.items()}))
    for object_type, items in by_type.items():
        for chunk in common.chunked(items, 100):
            inputs = [{"id": rid, "properties": props} for rid, props in chunk]
            outcome = await _write_batch(lf, f"/crm/v3/objects/{object_type}/batch/update", inputs, [rid for rid, _ in chunk])
            for rid, _ in chunk:
                state, detail = outcome[rid]
                if state == "ok":
                    restored += 1
                else:
                    failures.append({"what": f"restore {object_type} {rid}", "error": detail})

    summary = {"restored": restored, "archived": archived, "associations_removed": removed, "failed": len(failures)}
    if failures:
        await _audit(portal_id, "undo_changes", {"confirmation_id": confirmation_id, **summary})
        return {
            "status": "partially_undone",
            "confirmation_id": confirmation_id,
            **summary,
            "problems": failures[:PROBLEMS_SHOWN],
            "next": "Some changes were not reverted. Calling pronova_undo_changes again retries them; it is safe to repeat.",
        }
    if record is not None:
        record["status"] = "undone"
        record["undone_at"] = _now()
        await store.store_pending(portal_id, confirmation_id, record)
    await store.delete_undo_snapshot(portal_id, confirmation_id)
    await _audit(portal_id, "undo_changes", {"confirmation_id": confirmation_id, **summary})
    return {"status": "undone", "confirmation_id": confirmation_id, **summary}


# --- list ------------------------------------------------------------------


async def list_pending(lf: dict[str, Any], limit: int = 50, response_format: str = "concise") -> dict[str, Any]:
    """Changesets waiting for a decision. Other pending previews are not listed.

    ``detailed`` adds the first few change summaries of each, to tell similar titles apart.
    """
    portal_id = lf["portal_id"]
    store = get_store()
    items: list[dict[str, Any]] = []
    for pending_id in await store.list_pending(portal_id):
        if not _ID_RE.fullmatch(pending_id):
            continue
        record = await store.load_pending(portal_id, pending_id)
        if not record or record.get("kind") != KIND or record.get("status") != "pending":
            continue
        item = {
            "confirmation_id": pending_id,
            "title": record.get("title"),
            "change_count": record.get("change_count"),
            "created_at": record.get("created_at"),
        }
        if response_format == "detailed":
            item["changes"] = [op.get("summary") for op in (record.get("ops") or [])[:PREVIEW_SAMPLE]]
        items.append(item)
        if len(items) >= limit:
            break
    return {"count": len(items), "pending": items}


# --- tools -----------------------------------------------------------------


INLINE_CONFIRM_OFF_ENV = "HUBSPOT_MCP_INTENT_NO_INLINE_CONFIRM"
FORM_CHANGE_LINES = 15


def inline_confirm_enabled() -> bool:
    """The server asks the user itself before applying, when the client can show a form. Off only under the
    benchmark switch: headless Claude Code declares form elicitation but declines every form, so a benchmark
    session could never write, and the arms it is compared with get no form either."""
    return os.environ.get(INLINE_CONFIRM_OFF_ENV) != "1"


def _confirmation_form(record: dict[str, Any], confirmation_id: str) -> T.InputRequiredResult:
    from hubspot_mcp import server

    ops = record.get("ops") or []
    lines = [f"Apply {record['change_count']} change(s) to HubSpot: {record['title']}?"]
    lines += [f"- {op.get('summary')}" for op in ops[:FORM_CHANGE_LINES]]
    if len(ops) > FORM_CHANGE_LINES:
        lines.append(f"... and {len(ops) - FORM_CHANGE_LINES} more")
    lines.append(f"confirmation_id: {confirmation_id}. pronova_undo_changes can revert it afterwards.")
    return T.InputRequiredResult(
        input_requests={
            server._CONFIRM_KEY: T.ElicitRequest(
                method="elicitation/create",
                params=T.ElicitRequestFormParams(
                    mode="form",
                    message="\n".join(lines),
                    requested_schema={
                        "type": "object",
                        "properties": {"approve": {"type": "boolean", "description": "Apply these changes."}},
                        "required": ["approve"],
                    },
                ),
            )
        },
        request_state=confirmation_id,
    )


async def pronova_apply_changes(
    ctx: Context,
    confirmation_id: Annotated[str, Field(description="The confirmation_id from a preview, like chg_abcdefghjkmn.")],
    expected_count: Annotated[int, Field(description="The preview's change_count. A different number is refused.")],
) -> dict[str, Any] | T.InputRequiredResult:
    """The user's yes is checked by the server where the client allows it: a client that shows forms gets a
    confirmation form listing the changes, and only the user's answer to it applies them. Text in a CRM record
    cannot answer that form. A client without forms keeps the earlier contract (the model confirms with the
    user before calling), and the result says which kind of confirmation it had."""
    from hubspot_mcp import server

    lf = await common.session(ctx)
    answer = server._mrtr_answer(ctx)
    if answer is not None:
        answered_id, approved, _ = answer
        if answered_id != confirmation_id:
            raise _fail("The confirmation answer belongs to a different changeset; nothing was written.")
        if not approved:
            return {"status": "declined", "confirmation_id": confirmation_id,
                    "note": "The user declined in the confirmation form. Nothing was written; the changeset "
                            "stays pending."}
        return {**await apply_changeset(lf, confirmation_id, expected_count), "confirmed_by": "user"}
    if inline_confirm_enabled() and server._supports_form_elicitation(ctx):
        record = await _load(lf["portal_id"], confirmation_id)
        if record.get("status") == "pending" and expected_count == record.get("change_count"):
            return _confirmation_form(record, confirmation_id)
        # Anything else (already applied, a count mismatch, ...) goes on to apply_changeset, which returns the
        # earlier result or the refusal; none of those writes.
    confirmed_by = "assistant" if inline_confirm_enabled() else "assistant (confirmation form off on this server)"
    return {**await apply_changeset(lf, confirmation_id, expected_count), "confirmed_by": confirmed_by}


async def pronova_undo_changes(
    ctx: Context,
    confirmation_id: Annotated[str, Field(description="The confirmation_id of an applied changeset.")],
) -> dict[str, Any]:
    lf = await common.session(ctx)
    return await undo_changeset(lf, confirmation_id)


async def pronova_list_pending_changes(
    ctx: Context, response_format: common.ResponseFormatParam = "concise"
) -> dict[str, Any]:
    lf = await common.session(ctx)
    return await list_pending(lf, response_format=response_format)


TOOLS: list[tuple[str, object, str]] = common.guard_tools(
    [
        (
            "pronova_apply_changes",
            pronova_apply_changes,
            "Write a previewed changeset to HubSpot after the user has confirmed it. Pass the preview's "
            "confirmation_id and its change_count as expected_count; a mismatch is refused. When the client "
            "can show a confirmation form, the server asks the user directly and writes only on their yes. Records that "
            "changed since the preview are skipped and reported as drifted. Returns the status, counts "
            "of succeeded, failed and drifted changes, and the problems. Applying twice returns the first result.",
        ),
        (
            "pronova_undo_changes",
            pronova_undo_changes,
            "Revert an applied changeset after the user asks: restores the previous values of updated "
            "records, archives records it created and removes associations it added. Returns counts of "
            "restored, archived and removed items and any failures. A second undo changes nothing.",
        ),
        (
            "pronova_list_pending_changes",
            pronova_list_pending_changes,
            "List changesets previewed but not yet applied, newest first, so a confirmation_id lost "
            "from the conversation can be found. Returns each one's confirmation_id, title, "
            "change_count and created_at; detailed adds its first change summaries. Previews expire after 24 hours.",
        ),
    ]
)
