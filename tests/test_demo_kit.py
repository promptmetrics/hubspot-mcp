"""demo/kit.py: the two-portal demo kit, driven by a fake subprocess runner, a fake portal reader and fake transcripts.

No network, no HubSpot, no claude. Every name, id, token and address here is a placeholder.
"""
from __future__ import annotations

import json
import re
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "demo"))
import kit  # noqa: E402

DAY = "2026-11-10"
LEFT_ID, RIGHT_ID = "111", "222"
LEFT_TOKEN, RIGHT_TOKEN = "tok-left-SECRET-1111", "tok-right-SECRET-2222"
ENV = {"HUBSPOT_TOKEN_111": LEFT_TOKEN, "HUBSPOT_TOKEN_222": RIGHT_TOKEN, "PATH": "/usr/bin",
       "SEED_DEMO_USER_EMAIL": "demo.user@example.com", "SEED_OWNER_EMAIL_BASE": "ops@example.com"}
PREPARED = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
EMAIL = "demo.user@example.com"

PAST_DUE = ["Past Due One", "Past Due Two", "Past Due Three"]


# --- fixtures ------------------------------------------------------------------------------------------------------


def make_truth(day: str = DAY) -> dict:
    figures = {"closed_won": 27000, "hubspot_commit": 85000, "best_case": 120500, "pipeline": 640000,
               "hubspot_weighted": 310250, "supported_commit": 58000}
    return {
        "meta": {"as_of": day, "demo_owner": "demo", "portal": "left"},
        "job1": {"prompt": "Which of my deals need attention today, and why?", "owners": {"demo": {
            "flagged_count": 5, "by_code": {"OVERDUE": ["a", "b", "c"], "SILENT": ["d"], "NO_NEXT": ["e"]},
            "flagged": [
                {"dealname": "Acme Corp renewal", "codes": ["SILENT"]},
                {"dealname": "Initech upgrade", "codes": ["NO_NEXT"]},
                *({"dealname": n, "codes": ["OVERDUE"]} for n in PAST_DUE)]}}},
        "job3": {
            "prompt": "Just finished with Acme: they'll pilot, close moves to Dec 15, amount EUR 24k.",
            "target_deal": {"seed_key": "D01", "dealname": "Acme Corp renewal"},
            "expected_end_state": {
                "deal": {"seed_key": "D01", "closedate": "2026-12-15", "amount": 24000, "currency": "EUR",
                         "unchanged": ["dealstage", "hubspot_owner_id"]},
                "meeting": {"count": 1, "date": DAY, "associated_with": ["deal:D01"]},
                "note": {"count": 1, "mentions": ["pilot"], "associated_with": ["deal:D01"]},
                "task": {"count": 1, "title_mentions": ["security"], "due": "2026-11-13", "status": "NOT_STARTED",
                         "associated_with": ["deal:D01"]},
                "contact": {"count": 1, "firstname": "Maria", "lastname": "Lopez", "jobtitle": "CFO",
                            "associated_with": ["company:Acme"]}}},
        "job4": {"prompt": "What will I close this quarter, and what's at risk?",
                 "tolerance": "numeric match within +/-0.5 percent",
                 "period": {"label": "2026-Q4"}, "company_currency": "EUR",
                 "owners": {"demo": {**{k: {"total": v} for k, v in figures.items()},
                                     "supported_commit_removed": [{"dealname": "Acme Corp renewal", "codes": ["SILENT"]}],
                                     "slipped_by_arm_rules": {"count": 2}}}},
        "job5": {"prompt": "Move every past-due close date on my open deals to the end of next month and add a task on each to confirm timing.",
                 "count": len(PAST_DUE), "new_close_date": "2026-12-31", "deals": [{"dealname": n} for n in PAST_DUE],
                 "tasks_expected": {"count": len(PAST_DUE), "one_per_deal": True, "title_mentions": ["confirm timing"]}},
    }


RULES_YAML = f"as_of: {DAY}\ntimezone: Europe/Berlin\ndefault_owner: \"{EMAIL}\"\nsilent_days: 14\n"


def make_snapshot() -> dict:
    return {
        "version": 1, "portal": "left", "portal_id": LEFT_ID, "demo_day": DAY, "snapshot_at": "2026-11-10T07:00:00.000Z",
        "deals": {"D01": {"id": "9001", "roles": ["job3"], "properties": {
            "dealname": "Acme Corp renewal", "amount": "85000", "closedate": "2026-11-28T00:00:00Z",
            "dealstage": "st_neg", "pipeline": "default", "hs_next_step": "Send revised order form",
            "deal_currency_code": "EUR", "seed_tag": "hubspot-forecast-demo"}, "associations": {}}},
        "contacts": {"jane": {"id": "7001", "properties": {"firstname": "Jane", "lastname": "Doe", "email": EMAIL,
                                                           "phone": "+1 555 0100", "jobtitle": "VP", "seed_tag": "x"}}},
        "companies": {"acme": {"id": "5001", "properties": {"name": "Acme Corp", "domain": "acme.example.com", "seed_tag": "x"}}},
        "seeded_engagement_ids": {"tasks": ["401"], "notes": [], "meetings": ["301"], "calls": [], "emails": []},
    }


