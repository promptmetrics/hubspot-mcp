"""The `StateStore` seam (Phase 2, Task 1).

Phase 1 defined `StateStore` but nothing used it: `handlers`, `safety` and
`server` called the `persistence` / `snapshot` / `audit` modules directly, so
the "drop in a `RedisStateStore`" plan in `docs/architecture.md` §4 would not
have worked. These tests pin the seam that makes it work:

* nothing outside `hubspot_mcp.state` imports a storage module;
* every method on the interface is actually reached from `src/`;
* installing a non-file store genuinely diverts the write path off disk.

The last one is the decisive test. A remote store is a drop-in only if the
safety path can complete a full preview -> approve -> undo cycle without ever
touching the filesystem, and the only honest way to assert that is to point the
state directory somewhere that must not come into existence.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hubspot_mcp import handlers, safety, server, state
from hubspot_mcp.config import PortalConfig
from hubspot_mcp.handlers import handle_approve, handle_tool
from hubspot_mcp.state import FileStateStore, StateStore, get_store, set_store

SRC = Path(inspect.getfile(state)).parent.parent
STORAGE_MODULES = {"persistence", "snapshot", "audit"}

# `snapshot.is_undoable` is a pure predicate over a preview's captured
# originals -- no I/O, no paths -- and `policy` needs it to classify a write
# before any store exists. It is the one allowed exception.
_ALLOWED_STORAGE_IMPORTS = {("policy.py", "snapshot", "is_undoable")}


class RecordingStore(StateStore):
    """In-memory `StateStore` that records which interface methods were used.

    Stands in for `RedisStateStore`: it shares nothing with the file store, so
    any call that slips past the seam shows up as a filesystem write.
    """

    def __init__(self) -> None:
        self.pending: dict[tuple[str, str], dict[str, Any]] = {}
        self.snapshots: dict[tuple[str, str], dict[str, Any]] = {}
        self.audits: list[dict[str, Any]] = []
        self.calls: list[str] = []

    def _seen(self, name: str) -> None:
        self.calls.append(name)

    # --- pending previews -------------------------------------------------

    def store_pending(self, portal_id: str, action_id: str, data: dict[str, Any]) -> None:
        self._seen("store_pending")
        self.pending[(portal_id, action_id)] = dict(data)

    def load_pending(self, portal_id: str, action_id: str) -> dict[str, Any] | None:
        self._seen("load_pending")
        found = self.pending.get((portal_id, action_id))
        return dict(found) if found is not None else None

    def confirm_pending(self, portal_id: str, action_id: str, count: int) -> bool:
        self._seen("confirm_pending")
        record = self.pending.get((portal_id, action_id))
        if record is None or record.get("required_confirmation") != count:
            return False
        record["confirmed_count"] = count
        return True

    def clear_pending(self, portal_id: str, action_id: str) -> None:
        self._seen("clear_pending")
        self.pending.pop((portal_id, action_id), None)

    def list_pending(self, portal_id: str) -> list[str]:
        self._seen("list_pending")
        return [aid for (pid, aid) in self.pending if pid == portal_id]

    # --- undo snapshots ---------------------------------------------------

    def save_undo_snapshot_for_action(self, portal_id: str, action_id: str, preview_data: dict[str, Any]) -> None:
        self._seen("save_undo_snapshot_for_action")
        self.snapshots[(portal_id, action_id)] = {
            "action_id": action_id,
            "original_values": preview_data.get("original_values") or {},
            "metadata": {"intent_type": (preview_data.get("intent") or {}).get("intent_type")},
        }

    def save_undo_snapshot(
        self,
        portal_id: str,
        action_id: str,
        original_values: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._seen("save_undo_snapshot")
        self.snapshots[(portal_id, action_id)] = {
            "action_id": action_id,
            "original_values": dict(original_values),
            "metadata": dict(metadata or {}),
        }

    def load_undo_snapshot(self, portal_id: str, action_id: str) -> dict[str, Any] | None:
        self._seen("load_undo_snapshot")
        return self.snapshots.get((portal_id, action_id))

    def update_undo_snapshot(self, portal_id: str, action_id: str, *, metadata: dict[str, Any] | None = None) -> None:
        self._seen("update_undo_snapshot")
        snap = self.snapshots.get((portal_id, action_id))
        if snap is not None and metadata is not None:
            snap.setdefault("metadata", {}).update(metadata)

    def delete_undo_snapshot(self, portal_id: str, action_id: str) -> None:
        self._seen("delete_undo_snapshot")
        self.snapshots.pop((portal_id, action_id), None)

    # --- audit log --------------------------------------------------------

    def log_write(
        self,
        portal_id: str,
        *,
        action: str,
        agent: str,
        result_summary: dict[str, Any],
        informing_sources: list[dict[str, Any]] | None = None,
    ) -> None:
        self._seen("log_write")
        self.audits.append({"portal_id": portal_id, "action": action, "agent": agent})

    def get_recent_audits(self, portal_id: str, limit: int = 50) -> list[dict[str, Any]]:
        self._seen("get_recent_audits")
        return [a for a in reversed(self.audits) if a["portal_id"] == portal_id][:limit]


class _FakeClient:
    async def close(self) -> None:
        pass


@pytest.fixture
def forbidden_disk(tmp_path, monkeypatch):
    """Root all on-disk state at a path that must never come into existence.

    Every file-backed write in this codebase runs `mkdir(parents=True)` first,
    so if anything bypasses the installed store, this directory appears.
    """
    forbidden = tmp_path / "must-not-exist"
    monkeypatch.setattr(Path, "home", lambda: forbidden)
    monkeypatch.setattr("hubspot_mcp.config.CONFIG_DIR", forbidden)
    monkeypatch.setattr("hubspot_mcp.persistence.CONFIG_DIR", forbidden)
    return forbidden


@pytest.fixture
def store(monkeypatch):
    recording = RecordingStore()
    set_store(recording)
    yield recording
    set_store(None)


def _portal() -> PortalConfig:
    return PortalConfig(portal_id="99999999", token="test-token", scopes_granted=[])


# --------------------------------------------------------------------------- #
# The seam holds: no direct storage imports outside the state package
# --------------------------------------------------------------------------- #


def _storage_imports(path: Path) -> list[tuple[str, str]]:
    """Return `(module, name)` for each `persistence`/`snapshot`/`audit` import."""
    found: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] == "hubspot_mcp" and len(parts) > 1 and parts[1] in STORAGE_MODULES:
                found.extend((parts[1], alias.name) for alias in node.names)
            elif node.module == "hubspot_mcp":
                found.extend((a.name, "*") for a in node.names if a.name in STORAGE_MODULES)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "hubspot_mcp" and len(parts) > 1 and parts[1] in STORAGE_MODULES:
                    found.append((parts[1], "*"))
    return found


def test_no_module_outside_the_state_package_imports_storage_directly():
    """Phase 2 swaps one factory, not ~30 call sites -- keep it that way."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC)
        if rel.parts[0] == "state" or rel.name in {f"{m}.py" for m in STORAGE_MODULES}:
            continue
        for module, name in _storage_imports(path):
            if (rel.name, module, name) in _ALLOWED_STORAGE_IMPORTS:
                continue
            offenders.append(f"{rel}: imports {name} from {module}")
    assert offenders == [], (
        "these must go through hubspot_mcp.state.get_store() instead:\n  " + "\n  ".join(offenders)
    )


