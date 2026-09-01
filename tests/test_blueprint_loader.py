import json
import pathlib

import pytest

from hubspot_mcp.blueprints.workflows import (
    build_blueprint_context,
    get_blueprint,
    reload_blueprints,
)
from hubspot_mcp.blueprints.workflows.loader import (
    list_drafts,
    load_packaged_blueprints,
    load_user_blueprints,
)


@pytest.fixture(autouse=True)
def _restore_registry():
    # reload_blueprints mutates the process-global registry; restore it so this
    # file's tests can't deplete it for later test files that snapshot a populated
    # state (mirrors test_workflow_blueprints._clean_registry).
    import hubspot_mcp.blueprints.workflows as reg

    original = dict(reg._BLUEPRINT_REGISTRY)
    yield
    reg._BLUEPRINT_REGISTRY.clear()
    reg._BLUEPRINT_REGISTRY.update(original)


def _bp_json(name="user_one", **spec_overrides):
    spec = {
        "ui_path": "Settings > Automation > Workflows",
        "object_type": "Contact-based",
        "enrollment": {"type": "EVENT_BASED", "trigger": "Contact is created"},
        "actions": [
            {"ui_action": "Send internal email notification", "fields": {"Subject": "Hi"}},
        ],
        "prerequisites": [],
        "validation": [],
    }
    spec.update(spec_overrides)
    return {
        "name": name,
        "description": f"a user blueprint named {name}",
        "tags": ["user"],
        "parameters": {},
        "spec": spec,
    }


@pytest.fixture
def user_base(tmp_path):
    (tmp_path / "blueprints").mkdir()
    return tmp_path


# --- load_packaged_blueprints (wheel-safe importlib.resources) -------------


class TestPackagedBlueprints:
    def test_returns_all_nineteen(self):
        # Resolves via importlib.resources.files(__package__)/"data" — works
        # identically from a source checkout and an installed wheel (R8).
        bps = load_packaged_blueprints()
        assert len(bps) == 19

    def test_expected_names_present(self):
        names = {bp.name for bp in load_packaged_blueprints()}
        assert names >= {
            "welcome_email", "lead_scoring", "deal_stage_task", "re_engagement",
            "re_speed_to_lead", "re_stale_listing", "re_closing_day",
        }

    def test_packaged_blueprint_renders(self):
        bp = {bp.name: bp for bp in load_packaged_blueprints()}["deal_stage_task"]
        spec = bp.build({"stage": "closedwon", "due_days": 3})
        assert spec["actions"][0]["fields"]["Due date"] == "{timestamp + 3d}"


# --- load_user_blueprints ------------------------------------------------


class TestLoadUserBlueprints:
    def test_reads_json_from_blueprints_dir(self, user_base):
        (user_base / "blueprints" / "one.json").write_text(json.dumps(_bp_json("one")))
        bps = load_user_blueprints(base_dir=user_base)
        assert [b.name for b in bps] == ["one"]

    def test_build_closure_renders(self, user_base):
        data = _bp_json("one")
        data["parameters"] = {"subject": {"type": "string", "default": "Hi", "description": "s"}}
        data["spec"]["actions"] = [
            {"ui_action": "Send internal email notification", "fields": {"Subject": "{{param:subject}}"}},
        ]
        (user_base / "blueprints" / "one.json").write_text(json.dumps(data))
        bp = load_user_blueprints(base_dir=user_base)[0]
        assert bp.build({"subject": "Hello!"})["actions"][0]["fields"]["Subject"] == "Hello!"

    def test_bad_file_skipped_not_crash(self, user_base):
        (user_base / "blueprints" / "good.json").write_text(json.dumps(_bp_json("good")))
        (user_base / "blueprints" / "bad.json").write_text("{ not valid json")
        bps = load_user_blueprints(base_dir=user_base)
        assert [b.name for b in bps] == ["good"]

    def test_missing_dir_returns_empty(self, tmp_path):
        assert load_user_blueprints(base_dir=tmp_path) == []

    def test_user_overrides_on_collision_via_reload(self, user_base):
        # Phase 1 has no packaged blueprints; verify reload registers packaged(0)
        # then user, and a colliding user entry wins the registry slot.
        (user_base / "blueprints" / "welcome_email.json").write_text(
            json.dumps(_bp_json("welcome_email", object_type="Deal-based"))
        )
        reload_blueprints(base_dir=user_base)
        bp = get_blueprint("welcome_email")
        assert bp is not None
        assert bp.build({})["object_type"] == "Deal-based"


# --- drafts --------------------------------------------------------------


class TestDrafts:
    def test_list_drafts_lists_draft_dir(self, user_base):
        ddir = user_base / "blueprints" / "drafts"
        ddir.mkdir()
        (ddir / "draft_a.json").write_text(json.dumps(_bp_json("draft_a")))
        (ddir / "draft_b.json").write_text(json.dumps(_bp_json("draft_b")))
        drafts = list_drafts(base_dir=user_base)
        assert [d.name for d in drafts] == ["draft_a.json", "draft_b.json"]

    def test_drafts_not_registered_by_load_user(self, user_base):
        ddir = user_base / "blueprints" / "drafts"
        ddir.mkdir()
        (ddir / "draft_a.json").write_text(json.dumps(_bp_json("draft_a")))
        # drafts live in drafts/ subdir; load_user_blueprints globs blueprints/*.json only
        assert [b.name for b in load_user_blueprints(base_dir=user_base)] == []

    def test_no_drafts_dir_returns_empty(self, tmp_path):
        assert list_drafts(base_dir=tmp_path) == []


# --- build_blueprint_context draft surfacing -----------------------------


class TestContextDrafts:
    def test_drafts_surfaced_when_present(self, tmp_path, monkeypatch):
        # build_blueprint_context() reads list_drafts() with default base_dir,
        # which resolves Path.home()/.claude/hubspot/blueprints/drafts — so the
        # draft must live under the full CONFIG_DIR layout.
        monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
        ddir = tmp_path / ".claude" / "hubspot" / "blueprints" / "drafts"
        ddir.mkdir(parents=True)
        (ddir / "draft_a.json").write_text(json.dumps(_bp_json("draft_a")))
        ctx = build_blueprint_context()
        assert "Pending draft blueprints" in ctx
        assert "draft_a.json" in ctx

    def test_no_drafts_section_when_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
        ctx = build_blueprint_context()
        assert "Pending draft blueprints" not in ctx