@pytest.fixture
def seed_dir(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    (seed / "out").mkdir(parents=True)
    for name in ("preflight.py", "reset.py", "seed.py", "restore.py"):
        (seed / name).write_text("", encoding="utf-8")
    for side in ("left", "right"):
        (seed / "out" / f"truth-{side}-{DAY}.json").write_text(json.dumps(make_truth()), encoding="utf-8")
        (seed / "out" / f"rules-{side}-{DAY}.yaml").write_text(RULES_YAML, encoding="utf-8")
        snap = make_snapshot()
        snap["portal"] = side
        (seed / "out" / f"snapshot-{side}-{DAY}.json").write_text(json.dumps(snap), encoding="utf-8")
        write_manifest(seed, side)
    return seed


def write_manifest(seed: Path, side: str, stamp: str = "2026-11-10-070000", **changes) -> Path:
    manifest = {"portal": side, "portal_id": LEFT_ID if side == "left" else RIGHT_ID, "demo_day": DAY,
                "owners": {"demo": "1", "rep1": "2"}, "team": {"id": "9"},
                "deals": {f"D{i}": str(i) for i in range(5)}, "companies": {"acme": "1", "initech": "2"},
                "contacts": {"jane": "1"}, "goals": {"g1": "1", "g2": "2", "g3": None}}
    manifest.update(changes)
    path = seed / "out" / f"{side}-{stamp}.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    skills = tmp_path / "skills"
    for name in ("pipeline-triage", "forecast"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(f"---\nname: {name}\n---\nPlaceholder skill.\n", encoding="utf-8")
    (skills / "README.md").write_text("not a skill folder\n", encoding="utf-8")
    return skills


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "hubspot-demo"
    assert kit.main(["setup", "--home", str(path), "--left-portal", LEFT_ID, "--right-portal", RIGHT_ID]) == 0
    return path


class FakeRunner:
    def __init__(self, fail: tuple[str, str] | None = None, log: list | None = None, outputs: dict | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail
        self.log = log if log is not None else []
        self.outputs = outputs or {}

    def __call__(self, argv, env):
        script = Path(argv[1]).name
        label = argv[argv.index("--portal") + 1]
        self.calls.append(argv)
        self.log.append(f"{script}:{label}")
        if self.fail == (script, label):
            return kit.RunResult(3, "", f"boom with {LEFT_TOKEN} and {EMAIL}")
        return kit.RunResult(0, self.outputs.get((script, label), ""), "")


def do_prepare(home: Path, seed_dir: Path, skills_dir: Path, runner, *, yes: bool = True, env=None) -> int:
    argv = ["prepare", "--home", str(home), "--day", DAY, "--seed-dir", str(seed_dir), "--skills-dir", str(skills_dir)]
    return kit.main(argv + (["--yes"] if yes else []), env=ENV if env is None else env, runner=runner)


# --- config ---------------------------------------------------------------------------------------------------------


def test_setup_round_trip_holds_names_not_tokens(tmp_path):
    path = tmp_path / "demo-home"
    assert kit.main(["setup", "--home", str(path), "--left-portal", LEFT_ID, "--right-portal", RIGHT_ID,
                     "--left-server", "official-hubspot"], env=ENV) == 0
    raw = json.loads((path / "portals.json").read_text())
    assert raw["left"] == {"label": "left", "portal_id": LEFT_ID, "token_env": "HUBSPOT_TOKEN_111"}
    assert raw["right"] == {"label": "right", "portal_id": RIGHT_ID, "token_env": "HUBSPOT_TOKEN_222"}
    assert raw["left_server"] == "official-hubspot"
    assert LEFT_TOKEN not in (path / "portals.json").read_text()
    cfg = kit.load_config(path)
    assert (cfg.left.portal_id, cfg.right.token_env, cfg.left_server) == (LEFT_ID, "HUBSPOT_TOKEN_222", "official-hubspot")
    assert cfg.to_json() == raw


def test_setup_defaults_the_left_server_to_hubspot_bench(home):
    assert kit.load_config(home).left_server == "hubspot-bench"


@pytest.mark.parametrize("left,right,server", [("111", "111", "hubspot-bench"), ("abc", "222", "hubspot-bench"),
                                               ("111", "222", "bad name!")])
def test_setup_rejects_bad_input(tmp_path, capsys, left, right, server):
    path = tmp_path / "demo-home"
    assert kit.main(["setup", "--home", str(path), "--left-portal", left, "--right-portal", right,
                     "--left-server", server]) == 2
    assert not (path / "portals.json").exists()
    assert "error:" in capsys.readouterr().err


def test_load_config_without_setup_names_the_fix(tmp_path):
    with pytest.raises(kit.KitError, match="kit.py setup"):
        kit.load_config(tmp_path)


def test_parse_day_today_and_errors():
    assert kit.parse_day("today", lambda: datetime(2026, 11, 2).date()) == "2026-11-02"
    for bad in ("2026-13-01", "11/10/2026", "tomorrow"):
        with pytest.raises(kit.KitError):
            kit.parse_day(bad)


# --- check ----------------------------------------------------------------------------------------------------------


def test_check_runs_preflight_on_both_sides_and_prints_a_table(home, seed_dir, capsys):
    ok = json.dumps({"checks": [{"name": "portal", "status": "ok", "detail": "fine"}]})
    bad = json.dumps({"checks": [{"name": "team", "status": "fail", "detail": f"missing for {EMAIL}"}]})
    calls: list[list[str]] = []

    def runner(argv, env):
        calls.append(argv)
        return kit.RunResult(0, ok) if "left" in argv else kit.RunResult(1, bad)
    code = kit.main(["check", "--home", str(home), "--day", DAY, "--seed-dir", str(seed_dir)], env=ENV, runner=runner)
    out = capsys.readouterr().out
    assert code == 1
    left, right = calls
    assert left[2:] == ["--portal", "left", "--expect-portal", LEFT_ID, "--token-env", "HUBSPOT_TOKEN_111",
                        "--demo-day", DAY, "--json"]
    assert right[2:] == ["--portal", "right", "--expect-portal", RIGHT_ID, "--token-env", "HUBSPOT_TOKEN_222",
                         "--demo-day", DAY, "--json"]
    assert "NOT READY" in out and "team: missing for <email>" in out and EMAIL not in out
    assert "pass" in out and "a check failed" in out


def test_check_ready_when_both_pass(home, seed_dir, capsys):
    runner = FakeRunner(outputs={("preflight.py", "left"): "{}", ("preflight.py", "right"): "{}"})
    assert kit.main(["check", "--home", str(home), "--day", DAY, "--seed-dir", str(seed_dir)], env=ENV, runner=runner) == 0
    assert "READY: both portals pass." in capsys.readouterr().out


def test_check_reports_a_refused_day(home, seed_dir, capsys):
    runner = FakeRunner()

    def refuse(argv, env):
        return kit.RunResult(4, "", "REFUSING: the first day of a quarter")
    code = kit.main(["check", "--home", str(home), "--day", DAY, "--seed-dir", str(seed_dir)], env=ENV, runner=refuse)
    assert code == 4 and "demo day or plan refused" in capsys.readouterr().out
    assert runner.calls == []


# --- prepare --------------------------------------------------------------------------------------------------------


def test_prepare_without_yes_refuses_and_runs_nothing(home, seed_dir, skills_dir, capsys):
    runner = FakeRunner()
    assert do_prepare(home, seed_dir, skills_dir, runner, yes=False) == 2
    out = capsys.readouterr().out
    assert runner.calls == []
    assert "REFUSING" in out and "reset.py" in out and f"portal {RIGHT_ID}" in out
    assert sorted(p.name for p in home.iterdir()) == ["portals.json"]


def test_prepare_refuses_when_a_token_variable_is_unset(home, seed_dir, skills_dir, capsys):
    runner = FakeRunner()
    assert do_prepare(home, seed_dir, skills_dir, runner, env={"HUBSPOT_TOKEN_111": LEFT_TOKEN}) == 2
    assert "HUBSPOT_TOKEN_222" in capsys.readouterr().out and runner.calls == []


def test_prepare_validates_then_resets_then_seeds_each_portal(home, seed_dir, skills_dir):
    runner = FakeRunner()
    assert do_prepare(home, seed_dir, skills_dir, runner) == 0
    assert runner.log == ["seed.py:left", "seed.py:right", "preflight.py:left", "preflight.py:right", "reset.py:left",
                          "seed.py:left", "reset.py:right", "seed.py:right"]
    by_call = [(Path(c[1]).name, c[2:]) for c in runner.calls if Path(c[1]).name != "preflight.py"]

    def common(label, pid):
        return ["--portal", label, "--expect-portal", pid, "--token-env", f"HUBSPOT_TOKEN_{pid}"]
    assert by_call[0] == ("seed.py", common("left", LEFT_ID) + ["--demo-day", DAY, "--plan"])
    assert by_call[1] == ("seed.py", common("right", RIGHT_ID) + ["--demo-day", DAY, "--plan"])
    assert by_call[2] == ("reset.py", common("left", LEFT_ID) + ["--apply", "--yes"])
    assert by_call[3] == ("seed.py", common("left", LEFT_ID) + ["--demo-day", DAY, "--apply", "--yes"])
    assert by_call[4] == ("reset.py", common("right", RIGHT_ID) + ["--apply", "--yes"])
    assert by_call[5] == ("seed.py", common("right", RIGHT_ID) + ["--demo-day", DAY, "--apply", "--yes"])


def test_prepare_stops_on_the_first_failure_and_writes_nothing(home, seed_dir, skills_dir, capsys):
    calls = []

    def flaky(argv, env):
        calls.append((Path(argv[1]).name, argv[argv.index("--portal") + 1]))
        if Path(argv[1]).name == "seed.py" and "--apply" in argv and "left" in argv:
            return kit.RunResult(3, "", f"boom with {LEFT_TOKEN} and {EMAIL}")
        return kit.RunResult(0)
    assert do_prepare(home, seed_dir, skills_dir, flaky) == 1
    out = capsys.readouterr().out
    assert calls == [("seed.py", "left"), ("seed.py", "right"), ("preflight.py", "left"), ("preflight.py", "right"),
                     ("reset.py", "left"), ("seed.py", "left")]
    assert "FAILED: seed.py (left" in out and LEFT_TOKEN not in out and EMAIL not in out and "<redacted>" in out
    assert not (home / "state.json").exists() and not (home / "left").exists()


def test_prepare_stops_when_reset_fails_and_never_touches_the_other_portal(home, seed_dir, skills_dir):
    runner = FakeRunner(fail=("reset.py", "left"))
    assert do_prepare(home, seed_dir, skills_dir, runner) == 1
    assert runner.log == ["seed.py:left", "seed.py:right", "preflight.py:left", "preflight.py:right", "reset.py:left"]


def test_prepare_refused_plan_stops_before_any_reset(home, seed_dir, skills_dir, capsys):
    calls = []

    def refuse(argv, env):
        calls.append(Path(argv[1]).name)
        return kit.RunResult(2, "", "REFUSING: first day of the quarter") if "--plan" in argv else kit.RunResult(0)
    assert do_prepare(home, seed_dir, skills_dir, refuse) == 1
    assert calls == ["seed.py"]
    assert "Nothing was changed on either portal." in capsys.readouterr().out


def test_prepare_writes_the_workspace(home, seed_dir, skills_dir):
    stale = home / "left" / ".claude" / "skills" / "stale-skill"
    stale.mkdir(parents=True)
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner(), env=ENV) == 0

    assert json.loads((home / "left" / "mcp.json").read_text()) == {
        "mcpServers": {"hubspot-bench": {"type": "http", "url": "https://mcp.hubspot.com"}}}
    right = json.loads((home / "right" / "mcp.json").read_text())
    assert right == {"mcpServers": {"pronova": {
        "command": "uv",
        "args": ["--directory", str(kit.REPO), "run", "hubspot-mcp", "--portal", RIGHT_ID, "--mode", "token",
                 "--surface", "intent"],
        "env": {"HUBSPOT_MCP_RULES_DIR": str(home / "rules")}}}}

    assert sorted(p.name for p in (home / "right" / ".claude" / "skills").iterdir()) == ["forecast", "pipeline-triage"]
    assert (home / "right" / ".claude" / "skills" / "forecast" / "SKILL.md").is_file()
    assert not (home / "left" / ".claude" / "skills").exists()

    assert (home / "rules" / f"{RIGHT_ID}.yaml").read_text() == RULES_YAML
    rules_md = (home / "sales-rules.md").read_text()
    assert f"As-of date: {DAY}" in rules_md and "Rules fingerprint" in rules_md

    state = json.loads((home / "state.json").read_text())
    assert state["day"] == DAY and state["seed_dir"] == str(seed_dir)
    assert state["truth"]["right"] == str(seed_dir / "out" / f"truth-right-{DAY}.json")
    assert state["portals"]["right"] == {"portal_id": RIGHT_ID, "label": "right", "token_env": "HUBSPOT_TOKEN_222"}
    assert datetime.fromisoformat(state["prepared_at"]).tzinfo is not None


def test_the_left_side_gets_the_configured_server_name(tmp_path, seed_dir, skills_dir):
    path = tmp_path / "other-home"
    assert kit.main(["setup", "--home", str(path), "--left-portal", LEFT_ID, "--right-portal", RIGHT_ID,
                     "--left-server", "official-hubspot"]) == 0
    assert do_prepare(path, seed_dir, skills_dir, FakeRunner()) == 0
    assert list(json.loads((path / "left" / "mcp.json").read_text())["mcpServers"]) == ["official-hubspot"]
    assert '--allowedTools "mcp__official-hubspot"' in (path / "left" / "launch.sh").read_text()


def test_no_token_value_reaches_any_written_file(home, seed_dir, skills_dir):
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 0
    files = [p for p in home.rglob("*") if p.is_file()]
    assert len(files) > 8
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert LEFT_TOKEN not in text and RIGHT_TOKEN not in text, path


def test_launch_scripts(home, seed_dir, skills_dir):
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 0
    left = (home / "left" / "launch.sh").read_text()
    right = (home / "right" / "launch.sh").read_text()
    common = ("claude --strict-mcp-config --mcp-config mcp.json --setting-sources project --model claude-sonnet-5-5 "
              "--append-system-prompt-file ../sales-rules.md")
    assert f"exec env -u HUBSPOT_TOKEN_111 -u HUBSPOT_TOKEN_222 {common} --allowedTools \"mcp__hubspot-bench\"\n" in left
    assert "Skill" not in left and ":?" not in left
    assert f"exec env -u HUBSPOT_TOKEN_111 {common} --allowedTools \"mcp__pronova\" \"Skill\"\n" in right
    assert ': "${HUBSPOT_TOKEN_222:?set HUBSPOT_TOKEN_222 before launching the right side}"' in right
    for text in (left, right):
        assert text.startswith("#!/bin/sh\n") and 'cd "$(dirname "$0")"' in text
    for side in ("left", "right"):
        assert (home / side / "launch.sh").stat().st_mode & stat.S_IXUSR


def test_script_prompts_equal_the_answer_key(home, seed_dir, skills_dir):
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 0
    truth = make_truth()
    text = (home / "script.md").read_text()
    prompts = kit.parse_script(text)
    assert prompts[1] == truth["job1"]["prompt"]
    assert prompts[2] == truth["job4"]["prompt"]
    assert prompts[3] == truth["job3"]["prompt"]
    assert prompts[4] == truth["job5"]["prompt"]
    assert prompts[5] == f"The SILENT rule is now 7 days. {truth['job1']['prompt']}"
    assert "one side at a time" in text and "Run the write acts (3 and 4)" in text
    assert "`silent_days: 14` to `silent_days: 7`" in text
    assert "Key: 5 deals flagged (OVERDUE 3, SILENT 1, NO_NEXT 1)." in text
    assert "3 deals move to 2026-12-31" in text


def test_prepare_refuses_keys_for_another_day(home, seed_dir, skills_dir, capsys):
    (seed_dir / "out" / f"truth-left-{DAY}.json").write_text(json.dumps(make_truth("2026-11-09")), encoding="utf-8")
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 1
    assert "is not the key for" in capsys.readouterr().out


# --- restore --------------------------------------------------------------------------------------------------------


def end_state_for_write_acts() -> dict:
    def assoc(name):
        return {"deals": [name], "companies": ["Acme Corp"], "contacts": []}
    return {
        "tz": "Europe/Berlin", "stable": True,
        "deals_target": {"Acme Corp renewal": {"closedate": "2026-12-15T00:00:00Z", "amount": "24000", "deal_currency_code": "EUR"},
                         **{n: {"closedate": "2026-12-31T00:00:00Z"} for n in PAST_DUE}},
        "deals_changed": {"Acme Corp renewal": {"changes": {"closedate": {"from": "2026-11-28", "to": "2026-12-15"},
                                                            "amount": {"from": "85000", "to": "24000"}}},
                          **{n: {"changes": {"closedate": {"from": "2026-07-01", "to": "2026-12-31"}}} for n in PAST_DUE}},
        "contacts_changed": {}, "companies_changed": {}, "deals_missing": [], "deals_created": [],
        "created": {
            "meetings": [{"id": "1", "start": f"{DAY}T09:00:00Z", "associations": assoc("Acme Corp renewal")}],
            "notes": [{"id": "2", "body": "They will pilot", "associations": assoc("Acme Corp renewal")}],
            "tasks": [{"id": "3", "title": "Send the security doc", "due": "2026-11-13T09:00:00Z", "status": "NOT_STARTED",
                       "associations": assoc("Acme Corp renewal")},
                      *({"id": f"1{i}", "title": "Confirm timing", "body": "", "associations": assoc(n)}
                        for i, n in enumerate(PAST_DUE))],
            "calls": [], "emails": [],
            "contacts": [{"id": "9", "firstname": "Maria", "lastname": "Lopez", "jobtitle": "CFO", "email": "maria@example.com",
                          "associations": {"companies": ["Acme Corp"], "deals": []}}],
        },
    }


class FakeReader:
    def __init__(self, log: list[str], token: str, fail: bool = False) -> None:
        self.log, self.token, self.fail = log, token, fail
        self.snapshot: dict = {}

    def use_snapshot(self, snapshot):
        self.snapshot = snapshot

    def end_state(self, since, targets):
        self.log.append(f"end_state:{self.snapshot['portal']}:{since}:{len(targets)}")
        if self.fail:
            raise RuntimeError(f"read failed for {self.token}")
        state = end_state_for_write_acts()
        state["created"]["tasks"].append({"id": "401", "title": "seeded task",
                                          "associations": {"deals": [], "companies": [], "contacts": []}})
        state["created"]["meetings"].append({"id": "301", "start": f"{DAY}T08:00:00Z", "associations": {"deals": []}})
        state["contacts_changed"] = {EMAIL: {"name": "Jane Doe", "changes": {}}}
        state["counts"] = {"tasks": 5, "meetings": 2}
        return state


def do_restore(home, runner, log, *, yes=True, skip=False, fail_capture=False):
    def factory(token):
        return FakeReader(log, token, fail=fail_capture)
    argv = ["restore", "--home", str(home)] + (["--yes"] if yes else []) + (["--skip-capture"] if skip else [])
    return kit.main(argv, env=ENV, runner=runner, reader_factory=factory)


def test_restore_captures_end_states_before_restore_py(home, seed_dir, skills_dir):
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 0
    log: list[str] = []
    runner = FakeRunner(log=log)
    assert do_restore(home, runner, log) == 0
    assert log == ["end_state:left:2026-11-10T07:00:00.000Z:4", "end_state:right:2026-11-10T07:00:00.000Z:4",
                   "restore.py:left", "restore.py:right"]
    restore_calls = [c for c in runner.calls if Path(c[1]).name == "restore.py"]
    assert restore_calls[0][2:] == ["--portal", "left", "--expect-portal", LEFT_ID, "--token-env", "HUBSPOT_TOKEN_111",
                                    "--demo-day", DAY, "--apply", "--yes"]
    assert restore_calls[1][2:] == ["--portal", "right", "--expect-portal", RIGHT_ID, "--token-env", "HUBSPOT_TOKEN_222",
                                    "--demo-day", DAY, "--apply", "--yes"]
    saved = json.loads((home / "runs" / DAY / "endstate-left.json").read_text())
    assert saved["side"] == "left" and saved["since"] == "2026-11-10T07:00:00.000Z"
    es = saved["end_state"]
    assert "401" not in [t["id"] for t in es["created"]["tasks"]], "seeded engagement ids are dropped"
    assert [m["id"] for m in es["created"]["meetings"]] == ["1"]
    assert es["counts"] == {"tasks": 4, "meetings": 1}
    raw = (home / "runs" / DAY / "endstate-left.json").read_text()
    assert EMAIL not in raw and "maria@example.com" not in raw and "<email-1>" in raw
    assert json.loads((home / "state.json").read_text())["restored_at"]


def test_restore_without_yes_refuses(home, seed_dir, skills_dir, capsys):
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 0
    log: list[str] = []
    assert do_restore(home, FakeRunner(log=log), log, yes=False) == 2
    assert log == [] and "REFUSING" in capsys.readouterr().out


def test_failed_capture_stops_before_restore_py(home, seed_dir, skills_dir, capsys):
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 0
    log: list[str] = []
    runner = FakeRunner(log=log)
    assert do_restore(home, runner, log, fail_capture=True) == 1
    out = capsys.readouterr().out
    assert log == ["end_state:left:2026-11-10T07:00:00.000Z:4"]
    assert "Nothing was restored" in out and LEFT_TOKEN not in out
    assert runner.calls == []


def test_skip_capture_restores_without_reading(home, seed_dir, skills_dir):
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 0
    log: list[str] = []
    assert do_restore(home, FakeRunner(log=log), log, skip=True) == 0
    assert log == ["restore.py:left", "restore.py:right"]
    assert not (home / "runs").exists()


def test_restore_tries_both_portals_even_when_one_fails(home, seed_dir, skills_dir, capsys):
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 0
    log: list[str] = []
    assert do_restore(home, FakeRunner(fail=("restore.py", "left"), log=log), log, skip=True) == 1
    assert log == ["restore.py:left", "restore.py:right"]
    out = capsys.readouterr().out
    assert "Restore failed on: left" in out and LEFT_TOKEN not in out
    assert json.loads((home / "state.json").read_text())["restored_at"] is None


# --- the snapshot reader --------------------------------------------------------------------------------------------


def portal_transport(closedate: str = "2026-11-28T00:00:00Z") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert request.method == "GET" or path.endswith("/batch/read"), "only reads"
        if path == "/crm/v3/pipelines/deals":
            return httpx.Response(200, json={"results": [{"id": "default", "label": "Sales Pipeline",
                                                          "stages": [{"id": "st_neg", "label": "Negotiation"}]}]})
        if path.endswith("/deals/batch/read"):
            return httpx.Response(200, json={"results": [{"id": "9001", "properties": {
                "dealname": "Acme Corp renewal", "amount": "85000.0", "closedate": closedate, "dealstage": "st_neg",
                "pipeline": "default", "hs_next_step": "Send revised order form", "deal_currency_code": "EUR",
                "description": "added later by HubSpot", "dealtype": "newbusiness"}}]})
        if path.endswith("/contacts/batch/read"):
            return httpx.Response(200, json={"results": [{"id": "7001", "properties": {
                "firstname": "Jane", "lastname": "Doe", "email": EMAIL, "phone": "+1 555 0100", "jobtitle": "VP",
                "hubspot_owner_id": "5"}}]})
        if path.endswith("/companies/batch/read"):
            return httpx.Response(200, json={"results": [{"id": "5001", "properties": {
                "name": "Acme Corp", "domain": "acme.example.com", "hubspot_owner_id": "5"}}]})
        raise AssertionError(f"unexpected request {request.method} {path}")
    return httpx.MockTransport(handler)


def test_snapshot_reader_baseline_matches_an_untouched_portal():
    reader = kit.SnapshotReader("tok-fake", transport=portal_transport())
    reader.use_snapshot(make_snapshot())
    baseline = reader.baseline_data
    assert set(baseline["deals"]) == {"Acme Corp renewal"}
    assert baseline["deals"]["Acme Corp renewal"]["props"]["dealstage"] == "Negotiation"
    assert "description" not in baseline["deals"]["Acme Corp renewal"]["props"]
    assert set(baseline["contacts"]) == {EMAIL}
    assert reader.verify_baseline() == {"ok": True, "differs": [], "unrestorable": []}


def test_snapshot_reader_sees_a_changed_close_date():
    reader = kit.SnapshotReader("tok-fake", transport=portal_transport(closedate="2026-12-15T00:00:00Z"))
    reader.use_snapshot(make_snapshot())
    assert reader.verify_baseline()["differs"] == ["deals:Acme Corp renewal"]


# --- transcripts and compare ----------------------------------------------------------------------------------------


class Transcript:
    """Builds a Claude Code transcript with placeholder content; every record advances a clock by a chosen number of seconds."""

    def __init__(self, start: datetime, session: str = "s1") -> None:
        self.now = start
        self.lines: list[dict] = []
        self.counter = 0
        self.session = session

    def _stamp(self, after: float) -> str:
        self.now += timedelta(seconds=after)
        return self.now.isoformat().replace("+00:00", "Z")

    def noise(self, after: float = 0.1) -> None:
        self.lines.append({"type": "attachment", "timestamp": self._stamp(after)})

    def prompt(self, text: str, after: float = 1) -> None:
        self.lines.append({"type": "user", "timestamp": self._stamp(after), "message": {"role": "user", "content": text}})

    def assistant(self, blocks: list[dict], usage: tuple[int, int, int, int], after: float = 1, *, split: bool = False,
                  model: str = "claude-sonnet-5-5", drop: str | None = None) -> None:
        self.counter += 1
        msg_id = f"msg_{self.session}_{self.counter}"
        keys = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
        u = dict(zip(keys, usage, strict=True))
        if drop:
            del u[drop]
        pieces = [[b] for b in blocks] if split else [blocks]
        for i, piece in enumerate(pieces):
            self.lines.append({"type": "assistant", "timestamp": self._stamp(after if i == 0 else 0.01),
                               "message": {"id": msg_id, "model": model, "usage": u, "content": piece}})

    def tool(self, tool_id: str, name: str) -> dict:
        return {"type": "tool_use", "id": tool_id, "name": name, "input": {}}

    def result(self, tool_id: str, after: float, error: bool = False) -> None:
        self.lines.append({"type": "user", "timestamp": self._stamp(after), "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": "ok", "is_error": error}]}})

    def text(self, body: str) -> dict:
        return {"type": "text", "text": body}

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(line) for line in self.lines) + "\n", encoding="utf-8")
        return path