def test_every_interface_method_is_reached_from_src():
    """No dead methods on the interface, and none missing.

    `save_undo_snapshot` was absent from the Phase 1 interface even though the
    pattern-write executor needs it -- the omission was invisible precisely
    because nothing called the interface.
    """
    abstract = {
        name
        for name, member in vars(StateStore).items()
        if getattr(member, "__isabstractmethod__", False)
    }
    body = "\n".join(p.read_text() for p in SRC.rglob("*.py") if p.parent.name != "state")
    # `.name(` misses `asyncio.to_thread(get_store().store_pending, ...)`.
    unreached = {name for name in abstract if not re.search(rf"\.{name}\b", body)}
    assert unreached == set(), f"StateStore methods nothing calls: {sorted(unreached)}"


def test_file_store_is_the_default_and_set_store_round_trips():
    set_store(None)
    assert isinstance(get_store(), FileStateStore)
    replacement = RecordingStore()
    set_store(replacement)
    assert get_store() is replacement
    set_store(None)
    assert isinstance(get_store(), FileStateStore)


# --------------------------------------------------------------------------- #
# The seam works: a non-file store diverts the write path off disk entirely
# --------------------------------------------------------------------------- #


async def test_update_preview_and_approve_never_touch_disk(store, forbidden_disk):
    """The decisive test: this is what makes RedisStateStore a drop-in."""
    portal = _portal()
    updated: list[dict[str, Any]] = []

    async def fake_invoke(tool_name, portal_id, **kwargs):
        if tool_name == "hubspot_get_object":
            return {"id": kwargs["object_id"], "properties": {"lifecyclestage": "lead"}}
        updated.append(dict(kwargs))
        return {"id": kwargs.get("object_id"), "properties": kwargs.get("properties", {})}

    with patch.object(handlers, "invoke_tool", fake_invoke):
        preview = await handle_tool(
            _FakeClient(),
            None,
            portal,
            {
                "tool_name": "hubspot_update_object",
                # A sensitive property forces the CONFIRM tier, so this
                # previews rather than auto-applying (policy rule 4).
                "input": {
                    "object_type": "contacts",
                    "object_id": "1",
                    "properties": {"lifecyclestage": "customer"},
                },
            },
        )
        action_id = preview["data"]["action_id"]
        assert preview["data"]["status"] == "preview"
        assert (portal.portal_id, action_id) in store.pending

        approved = await handle_approve(
            _FakeClient(), None, portal, {"action_id": action_id}
        )

    assert approved["data"]["status"] == "success"
    assert updated, "the approved write never reached the tool layer"
    assert store.audits and store.audits[0]["action"] == f"approve:{action_id}"
    assert (portal.portal_id, action_id) not in store.pending
    assert not forbidden_disk.exists(), (
        f"the write path wrote to disk at {forbidden_disk} despite a non-file store"
    )