TRIAGE_ANSWER = (f"PIPELINE PULSE\n\n━━ NEEDS ATTENTION\n• Acme Corp renewal — no contact in 35 days (SILENT)\n"
                 f"• Initech upgrade — no next step\nOwner {EMAIL}\n\n━━ CLOSE DATES PASSED (3)\n"
                 + "\n".join(f"• {n}" for n in PAST_DUE[:2]) + "\n")
FORECAST_ANSWER = ("FORECAST — Q4 2026\nClosed won: EUR 27,000\nCommit: EUR 85,000\nBest case: EUR 120.5k\n"
                   "Pipeline: EUR 640,000\nWeighted: EUR 310,250\nSupported commit: EUR 99,000\n━━ AT RISK\n• Acme Corp renewal\n")


def build_right(path: Path, start: datetime) -> Path:
    t = Transcript(start, "right")
    t.prompt("❯ Which of my deals need attention today, and why?", 1)
    t.assistant([t.tool("r1", "mcp__pronova__pronova_triage_pipeline")], (10, 100, 1000, 50), 2)
    t.result("r1", 3)
    t.assistant([t.text(TRIAGE_ANSWER)], (10, 0, 1200, 400), 2)                      # act 1: wall 7 s
    t.prompt("What will I close this quarter, and what's at risk?", 12)
    t.assistant([t.text(FORECAST_ANSWER)], (8, 50, 1100, 300), 4)
    t.prompt("Just finished with Acme: they'll pilot, close moves to Dec 15, amount EUR 24k.", 20)
    t.assistant([t.tool("r2", "mcp__pronova__pronova_log_meeting_outcome"), t.text("Log it?")], (5, 0, 900, 30), 4, split=True)
    t.result("r2", 2)
    t.prompt("yes", 10)
    t.assistant([t.tool("r3", "mcp__pronova__pronova_apply_changes")], (5, 0, 950, 20), 2)
    t.result("r3", 45)                                                                  # form waited 45 s
    t.assistant([t.text("Logged.")], (5, 0, 1000, 10), 1)
    t.prompt("Move every past-due close date on my open deals to the end of next month and add a task on each to confirm timing.", 15)
    t.assistant([t.tool("r4", "mcp__pronova__pronova_fix_past_due_close_dates")], (5, 0, 900, 30), 3)
    t.result("r4", 2, error=True)
    t.assistant([t.text("Apply?")], (5, 0, 1000, 10), 1)
    t.prompt("confirmed", 5)
    t.assistant([t.tool("r5", "mcp__pronova__pronova_apply_changes")], (5, 0, 950, 20), 2)
    t.result("r5", 60)                                                                  # form waited 60 s
    t.assistant([t.text("Done.")], (5, 0, 1000, 10), 1)
    t.prompt("The SILENT rule is now 7 days. Which of my deals need attention today, and why?", 30)
    t.assistant([t.text("Nothing new.")], (4, 0, 1000, 5), 2)
    return t.write(path)


def build_left(path: Path, start: datetime) -> Path:
    t = Transcript(start, "left")
    t.prompt("Which of my deals need attention today, and why?", 1)
    for i in range(3):
        t.assistant([t.tool(f"l{i}", "mcp__hubspot-bench__search_crm_objects")], (10, 0, 2000, 40), 2)
        t.result(f"l{i}", 2)
    t.assistant([t.text("Acme Corp renewal needs attention. Initech upgrade too.")], (10, 0, 4000, 500), 3)
    t.prompt("Move every past-due close date on my open deals to the end of next month and add a task on each to confirm timing.", 20)
    t.assistant([t.tool("lw", "mcp__hubspot-bench__manage_crm_objects")], (10, 0, 4000, 200), 5)
    t.result("lw", 3)
    t.assistant([t.text("Done.")], (10, 0, 4500, 100), 2)
    return t.write(path)


@pytest.fixture
def prepared(home, seed_dir, skills_dir):
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 0
    # state.json carries the real clock; pin it so the fake transcripts can sit before or after it.
    state = json.loads((home / "state.json").read_text())
    state["prepared_at"] = PREPARED.isoformat()
    (home / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return home


def save_end_states(home: Path, end_state: dict | None = None, sides=("left", "right"),
                    captured_at: datetime = PREPARED + timedelta(hours=1)) -> None:
    for side in sides:
        path = home / "runs" / DAY / f"endstate-{side}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"side": side, "captured_at": captured_at.isoformat(),
                                    "end_state": end_state or end_state_for_write_acts()}), encoding="utf-8")