async def test_pattern_write_snapshot_goes_through_the_store(store, forbidden_disk):
    """Exercises `save_undo_snapshot`, the method Phase 1's interface lacked."""
    portal = _portal()
    # A non-sensitive property: `lifecyclestage` would fail the pattern
    # eligibility gate before any snapshot is written.
    props = {"1": {"jobtitle": "Analyst"}, "2": {"jobtitle": "Analyst"}}

    async def fake_invoke(tool_name, portal_id, **kwargs):
        if tool_name == "hubspot_get_object":
            return {"id": kwargs["object_id"], "properties": dict(props[str(kwargs["object_id"])])}
        return {"id": kwargs.get("object_id"), "properties": kwargs.get("properties", {})}

    with patch.object(handlers, "invoke_tool", fake_invoke):
        preview = await handle_tool(
            _FakeClient(),
            None,
            portal,
            {
                "tool_name": "hubspot_bulk_update_objects",
                "input": {
                    "object_type": "contacts",
                    "records": [
                        {"id": "1", "properties": {"jobtitle": "Manager"}},
                        {"id": "2", "properties": {"jobtitle": "Manager"}},
                    ],
                },
                "batch_mode": "pattern",
            },
        )
        action_id = preview["data"]["action_id"]
        assert preview["data"]["status"] == "preview"
        await handle_approve(_FakeClient(), None, portal, {"action_id": action_id})

    assert "save_undo_snapshot" in store.calls
    snap = store.snapshots[(portal.portal_id, action_id)]
    assert set(snap["original_values"]) == {"1", "2"}
    assert not forbidden_disk.exists()


async def test_undo_reads_and_deletes_its_snapshot_through_the_store(store):
    """`hubspot_undo_write`'s snapshot handling must not be filesystem-shaped."""
    portal = _portal()
    store.save_undo_snapshot(
        portal.portal_id,
        "act-1",
        {"1": {"firstname": "Old"}},
        metadata={"intent_type": "update", "target_object": "contacts", "undoable": True},
    )

    async def fake_invoke(tool_name, portal_id, **kwargs):
        return {"id": kwargs.get("object_id"), "properties": kwargs.get("properties", {})}

    with patch.object(handlers, "invoke_tool", fake_invoke):
        snapshot = get_store().load_undo_snapshot(portal.portal_id, "act-1")
        assert snapshot is not None
        succeeded, _ = await handlers.undo_action(
            snapshot, portal.portal_id, portal, client=_FakeClient()
        )

    assert succeeded
    get_store().delete_undo_snapshot(portal.portal_id, "act-1")
    assert get_store().load_undo_snapshot(portal.portal_id, "act-1") is None


# --------------------------------------------------------------------------- #
# The interface is storage-agnostic
# --------------------------------------------------------------------------- #


def test_list_pending_returns_action_ids_not_paths(tmp_path, monkeypatch):
    """A path is meaningless to a remote store and leaks $HOME to the client."""
    monkeypatch.setattr("hubspot_mcp.persistence.CONFIG_DIR", tmp_path)
    set_store(None)
    file_store = get_store()
    file_store.store_pending("99999999", "act-1", {"tool_name": "hubspot_update_object"})

    listed = file_store.list_pending("99999999")

    assert listed == ["act-1"]
    assert all(isinstance(entry, str) for entry in listed)


def test_interface_exposes_no_filesystem_types():
    """`Path` in a signature would make the interface unimplementable remotely."""
    leaks = []
    for name, member in vars(StateStore).items():
        if not getattr(member, "__isabstractmethod__", False):
            continue
        annotations = inspect.get_annotations(member, eval_str=False)
        leaks.extend(
            f"{name}.{param}: {ann}" for param, ann in annotations.items() if "Path" in str(ann)
        )
    assert leaks == [], f"filesystem types on the StateStore interface: {leaks}"


def test_safety_and_server_resolve_the_store_lazily():
    """Binding the store at import would freeze the file store into the module."""
    for module in (handlers, safety, server):
        assert not hasattr(module, "_store"), (
            f"{module.__name__} holds a module-level store; it must call get_store() per use "
            "so Phase 2 can install the remote store after import"
        )