def seed_transcripts(home: Path, projects: Path) -> None:
    build_right(projects / kit.project_dir_name(home / "right") / "r.jsonl", PREPARED + timedelta(minutes=5))
    build_left(projects / kit.project_dir_name(home / "left") / "l.jsonl", PREPARED + timedelta(minutes=5))


def run_compare(home: Path, projects: Path) -> int:
    return kit.main(["compare", "--home", str(home), "--projects-dir", str(projects)], env={})


def row(report: str, act_title: str, side: str) -> list[str]:
    for line in report.splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) > 2 and cells[0] == act_title and cells[1] == side:
            return cells
    raise AssertionError(f"no row for {act_title!r} {side!r}")


def quality(report: str, act_title: str, side: str) -> list[str]:
    return row(report.split("## Quality against the answer key")[1], act_title, side)


def test_compare_builds_the_table_from_fixture_transcripts(prepared, tmp_path, capsys):
    projects = tmp_path / "projects"
    seed_transcripts(prepared, projects)
    # an earlier session in the same folder (before prepare) is not part of the demo
    build_left(projects / kit.project_dir_name(prepared / "left") / "old.jsonl", PREPARED - timedelta(hours=3))
    save_end_states(prepared)

    assert run_compare(prepared, projects) == 0
    out = capsys.readouterr().out
    report = (prepared / f"report-{DAY}.md").read_text()
    assert report.strip() in out

    # Act 1, right: 2 model calls (the tool-use and the answer), 1 HubSpot call, 7 s wall, context 1,110 -> 1,210
    a1r = row(report, "1 Triage", "right")
    assert a1r[2:9] == ["1", "7.0s", "0.0s", "7.0s", "2", "1", "20"]
    assert a1r[9:14] == ["100", "2,200", "450", "1,110", "1,210"]
    assert a1r[14] == "$0.0054" and a1r[15] == "0"  # (20*2 + 100*4 + 2200*0.2 + 450*10) / 1e6 at sonnet-5.5 list prices
    # Act 1, left: three searches and the answer, no form
    a1l = row(report, "1 Triage", "left")
    assert a1l[4] == "0.0s" and a1l[6:8] == ["4", "3"]
    # Act 3, right: two turns (the "yes" joins the act); the apply form waited 45 s; 2 HubSpot calls
    a3r = row(report, "3 Log the meeting", "right")
    assert a3r[2] == "2" and a3r[4] == "45.0s" and a3r[7] == "2"
    # Act 4, right: the form waited 60 s; the preview call errored once
    a4r = row(report, "4 Bulk fix of past-due close dates", "right")
    assert a4r[2] == "2" and a4r[4] == "60.0s" and a4r[15] == "1"
    assert a4r[3] == "69.0s" and a4r[5] == "9.0s"
    # Acts the left side never ran
    assert row(report, "3 Log the meeting", "left")[2] == "not run"
    assert row(report, "5 Rule edit, then triage again", "left")[2] == "not run"
    # the pre-prepare session is excluded: one file per side
    assert "left: 1 transcript file(s), 2 turn(s)." in report
    assert "right: 1 transcript file(s), 7 turn(s)." in report
    assert "## Totals" in report


def test_compare_grades_against_the_answer_key(prepared, tmp_path):
    projects = tmp_path / "projects"
    seed_transcripts(prepared, projects)
    save_end_states(prepared)
    assert run_compare(prepared, projects) == 0
    report = (prepared / f"report-{DAY}.md").read_text()

    # triage: the right side names 4 of 5 flagged deals, codes from line text and from the section header
    assert quality(report, "1 Triage", "right")[2:4] == ["0.80", "deals named 4/5, codes 4/5; missing e.g. Past Due Three"]
    assert quality(report, "1 Triage", "left")[2:4] == ["0.40", "deals named 2/5, codes 0/5; missing e.g. Past Due One, Past Due Two, Past Due Three"]
    # write acts: graded from the end state, one capture split by association
    assert quality(report, "3 Log the meeting", "right")[2:4] == ["1.00 exact", "failed checks: none; violations: 0"]
    assert quality(report, "4 Bulk fix of past-due close dates", "right")[2:4] == ["1.00 exact", "failed checks: none; violations: 0"]
    assert quality(report, "4 Bulk fix of past-due close dates", "left")[2] == "1.00 exact"
    # forecast: five of six figures match; supported commit (99,000 against 58,000) does not
    f = quality(report, "2 Forecast", "right")
    assert f[2] == "5/6" and "off: supported_commit" in f[3] and "at-risk deals named 1/1" in f[3]
    assert quality(report, "2 Forecast", "left")[2] == "not run"
    assert quality(report, "5 Rule edit, then triage again", "right")[2] == "not graded"
    assert LEFT_TOKEN not in report and RIGHT_TOKEN not in report and EMAIL not in report


def test_compare_masks_emails_that_reach_the_report(prepared, tmp_path):
    projects = tmp_path / "projects"
    seed_transcripts(prepared, projects)
    es = end_state_for_write_acts()
    es["contacts_changed"] = {"k": {"name": f"Jane Doe ({EMAIL})", "changes": {}}}
    save_end_states(prepared, es)
    assert run_compare(prepared, projects) == 0
    report = (prepared / f"report-{DAY}.md").read_text()
    assert "existing contact modified: Jane Doe (<email>)" in report and EMAIL not in report


def test_compare_without_an_end_state_says_so(prepared, tmp_path):
    projects = tmp_path / "projects"
    seed_transcripts(prepared, projects)
    assert run_compare(prepared, projects) == 0
    report = (prepared / f"report-{DAY}.md").read_text()
    assert quality(report, "4 Bulk fix of past-due close dates", "left")[2] == "no end state"


def test_compare_fails_loudly_when_no_transcript_started_after_prepare(prepared, tmp_path, capsys):
    projects = tmp_path / "projects"
    build_right(projects / kit.project_dir_name(prepared / "right") / "r.jsonl", PREPARED - timedelta(hours=1))
    build_left(projects / kit.project_dir_name(prepared / "left") / "l.jsonl", PREPARED + timedelta(minutes=5))
    assert run_compare(prepared, projects) == 2
    assert "no right transcript" in capsys.readouterr().err
    assert not (prepared / f"report-{DAY}.md").exists()


@pytest.mark.parametrize("missing", ["cache_read_input_tokens", "output_tokens"])
def test_compare_fails_loudly_on_a_missing_usage_field(prepared, tmp_path, capsys, missing):
    projects = tmp_path / "projects"
    t = Transcript(PREPARED + timedelta(minutes=5))
    t.prompt("Which of my deals need attention today, and why?")
    t.assistant([t.text("x")], (1, 2, 3, 4), drop=missing)
    t.write(projects / kit.project_dir_name(prepared / "left") / "l.jsonl")
    build_right(projects / kit.project_dir_name(prepared / "right") / "r.jsonl", PREPARED + timedelta(minutes=5))
    assert run_compare(prepared, projects) == 2
    err = capsys.readouterr().err
    assert f"usage lacks {missing}" in err and "l.jsonl line 2" in err


def test_compare_refuses_a_day_that_was_not_prepared(prepared, tmp_path, capsys):
    code = kit.main(["compare", "--home", str(prepared), "--day", "2026-11-11", "--projects-dir", str(tmp_path)], env={})
    assert code == 2 and "state.json is for 2026-11-10" in capsys.readouterr().err


def test_parse_transcript_requires_timestamps_and_valid_json(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"content": "hello"}}) + "\n", encoding="utf-8")
    with pytest.raises(kit.TranscriptError, match="no timestamp"):
        kit.parse_transcript(path)
    path.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(kit.TranscriptError, match="not JSON"):
        kit.parse_transcript(path)


def test_parse_transcript_counts_a_split_response_once_and_skips_sidechains(tmp_path):
    t = Transcript(PREPARED)
    t.prompt("Which of my deals need attention today, and why?")
    t.assistant([t.tool("x1", "mcp__pronova__pronova_triage_pipeline"), t.text("hello")], (7, 8, 9, 10), split=True)
    t.lines.append({"type": "assistant", "isSidechain": True, "timestamp": t._stamp(1), "message": {"id": "side"}})
    t.noise()
    (turn,) = kit.parse_transcript(t.write(tmp_path / "t.jsonl"))
    assert turn.calls == [{"input": 7, "cache_write": 8, "cache_read": 9, "output": 10}]
    assert [x.name for x in turn.tools] == ["mcp__pronova__pronova_triage_pipeline"]


def test_assign_acts_matches_prompts_and_attaches_followups():
    prompts = {1: "Which of my deals need attention today, and why?",
               5: "The SILENT rule is now 7 days. Which of my deals need attention today, and why?"}

    def turn(text):
        return kit.Turn(prompt=text, start=PREPARED, end=PREPARED)
    turns = [turn("hello there"), turn("❯ Which of my deals need attention today, and why?"), turn("yes"),
             turn("The SILENT rule is now 7 days. Which of my deals need attention today, and why?")]
    acts, unmapped = kit.assign_acts(turns, prompts)
    assert unmapped == 1
    assert [len(acts[1]), len(acts[5])] == [2, 1]


# --- grading helpers ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("EUR 27,000", 27000), ("1.234.567,5", 1234567.5), ("1,234,567.5", 1234567.5), ("27k", 27000),
    ("EUR 1.2M", 1200000), ("85000", 85000), ("27 months", 27)])
def test_numbers_in_reads_common_formats(text, expected):
    assert any(abs(v - expected) < 1e-6 for v in kit.numbers_in(text))


def test_numbers_in_does_not_apply_a_suffix_inside_a_word():
    assert not any(abs(v - 27e6) < 1 for v in kit.numbers_in("27 months"))


def test_grade_forecast_uses_the_keys_tolerance():
    truth = make_truth()
    base = "Closed won 27,000; commit {c}; best case 120,500; pipeline 640,000; weighted 310,250; supported 58,000"
    within = kit.grade_forecast(base.format(c="85,200"), truth)  # 0.24% off: inside +/-0.5%
    assert within["passed"] == 6 and within["tolerance"] == pytest.approx(0.005)
    outside = kit.grade_forecast(base.format(c="85,600"), truth)  # 0.7% off
    assert outside["figures"]["commit"] is False and outside["passed"] == 5


def test_grade_triage_reads_codes_from_headers_and_lines():
    result = kit.grade_triage(TRIAGE_ANSWER, make_truth())
    assert (result["found"], result["total"], result["codes_matched"], result["codes_total"]) == (4, 5, 4, 5)
    assert result["missing"] == ["Past Due Three"]


def test_split_end_state_attributes_records_by_association():
    es = end_state_for_write_acts()
    views = kit.split_end_state(es, make_truth())
    assert sorted(views["job3"]["deals_changed"]) == ["Acme Corp renewal"]
    assert sorted(views["job5"]["deals_changed"]) == sorted(PAST_DUE)
    assert [t["id"] for t in views["job3"]["created"]["tasks"]] == ["3"]
    assert len(views["job5"]["created"]["tasks"]) == 3 and views["job5"]["created"]["contacts"] == []
    assert len(views["job3"]["created"]["contacts"]) == 1
    es["deals_changed"]["Unrelated Deal"] = {"changes": {"amount": {"from": 1, "to": 2}}}
    both = kit.split_end_state(es, make_truth())
    assert "Unrelated Deal" in both["job3"]["deals_changed"] and "Unrelated Deal" in both["job5"]["deals_changed"]
    assert kit.grade_write("job3", es, make_truth())["violations"] == ["other deal changed: Unrelated Deal"]


def test_grade_write_scores_a_perfect_capture_and_a_missed_deal():
    truth = make_truth()
    assert kit.grade_write("job3", end_state_for_write_acts(), truth)["exact"] is True
    assert kit.grade_write("job5", end_state_for_write_acts(), truth)["exact"] is True
    es = end_state_for_write_acts()
    del es["deals_changed"][PAST_DUE[0]]
    es["deals_target"][PAST_DUE[0]]["closedate"] = "2026-07-01T00:00:00Z"
    es["created"]["tasks"] = [t for t in es["created"]["tasks"] if t["associations"]["deals"] != [PAST_DUE[0]]]
    partial = kit.grade_write("job5", es, truth)
    assert partial["exact"] is False and partial["score"] == pytest.approx(4 / 6)


def test_mask_tree_masks_values_and_numbers_keys():
    masked = kit.mask_tree({"a@example.com": {"x": "write to b@example.com"}, "c@example.com": 1, "plain": ["d@example.com"]})
    assert masked == {"<email-1>": {"x": "write to <email>"}, "<email-2>": 1, "plain": ["<email>"]}


def test_redact_hides_token_values_and_emails():
    text = kit.redact(f"{LEFT_TOKEN} failed for {EMAIL}", ENV, ["HUBSPOT_TOKEN_111", "HUBSPOT_TOKEN_222"])
    assert text == "<redacted> failed for <email>"


# --- review fixes: ordering, guards, evidence ---------------------------------------------------------------------


def test_the_read_acts_with_keys_run_before_the_write_acts():
    assert [(n, job, kind) for n, _, job, kind in kit.ACTS] == [
        (1, "job1", "read"), (2, "job4", "read"), (3, "job3", "write"), (4, "job5", "write"), (5, "job1", "read")]
    assert kit.WRITE_ACTS == {3: "job3", 4: "job5"}


def test_the_script_confirms_the_left_login_before_act_one(home, seed_dir, skills_dir):
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 0
    text = (home / "script.md").read_text()
    assert text.index("## Before Act 1") < text.index("## Act 1")
    assert "get_organization_details" in text and f"must read {LEFT_ID}" in text
    assert "Key (2026-Q4, EUR, seeded state):" in text


def test_check_names_the_failing_check_and_lists_warnings(home, seed_dir, capsys):
    bad = json.dumps({"checks": [{"name": "currency", "status": "fail", "detail": "company currency is USD"}]})
    warn = json.dumps({"checks": [{"name": "scopes", "status": "warn", "detail": "write scopes cannot be probed"}]})
    runner = FakeRunner(outputs={("preflight.py", "left"): warn, ("preflight.py", "right"): "{}"})
    assert kit.main(["check", "--home", str(home), "--day", DAY, "--seed-dir", str(seed_dir)], env=ENV, runner=runner) == 0
    out = capsys.readouterr().out
    assert "READY (1 warning(s))" in out and "warning: left scopes: write scopes cannot be probed" in out

    def runner_bad(argv, env):
        return kit.RunResult(1, bad) if "right" in argv else kit.RunResult(0, "{}")
    assert kit.main(["check", "--home", str(home), "--day", DAY, "--seed-dir", str(seed_dir)], env=ENV, runner=runner_bad) == 1
    assert "currency: company currency is USD" in capsys.readouterr().out


def test_check_is_not_ready_without_the_seed_owner_variables(home, seed_dir, capsys):
    env = {k: v for k, v in ENV.items() if not k.startswith("SEED_")}
    runner = FakeRunner(outputs={("preflight.py", "left"): "{}", ("preflight.py", "right"): "{}"})
    assert kit.main(["check", "--home", str(home), "--day", DAY, "--seed-dir", str(seed_dir)], env=env, runner=runner) == 2
    out = capsys.readouterr().out
    assert "SEED_DEMO_USER_EMAIL" in out and "SEED_OWNER_EMAIL_BASE" in out and "NOT READY" in out


@pytest.mark.parametrize("drop,value", [("SEED_DEMO_USER_EMAIL", None), ("SEED_OWNER_EMAIL_BASE", "no-at-sign")])
def test_prepare_refuses_before_any_call_without_the_seed_owner_variables(home, seed_dir, skills_dir, capsys, drop, value):
    env = {k: v for k, v in ENV.items() if k != drop}
    if value:
        env[drop] = value
    runner = FakeRunner()
    assert do_prepare(home, seed_dir, skills_dir, runner, env=env) == 2
    assert runner.calls == [] and drop in capsys.readouterr().out


def test_prepare_runs_the_preflight_on_both_sides_and_resets_nothing_when_one_fails(home, seed_dir, skills_dir, capsys):
    (home / "state.json").write_text(json.dumps({"day": "2026-11-09"}), encoding="utf-8")
    bad = json.dumps({"checks": [{"name": "team", "status": "fail", "detail": f"missing for {EMAIL}"}]})
    calls = []

    def runner(argv, env):
        name = (Path(argv[1]).name, argv[argv.index("--portal") + 1])
        calls.append(name)
        return kit.RunResult(1, bad) if name == ("preflight.py", "right") else kit.RunResult(0, "{}")
    assert do_prepare(home, seed_dir, skills_dir, runner) == 1
    out = capsys.readouterr().out
    assert ("reset.py", "left") not in calls and ("reset.py", "right") not in calls
    assert "REFUSING: the preflight failed" in out and "team: missing for <email>" in out and EMAIL not in out
    assert (home / "state.json").exists(), "nothing was reset, so the old state still describes the portals"


def test_prepare_tolerates_a_failed_leftovers_check_because_reset_clears_it(home, seed_dir, skills_dir):
    left_over = json.dumps({"checks": [{"name": "leftovers", "status": "fail", "detail": "2 records not in the plan"},
                                       {"name": "portal", "status": "ok", "detail": "fine"}]})
    runner = FakeRunner(outputs={("preflight.py", "left"): left_over, ("preflight.py", "right"): left_over})
    original = runner.__call__

    def with_exit_one(argv, env):
        result = original(argv, env)
        return kit.RunResult(1, result.stdout) if Path(argv[1]).name == "preflight.py" else result
    assert do_prepare(home, seed_dir, skills_dir, with_exit_one) == 0


@pytest.mark.parametrize("failing", [("reset.py", "left"), ("seed.py", "right")])
def test_prepare_drops_the_old_state_before_the_first_reset(home, seed_dir, skills_dir, failing):
    (home / "state.json").write_text(json.dumps({"day": "2026-11-09"}), encoding="utf-8")

    def runner(argv, env):
        name = (Path(argv[1]).name, argv[argv.index("--portal") + 1])
        applying = "--apply" in argv or name[0] == "reset.py"
        return kit.RunResult(3 if name == failing and applying else 0)
    assert do_prepare(home, seed_dir, skills_dir, runner) == 1
    assert not (home / "state.json").exists()
    with pytest.raises(kit.KitError, match="prepare"):
        kit.load_state(home)


def test_prepare_refuses_portals_that_were_not_seeded_identically(home, seed_dir, skills_dir, capsys):
    write_manifest(seed_dir, "right", "2026-11-10-070500", team=None, deals={"D0": "1"})
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 1
    out = capsys.readouterr().out
    assert "not seeded identically" in out and "deals: left 5, right 1" in out and "team: left True, right False" in out
    assert not (home / "state.json").exists()


def test_prepare_uses_the_newest_manifest_of_the_day(home, seed_dir, skills_dir):
    write_manifest(seed_dir, "right", "2026-11-10-060000", deals={"D0": "1"})  # older, differs
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 0


def captured_text(home: Path, side: str) -> str:
    return (home / "runs" / DAY / f"endstate-{side}.json").read_text()


def test_a_repeated_restore_keeps_the_end_states_it_already_saved(home, seed_dir, skills_dir, capsys):
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 0
    log: list[str] = []
    assert do_restore(home, FakeRunner(log=log), log) == 0
    saved = {side: captured_text(home, side) for side in kit.SIDES}
    # the second run sees a clean portal; it must not read it again
    log.clear()
    assert do_restore(home, FakeRunner(log=log), log) == 0
    assert log == ["restore.py:left", "restore.py:right"]
    assert {side: captured_text(home, side) for side in kit.SIDES} == saved
    assert "keeping the end state of left" in capsys.readouterr().out


def test_recapture_reads_the_portal_again(home, seed_dir, skills_dir):
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 0
    log: list[str] = []
    assert do_restore(home, FakeRunner(log=log), log) == 0
    log.clear()
    argv = ["restore", "--home", str(home), "--yes", "--recapture"]
    assert kit.main(argv, env=ENV, runner=FakeRunner(log=log),
                    reader_factory=lambda token: FakeReader(log, token)) == 0
    assert [e for e in log if e.startswith("end_state")] == ["end_state:left:2026-11-10T07:00:00.000Z:4",
                                                             "end_state:right:2026-11-10T07:00:00.000Z:4"]


def test_a_saved_end_state_from_before_this_prepare_is_captured_again(home, seed_dir, skills_dir):
    assert do_prepare(home, seed_dir, skills_dir, FakeRunner()) == 0
    save_end_states(home, captured_at=datetime(2020, 1, 1, tzinfo=UTC))
    log: list[str] = []
    assert do_restore(home, FakeRunner(log=log), log) == 0
    assert [e for e in log if e.startswith("end_state")] and "2020" not in captured_text(home, "left")


def test_compare_ignores_an_end_state_captured_before_the_prepare(prepared, tmp_path):
    projects = tmp_path / "projects"
    seed_transcripts(prepared, projects)
    save_end_states(prepared, captured_at=PREPARED - timedelta(hours=5))
    assert run_compare(prepared, projects) == 0
    report = (prepared / f"report-{DAY}.md").read_text()
    assert quality(report, "3 Log the meeting", "right")[2] == "no end state"
    assert "captured before this prepare and is ignored" in report


def test_compare_accepts_today_for_the_day(prepared, tmp_path):
    projects = tmp_path / "projects"
    seed_transcripts(prepared, projects)
    state = json.loads((prepared / "state.json").read_text())
    state["day"] = kit.date.today().isoformat()
    (prepared / "state.json").write_text(json.dumps(state), encoding="utf-8")
    argv = ["compare", "--home", str(prepared), "--day", "today", "--projects-dir", str(projects)]
    assert kit.main(argv, env={}) == 0
    assert kit.main(argv[:-2] + ["--day", "tomorrow", "--projects-dir", str(projects)], env={}) == 2


def test_compare_warns_when_the_left_transcript_never_names_the_left_portal(prepared, tmp_path):
    projects = tmp_path / "projects"
    seed_transcripts(prepared, projects)
    save_end_states(prepared)
    assert run_compare(prepared, projects) == 0
    assert f"no left transcript record names portal {LEFT_ID}" in (prepared / f"report-{DAY}.md").read_text()
    left = next((projects / kit.project_dir_name(prepared / "left")).glob("*.jsonl"))
    left.write_text(left.read_text() + json.dumps({"type": "attachment", "note": f"portalId {LEFT_ID}"}) + "\n")
    assert run_compare(prepared, projects) == 0
    assert "names portal" not in (prepared / f"report-{DAY}.md").read_text()


def test_a_local_command_after_the_last_answer_does_not_stretch_the_last_turn(tmp_path):
    t = Transcript(PREPARED)
    t.prompt("Which of my deals need attention today, and why?")
    t.assistant([t.tool("x1", "mcp__pronova__pronova_triage_pipeline")], (1, 1, 1, 1), 2)
    t.result("x1", 3)
    t.assistant([t.text("Answer")], (1, 1, 1, 1), 2)
    t.lines.append({"type": "user", "timestamp": t._stamp(600), "message": {
        "role": "user", "content": "<command-name>/exit</command-name>"}})
    t.lines.append({"type": "user", "timestamp": t._stamp(0.1), "isMeta": True, "message": {
        "role": "user", "content": [{"type": "text", "text": "<local-command-caveat>Caveat</local-command-caveat>"}]}})
    (turn,) = kit.parse_transcript(t.write(tmp_path / "t.jsonl"))
    assert (turn.end - turn.start).total_seconds() == pytest.approx(1 + 2 + 3 + 2 - 1)


def test_a_repeated_act_prompt_replaces_the_first_take():
    prompts = {1: "Which of my deals need attention today, and why?"}

    def turn(text, minute):
        when = PREPARED + timedelta(minutes=minute)
        return kit.Turn(prompt=text, start=when, end=when, final_text=text)
    first, second, followup = turn(prompts[1], 0), turn(prompts[1], 5), turn("yes", 6)
    retakes: dict[int, int] = {}
    acts, _ = kit.assign_acts([first, second, followup], prompts, retakes)
    assert acts[1] == [second, followup] and retakes == {1: 1}
    assert kit.act_stats(acts[1]).answer_text == second.final_text


def test_the_shipping_allowlist_admits_the_demo_folder():
    script = (kit.REPO / "scripts" / "check-artifact-allowlist.sh").read_text(encoding="utf-8")
    regex = re.search(r"allow_regex='(.*)'", script).group(1)
    assert re.match(regex, "demo/kit.py") and re.match(regex, "demo/README.md")
