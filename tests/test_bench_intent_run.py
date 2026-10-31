"""bench/intent_run.py: the intent benchmark's runner, driven by fakes (no claude, no HubSpot, no server).

Pins what the benchmark depends on: each arm's configuration, a clean environment, per-arm skills isolation with a
leak check, one shared prompt, the end-state read and restore around write tasks, the failure classes and the
single upstream re-run. Placeholder data only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

BENCH = Path(__file__).resolve().parent.parent / "bench"
sys.path.insert(0, str(BENCH))
import intent_run as ir  # noqa: E402

TRUTH_FILE = Path("/Users/izzy/Documents/hubspot-forecast/demo/seed/out/truth-enterprise-2026-11-10.json")
SECRET = "s3cret-bearer-value"
SECRET_ENV = "HUBSPOT_MCP_SERVER_SECRET"
JOBS = ir.load_jobs(BENCH / "jobs.jsonl")
JOB = {j["id"]: j for j in JOBS}
DEALS = ["Acme Corp renewal", "Globex expansion", "Initech pilot"]


def make_truth() -> dict:
    truth = {"meta": {"as_of": "2026-11-10"}, "job5": {"deals": [{"dealname": n} for n in DEALS]}}
    for job in JOBS:
        node = truth
        *parents, leaf = job["truth_key"].split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node.setdefault(leaf, {})["prompt"] = job["prompt"]
    truth["job3"]["target_deal"] = {"dealname": "Acme Corp renewal"}
    truth["offscript"]["globex_follow_up_task"]["target_deal"] = {"dealname": "Globex expansion"}
    return truth


def make_skills(root: Path, names: list[str]) -> Path:
    for name in names:
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: placeholder {name}\n---\nBody.\n", encoding="utf-8")
    return root


@pytest.fixture
def ctx(tmp_path):
    skills_b = make_skills(tmp_path / "skills-b", ["deal-management", "crm-lookup"])
    skills_cd = make_skills(tmp_path / "skills-cd", ["triage-format", "forecast-format"])
    out = tmp_path / "run"
    out.mkdir()
    (out / "sales-rules.md").write_text("rules", encoding="utf-8")
    env = {
        "PATH": "/usr/bin", "HOME": "/home/x", "CLAUDE_CODE_SUBAGENT_MODEL": "claude-sonnet-5-5",
        "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1", "HUBSPOT_TOKEN_149614891": "tok-test", "HUBSPOT_TOKEN_222": "tok-two",
        "HUBSPOT_PRIVATE_APP_TOKEN": "tok-priv", "SEED_DEMO_USER_EMAIL": "demo@example.com", SECRET_ENV: SECRET,
        "AI_GATEWAY_API_KEY": "gw-key",
    }
    return ir.Ctx(
        out=out, truth=make_truth(), secret=SECRET, rules_path=out / "sales-rules.md", skills_b=skills_b, skills_cd=skills_cd,
        expected_skills={"A": set(), "B": {"deal-management", "crm-lookup"}, "C": {"triage-format", "forecast-format"},
                         "D": {"triage-format", "forecast-format"}},
        base_env=env, rerun_wait=0.0, sleep=lambda s: None, cli_dir="/opt/hubspot/bin",
    )


def answer_text(task_id: str) -> str:
    return "All done.\n```json\n" + JOB[task_id]["answer_example"] + "\n```"


def tool_pair(i: int, name: str, tool_input: dict, result: str = "{}", is_error: bool = False) -> list[dict]:
    return [
        {"type": "assistant", "message": {"usage": {}, "content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": tool_input}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "is_error": is_error,
                                                  "content": [{"type": "text", "text": result}]}]}},
    ]


def events_for(arm: str, ctx, *, text: str, tools: list[tuple] = (), skills_extra=(), mcp_status="connected",
               builtin_tools=None, model="claude-sonnet-5-5", skills=None) -> list[dict]:
    spec = ir.ARMS[arm]
    init = {"type": "system", "subtype": "init", "model": model,
            "tools": sorted(spec["builtin_tools"]) if builtin_tools is None else builtin_tools,
            "mcp_servers": [{"name": spec["server"], "status": mcp_status}],
            "skills": sorted(ir.BUILTIN_SKILLS | ctx.expected_skills[arm] | set(skills_extra)) if skills is None else skills}
    events = [init]
    for i, t in enumerate(tools):
        events += tool_pair(i, *t)
    events.append({"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "duration_ms": 5,
                   "total_cost_usd": 0.01, "result": text})
    return events


class FakeClaude:
    """claude_fn: records what the runner handed it (and the temp cwd's contents, which are removed afterwards)."""

    def __init__(self, ctx, build=None, *, script=None):
        self.ctx, self.build = ctx, build
        self.script = list(script or [])
        self.calls: list[dict] = []

    def __call__(self, prompt, cfg_path, argv_extra, cwd, env, timeout):
        arm = next(a for a, s in ir.ARMS.items()
                   if s["sources"] == argv_extra[argv_extra.index("--setting-sources") + 1]
                   and s["tools"] == argv_extra[argv_extra.index("--tools") + 1])
        config = json.loads(Path(cfg_path).read_text())
        listing = sorted(str(p.relative_to(cwd)) for p in Path(cwd).rglob("*"))
        task_id = next(j["id"] for j in JOBS if prompt.startswith(j["prompt"]))
        self.calls.append({"prompt": prompt, "argv_extra": argv_extra, "config": config, "listing": listing, "env": env,
                           "arm": arm, "cwd": cwd, "timeout": timeout, "task_id": task_id})
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        model = argv_extra[argv_extra.index("--model") + 1]
        events = self.build(arm, task_id) if self.build else events_for(arm, self.ctx, text=answer_text(task_id), model=model)
        return {"events": events, "stderr": "", "returncode": 0, "wall_ms": 7, "timed_out": False}


class FakeReader:
    def __init__(self, *, log=None, check_ok=True, stale_then_ok=False, always_stale=False, fail=None, index_lag=False):
        self.log = log if log is not None else []
        self.check_ok, self.fail = check_ok, fail
        self.stale = stale_then_ok or always_stale
        self.always_stale = always_stale
        self.index_lag = index_lag
        self.index_calls = []

    def wait_search_index(self, timeout, interval=10.0, absent_contacts=(), as_of=None):
        self.index_calls.append((timeout, tuple(absent_contacts), as_of))
        if self.index_lag:
            return {"ok": False, "waited_s": timeout, "differs": ["deals:P01 renewal"]}
        return {"ok": True, "waited_s": 0.0, "differs": []}

    def verify_baseline(self):
        self.log.append("verify")
        if self.stale:
            if not self.always_stale:
                self.stale = False
            return {"ok": False, "differs": ["deals:Acme Corp renewal"]}
        return {"ok": True, "differs": []}

    def end_state(self, since, targets):
        self.log.append(("end_state", since, tuple(targets)))
        if self.fail == "end_state":
            raise RuntimeError("HTTP 503")
        return {"read_at": since, "tz": "Europe/Berlin", "rounds": 2, "stable": True, "target_names": list(targets),
                "created": {}, "counts": {}}

    def read_check(self, since):
        self.log.append("read_check")
        if self.fail == "read_check":
            raise RuntimeError("HTTP 503")
        return {"ok": self.check_ok, "deals_changed": 0 if self.check_ok else 1, "contacts_changed": 0, "companies_changed": 0,
                "created": {}, "changed_names": [] if self.check_ok else ["deals:Acme Corp renewal"]}


def restore_recorder(log, codes=(0,)):
    codes = list(codes)

    def restore():
        log.append("restore")
        code = codes.pop(0) if len(codes) > 1 else codes[0]
        return {"exit_code": code, "output_tail": "restored" if code == 0 else "boom"}
    return restore


def run(ctx, task_id, cell, claude, reader=None, restore=None, **kw):
    reader = reader or FakeReader()
    restore = restore or restore_recorder(reader.log)
    return ir.run_session(JOB[task_id], cell, 1, ctx, claude_fn=claude, reader=reader, restore_fn=restore, **kw)


def rerun(ctx, task_id, claude, reader, restore):
    records: list = []
    counter = [0]
    ok = ir.run_with_rerun(JOB[task_id], ("C", "sonnet"), 1, ctx, counter, claude_fn=claude, reader=reader,
                           restore_fn=restore, sink=records.append)
    return ok, records, counter


# --- arm configuration ----------------------------------------------------------------------------------------


def test_the_cells_are_seven_and_arm_d_has_no_opus_cell():
    assert ir.CELLS == [("A", "sonnet"), ("A", "opus"), ("B", "sonnet"), ("B", "opus"), ("C", "sonnet"), ("C", "opus"), ("D", "sonnet")]
    assert ir.select_cells("A,B,C,D", "sonnet,opus") == ir.CELLS
    assert ir.select_cells("D", "sonnet,opus") == [("D", "sonnet")]
    assert ir.select_cells("A,B", "opus") == [("A", "opus"), ("B", "opus")]


def test_a_smoke_run_is_49_sessions_and_arms_rotate_per_rep():
    plan = list(ir.session_plan(JOBS, ir.CELLS, 1))
    assert len(plan) == 49 and {t["id"] for _, t, _ in plan} == set(JOB)
    assert ir.rotated(ir.CELLS, 1)[0] == ("A", "opus") and ir.rotated(ir.CELLS, 2)[0] == ("B", "sonnet")
    assert ir.rotated(ir.CELLS, 7) == ir.CELLS
    assert [rep for rep, _, _ in ir.session_plan(JOBS[:1], ir.CELLS, 2)] == [1] * 7 + [2] * 7


@pytest.mark.parametrize(("arm", "server", "url", "tools", "sources", "bearer"), [
    ("A", "hubspot-bench", "https://mcp.hubspot.com", "", "", False),
    ("B", "hubspot-bench", "https://mcp.hubspot.com", "Bash,Skill,Read", "project", False),
    ("C", "hubspot-intent", "http://127.0.0.1:8000/mcp/intent", "Skill", "project", True),
    ("D", "hubspot-grouped", "http://127.0.0.1:8000/mcp/grouped", "Skill", "project", True),
])
def test_each_arm_gets_its_server_tools_and_setting_sources(arm, server, url, tools, sources, bearer):
    config = ir.mcp_config(arm, "http://127.0.0.1:8000/", SECRET)
    assert list(config["mcpServers"]) == [server]
    entry = config["mcpServers"][server]
    assert entry["type"] == "http" and entry["url"] == url
    assert ("headers" in entry) is bearer, "only the local servers get the bearer secret"
    if bearer:
        assert entry["headers"] == {"Authorization": f"Bearer {SECRET}"}
    extra = ir.arm_argv_extra(arm, "claude-sonnet-5-5", Path("/run/sales-rules.md"))
    assert extra[extra.index("--tools") + 1] == tools
    assert extra[extra.index("--setting-sources") + 1] == sources
    assert extra[extra.index("--model") + 1] == "claude-sonnet-5-5"
    assert extra[extra.index("--append-system-prompt-file") + 1] == "/run/sales-rules.md"


def test_the_base_command_is_strict_headless_and_carries_no_secret():
    argv = ir.base_argv("claude", "the prompt", "/tmp/x/mcp-config.json")
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--verbose"):
        assert flag in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert argv[argv.index("--mcp-config") + 1] == "/tmp/x/mcp-config.json"
    assert not any(SECRET in a for a in argv)


def test_the_masked_config_hides_the_bearer():
    masked = ir.masked(ir.mcp_config("C", "http://h", SECRET))
    assert SECRET not in json.dumps(masked) and "Bearer ***" in json.dumps(masked)


def test_models_are_passed_explicitly_and_the_observed_id_is_recorded(ctx):
    sonnet = FakeClaude(ctx)
    rec = run(ctx, "job1", ("C", "sonnet"), sonnet)
    assert sonnet.calls[0]["argv_extra"][1] == "claude-sonnet-5-5" and rec["model_id"] == "claude-sonnet-5-5"
    opus = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text=answer_text(t), model="claude-opus-5-5"))
    rec = run(ctx, "job1", ("C", "opus"), opus)
    assert opus.calls[0]["argv_extra"][1] == "claude-opus-5-5"
    assert rec["model"] == "opus" and rec["model_id"] == "claude-opus-5-5" and rec["model_observed"] == "claude-opus-5-5"


def test_a_session_served_by_another_model_than_requested_is_a_harness_failure(ctx):
    other = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text=answer_text(t), model="claude-haiku-5"))
    rec = run(ctx, "job1", ("C", "sonnet"), other)
    assert rec["failure_class"] == "harness" and rec["failure_detail"] == "model_mismatch: claude-haiku-5 != claude-sonnet-5-5"
    dated = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text=answer_text(t), model="claude-sonnet-5-5-20261001"))
    assert run(ctx, "job1", ("C", "sonnet"), dated)["failure_class"] is None
    unreported = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text=answer_text(t), model=""))
    assert run(ctx, "job1", ("C", "sonnet"), unreported)["failure_class"] is None, "an empty init model is unchecked"


def test_the_summary_counts_this_runners_server_names_and_the_full_reply_is_kept(ctx):
    tools = [("mcp__hubspot-intent__pronova_get_deal", {}, "{}", False), ("mcp__hubspot-intent__pronova_records", {}, "boom", True)]
    long_text = "x" * 9000 + "\n```json\n" + JOB["job1"]["answer_example"] + "\n```"
    claude = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text=long_text, tools=tools))
    rec = run(ctx, "job1", ("C", "sonnet"), claude)
    assert rec["summary"]["hubspot_tool_names"] == ["pronova_get_deal", "pronova_records"]
    assert rec["summary"]["hubspot_tool_errors"] == 1
    assert rec["result_text"] == long_text and len(rec["summary"]["final_text"]) == 8000


# --- environment ---------------------------------------------------------------------------------------------

STRIPPED = {"CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "HUBSPOT_PRIVATE_APP_TOKEN",
            "SEED_DEMO_USER_EMAIL", "HUBSPOT_TOKEN_149614891", "HUBSPOT_TOKEN_222", SECRET_ENV, "AI_GATEWAY_API_KEY"}


@pytest.mark.parametrize("arm", ["A", "B", "C", "D"])
def test_the_session_environment_has_no_subagent_pins_and_no_credentials(ctx, arm):
    env, removed = ir.session_env(arm, ctx.base_env, SECRET_ENV, ctx.cli_dir)
    assert not STRIPPED & set(env) and set(removed) == STRIPPED
    assert env["HOME"] == "/home/x" and "tok-test" not in json.dumps(env) and SECRET not in json.dumps(env)
    assert ctx.base_env["HUBSPOT_TOKEN_149614891"] == "tok-test", "the harness's own environment is not mutated"


def test_only_arm_b_gets_the_cli_on_its_path_and_auto_upgrade_off(ctx):
    for arm in "ACD":
        env, _ = ir.session_env(arm, ctx.base_env, SECRET_ENV, ctx.cli_dir)
        assert "HUBSPOT_NO_AUTO_UPGRADE" not in env and env["PATH"] == "/usr/bin"
    env, _ = ir.session_env("B", ctx.base_env, SECRET_ENV, ctx.cli_dir)
    assert env["HUBSPOT_NO_AUTO_UPGRADE"] == "1" and env["PATH"].startswith("/opt/hubspot/bin:")


OPERATOR_SHELL = {"PATH": "/usr/bin", "HOME": "/home/x", "USER": "x", "LANG": "en_US.UTF-8", "LC_ALL": "C", "TERM": "xterm",
                  "GITHUB_TOKEN": "gh-1", "VERCEL_TOKEN": "vc-1", "OPENAI_API_KEY": "oa-1", "HUBSPOT_PERSONAL_ACCESS_KEY": "pak",
                  "HUBSPOT_ACCESS_TOKEN": "hat", "ANTHROPIC_API_KEY": "ak-1", "CLAUDE_CODE_OAUTH_TOKEN": "ot-1",
                  "ANTHROPIC_BASE_URL": "https://gateway.example.com", "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
                  "CLAUDE_CODE_SUBAGENT_MODEL": "claude-sonnet-5-5", "SOME_OTHER_VAR": "v"}


def test_arm_b_environment_is_an_allowlist_so_no_other_credential_in_the_shell_reaches_its_bash(ctx, tmp_path):
    env, removed = ir.session_env("B", OPERATOR_SHELL, SECRET_ENV, ctx.cli_dir, tmp_path)
    assert set(env) <= {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_ENABLE_TELEMETRY",
                        "HUBSPOT_NO_AUTO_UPGRADE", "SHELL", "ZDOTDIR"}
    for leaked in ("GITHUB_TOKEN", "VERCEL_TOKEN", "OPENAI_API_KEY", "HUBSPOT_PERSONAL_ACCESS_KEY", "HUBSPOT_ACCESS_TOKEN",
                   "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_SUBAGENT_MODEL", "SOME_OTHER_VAR"):
        assert leaked not in env and leaked in removed
    assert "gh-1" not in json.dumps(env) and env["HUBSPOT_NO_AUTO_UPGRADE"] == "1"
    assert "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB" not in env, "it forces permission mode default in claude 2.1.314"


def test_arm_b_runs_zsh_with_an_empty_zdotdir_so_the_operators_shell_profile_is_not_sourced(ctx, tmp_path):
    # A stand-in zsh on PATH, so the test does not depend on the machine having zsh (CI's Ubuntu image has none).
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "zsh").write_text("#!/bin/sh\n", encoding="utf-8")
    (bindir / "zsh").chmod(0o755)
    workdir = tmp_path / "session"
    env, _ = ir.session_env("B", {**OPERATOR_SHELL, "PATH": str(bindir)}, SECRET_ENV, ctx.cli_dir, workdir)
    assert env["ZDOTDIR"] == str(workdir) and env["SHELL"] == str(bindir / "zsh")
    env, _ = ir.session_env("B", OPERATOR_SHELL, SECRET_ENV, ctx.cli_dir)
    assert "ZDOTDIR" not in env


def test_without_zsh_on_path_arm_b_sets_no_shell_override(ctx, tmp_path):
    env, _ = ir.session_env("B", {**OPERATOR_SHELL, "PATH": str(tmp_path)}, SECRET_ENV, ctx.cli_dir, tmp_path)
    assert "ZDOTDIR" not in env


def test_the_other_arms_keep_the_operators_environment_minus_the_credentials_the_harness_names(ctx):
    for arm in "ACD":
        env, _ = ir.session_env(arm, OPERATOR_SHELL, SECRET_ENV, ctx.cli_dir)
        assert env["SOME_OTHER_VAR"] == "v" and "CLAUDE_CODE_SUBAGENT_MODEL" not in env


def test_the_environment_claude_receives_is_the_stripped_one_and_the_secret_is_only_in_the_config_file(ctx):
    claude = FakeClaude(ctx)
    run(ctx, "job1", ("C", "sonnet"), claude)
    call = claude.calls[0]
    assert not STRIPPED & set(call["env"])
    assert SECRET not in json.dumps(call["argv_extra"]) and SECRET not in call["prompt"]
    assert call["config"]["mcpServers"]["hubspot-intent"]["headers"]["Authorization"] == f"Bearer {SECRET}"


# --- skills isolation ------------------------------------------------------------------------------------------


def skills_in(listing):
    return {p.split("/")[2] for p in listing if p.startswith(".claude/skills/") and p.count("/") >= 2}


def test_each_session_runs_in_its_own_clean_directory_that_is_removed_afterwards(ctx):
    claude = FakeClaude(ctx)
    run(ctx, "job1", ("A", "sonnet"), claude)
    run(ctx, "job1", ("A", "sonnet"), claude)
    first, second = claude.calls
    assert first["cwd"] != second["cwd"] and not Path(first["cwd"]).exists() and not Path(second["cwd"]).exists()


def test_arm_a_gets_no_claude_directory_at_all(ctx):
    claude = FakeClaude(ctx)
    run(ctx, "job1", ("A", "sonnet"), claude)
    assert claude.calls[0]["listing"] == ["mcp-config.json"]


def test_only_arm_b_gets_filesystem_deny_settings_and_they_cover_the_answer_key_and_other_runs(ctx):
    for arm in "ACD":
        claude = FakeClaude(ctx)
        run(ctx, "job1", (arm, "sonnet"), claude)
        assert ".claude/settings.json" not in claude.calls[0]["listing"]
    seen = {}

    def spy(prompt, cfg_path, argv_extra, cwd, env, timeout):
        seen["settings"] = json.loads((Path(cwd) / ".claude" / "settings.json").read_text())
        return {"events": events_for("B", ctx, text=answer_text("job1")), "stderr": "", "returncode": 0, "wall_ms": 1,
                "timed_out": False}

    ctx.deny_paths = [Path("/work/space"), Path("/home/x/.claude")]
    run(ctx, "job1", ("B", "sonnet"), spy)
    settings = seen["settings"]
    assert settings["permissions"]["deny"] == ["Read(//home/x/.claude/**)", "Read(//work/space/**)"]
    sandbox = settings["sandbox"]
    assert sandbox["enabled"] is True and sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["filesystem"]["denyRead"] == ["/home/x/.claude", "/work/space"]


def test_the_default_deny_paths_hold_the_workspace_with_the_answer_key_and_the_run_directories():
    deny = ir.default_deny_paths()
    bench_runs = BENCH / "runs"
    assert any(bench_runs.is_relative_to(p) for p in deny), "bench/runs must sit under a denied path"
    if TRUTH_FILE.is_file():  # the answer key exists only on the machine that seeds the portal, not in CI
        assert any(TRUTH_FILE.is_relative_to(p) for p in deny), "the truth file's directory tree must be denied"
    assert Path.home() / ".claude" in deny


def test_arm_b_gets_only_the_hubspot_cli_skills(ctx):
    claude = FakeClaude(ctx)
    run(ctx, "job1", ("B", "sonnet"), claude)
    assert skills_in(claude.calls[0]["listing"]) == {"deal-management", "crm-lookup"}


@pytest.mark.parametrize("arm", ["C", "D"])
def test_arms_c_and_d_get_only_the_thin_skills_and_never_the_hubspot_ones(ctx, arm):
    claude = FakeClaude(ctx)
    run(ctx, "job1", (arm, "sonnet"), claude)
    assert skills_in(claude.calls[0]["listing"]) == {"triage-format", "forecast-format"}
    assert not any("deal-management" in p for p in claude.calls[0]["listing"])


def test_skill_names_come_from_frontmatter_with_the_directory_name_as_fallback(tmp_path):
    (tmp_path / "dir-one").mkdir()
    (tmp_path / "dir-one" / "SKILL.md").write_text("---\nname: Named One\n---\nx", encoding="utf-8")
    (tmp_path / "dir-two").mkdir()
    (tmp_path / "dir-two" / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    (tmp_path / "not-a-skill").mkdir()
    assert ir.skill_names(tmp_path) == ["Named One", "dir-two"]


def test_a_hubspot_skill_visible_to_arm_c_fails_the_session_as_a_harness_error(ctx):
    claude = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text=answer_text(t), skills_extra=["deal-management"]))
    rec = run(ctx, "job1", ("C", "sonnet"), claude)
    assert rec["failure_class"] == "harness" and rec["skills_unexpected"] == ["deal-management"]
    assert "skills_unexpected: deal-management" in rec["failure_detail"]


def test_a_hubspot_cli_skill_visible_to_arm_a_fails_the_session(ctx):
    claude = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text=answer_text(t), skills_extra=["crm-lookup"]))
    rec = run(ctx, "job1", ("A", "sonnet"), claude)
    assert rec["failure_class"] == "harness" and rec["skills_unexpected"] == ["crm-lookup"]


def test_a_new_claude_code_skill_no_arm_provides_is_recorded_not_failed(ctx):
    claude = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text=answer_text(t), skills_extra=["send-to-project"]))
    for cell in (("A", "sonnet"), ("C", "sonnet")):
        rec = run(ctx, "job1", cell, claude)
        assert rec["failure_class"] is None, rec["failure_detail"]
        assert rec["skills_unexpected"] == [] and rec["skills_unknown"] == ["send-to-project"]


def test_a_missing_expected_skill_fails_the_session(ctx):
    def build(arm, t):
        return events_for(arm, ctx, text=answer_text(t), skills=sorted(ir.BUILTIN_SKILLS | {"triage-format"}))
    rec = run(ctx, "job1", ("C", "sonnet"), FakeClaude(ctx, build))
    assert rec["failure_class"] == "harness" and rec["skills_missing"] == ["forecast-format"]


def test_a_builtin_tool_the_arm_should_not_have_fails_the_session(ctx):
    claude = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text=answer_text(t), builtin_tools=["Skill", "Bash"]))
    rec = run(ctx, "job1", ("C", "sonnet"), claude)
    assert rec["failure_class"] == "harness" and rec["tools_ok"] is False


def test_skills_loaded_records_every_skill_invocation_in_order_with_duplicates(ctx):
    tools = [("Skill", {"skill": "triage-format"}), ("Skill", {"name": "forecast-format"}), ("Skill", {"skill": "triage-format"})]
    claude = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text=answer_text(t), tools=tools))
    rec = run(ctx, "job1", ("C", "sonnet"), claude)
    assert rec["skills_loaded"] == ["triage-format", "forecast-format", "triage-format"]
    assert rec["failure_class"] is None and rec["tools_ok"] is True
    assert rec["init"]["skills"] and rec["skills_expected"] == ["forecast-format", "triage-format"]


# --- shared text ---------------------------------------------------------------------------------------------


def test_the_prompt_is_the_rep_text_verbatim_then_the_suffix_for_its_kind_then_the_answer_block():
    for job in JOBS:
        text = ir.compose_prompt(job)
        assert text.startswith(job["prompt"] + "\n\n")
        suffix = ir.SUFFIX_WRITE if job["kind"] == "write" else ir.SUFFIX_READ
        assert text.split("\n\n")[1] == suffix
        assert text.endswith(f"\n```json\n{job['answer_example']}\n```")
        assert ir.ANSWER_PREFIX in text and job["answer_rule"] in text


def test_write_tasks_pre_approve_writes_and_read_tasks_forbid_them():
    kinds = {j["id"]: j["kind"] for j in JOBS}
    assert kinds == {"job1": "read", "job2": "read", "job3": "write", "job4": "read", "job5": "write",
                     "off_phone": "read", "off_globex": "write"}
    assert ir.suffix_for("write") == "You have my approval to make these changes in HubSpot; if a tool asks for confirmation, confirm it."
    assert ir.suffix_for("read") == "Do not change anything in HubSpot."


def test_every_arm_receives_the_same_text(ctx):
    sent = set()
    for arm in "ABCD":
        claude = FakeClaude(ctx)
        rec = run(ctx, "job3", (arm, "sonnet"), claude)
        assert claude.calls[0]["prompt"] == rec["prompt_sent"]
        sent.add(rec["prompt_sent"])
    assert len(sent) == 1


def test_the_prompts_equal_the_truth_files_when_it_exists():
    if not TRUTH_FILE.is_file():
        pytest.skip("truth file not on this machine")
    truth = json.loads(TRUTH_FILE.read_text(encoding="utf-8"))
    for job in JOBS:
        assert ir.truth_at(truth, job["truth_key"])["prompt"] == job["prompt"]
    assert truth["meta"]["as_of"] == "2026-11-10"


def test_jobs_file_has_seven_valid_lines_and_each_example_validates_against_its_schema():
    assert [j["id"] for j in JOBS] == ["job1", "job2", "job3", "job4", "job5", "off_phone", "off_globex"]
    for job in JOBS:
        assert set(job) == {"id", "kind", "truth_key", "prompt", "answer_rule", "answer_example", "answer_schema"}
        assert ir.validate_schema(json.loads(job["answer_example"]), job["answer_schema"]) is None, job["id"]


def test_write_task_targets_come_from_the_truth_file(ctx):
    assert ir.target_names(JOB["job3"], ctx.truth) == ["Acme Corp renewal"]
    assert ir.target_names(JOB["job5"], ctx.truth) == DEALS
    assert ir.target_names(JOB["off_globex"], ctx.truth) == ["Globex expansion"]
    assert ir.target_names(JOB["job1"], ctx.truth) == [] and ir.target_names(JOB["job4"], ctx.truth) == []


# --- answer block ----------------------------------------------------------------------------------------------


def test_the_last_json_block_wins_and_earlier_blocks_are_ignored():
    text = 'draft\n```json\n{"a": 1}\n```\nfinal\n```json\n{"a": 2}\n```\n'
    out = ir.extract_answer_block(text)
    assert out["parsed"] == {"a": 2} and out["block_count"] == 2 and out["error"] is None


@pytest.mark.parametrize(("text", "error"), [
    ("no block here", "no_block"),
    ("```python\nx = 1\n```", "no_block"),
    ("```json\n{not json}\n```", "invalid_json"),
])
def test_missing_or_broken_blocks_are_reported(text, error):
    out = ir.extract_answer_block(text)
    assert out["error"] == error and out["parsed"] is None


def test_a_schema_violation_keeps_the_parsed_answer_and_names_the_path():
    schema = JOB["job4"]["answer_schema"]
    figures = {"closed_won": "x", "commit": 1, "best_case": 1, "pipeline": 1, "weighted": 1, "supported_commit": 1}
    out = ir.extract_answer_block("```json\n" + json.dumps({"figures": figures}) + "\n```", schema)
    assert out["error"] == "schema: $.figures.closed_won" and out["parsed"] == {"figures": figures}
    assert ir.extract_answer_block('```json\n{"figures": {}}\n```', schema)["error"] == "schema: $.figures.closed_won"


def test_the_schema_subset_covers_type_required_properties_items_and_enum():
    schema = {"type": "object", "required": ["k"], "properties": {
        "k": {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}},
        "n": {"type": ["integer", "null"]}}}
    assert ir.validate_schema({"k": ["a"], "n": None}, schema) is None
    assert ir.validate_schema({"n": 1}, schema) == "$.k"
    assert ir.validate_schema({"k": ["c"]}, schema) == "$.k[0]"
    assert ir.validate_schema({"k": [], "n": 1.5}, schema) == "$.n"
    assert ir.validate_schema({"k": [], "n": True}, schema) == "$.n", "a bool is not an integer"


def test_the_answer_is_parsed_from_the_full_result_not_the_capped_summary_text(ctx):
    long_text = "x" * 9000 + "\n```json\n" + JOB["job1"]["answer_example"] + "\n```"
    claude = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text=long_text))
    rec = run(ctx, "job1", ("C", "sonnet"), claude)
    assert len(rec["summary"]["final_text"]) == 8000
    assert rec["answer_block"]["error"] is None and rec["answer_block"]["parsed"]["flagged_deals"]


def test_without_a_result_event_the_last_assistant_text_is_used():
    events = [{"type": "assistant", "message": {"content": [{"type": "text", "text": 'hi\n```json\n{"a": 1}\n```'}]}}]
    assert ir.extract_answer_block(ir.final_result_text(events))["parsed"] == {"a": 1}


# --- end state, restore, read check -----------------------------------------------------------------------------


def test_a_write_task_reads_the_end_state_then_restores_in_that_order(ctx):
    reader = FakeReader()
    rec = run(ctx, "job5", ("C", "sonnet"), FakeClaude(ctx), reader=reader)
    assert [e if isinstance(e, str) else e[0] for e in reader.log] == ["verify", "end_state", "restore"]
    assert reader.log[1] == ("end_state", rec["since"], tuple(DEALS))
    assert rec["end_state"]["target_names"] == DEALS and rec["end_state"]["stable"] is True
    assert rec["read_check"] is None
    assert rec["restore"] == {"ran": True, "ok": True, "attempts": 1, "exit_code": 0, "output_tail": "restored"}
    assert rec["failure_class"] is None


def test_a_clean_read_task_checks_but_does_not_restore(ctx):
    reader = FakeReader()
    rec = run(ctx, "job1", ("A", "sonnet"), FakeClaude(ctx), reader=reader)
    assert reader.log == ["verify", "read_check"]
    assert rec["read_check"]["ok"] is True and rec["end_state"] is None
    assert rec["restore"]["ran"] is False and rec["summary"]["unchanged"] is True


def test_a_read_task_that_changed_the_portal_is_a_model_failure_and_triggers_a_restore(ctx):
    reader = FakeReader(check_ok=False)
    rec = run(ctx, "job1", ("B", "opus"), FakeClaude(ctx), reader=reader)
    assert (rec["failure_class"], rec["failure_detail"]) == ("model", "wrote_on_read_task")
    assert reader.log == ["verify", "read_check", "restore"] and rec["restore"]["ok"] is True
    assert rec["summary"]["unchanged"] is False


def test_no_restore_skips_every_restore_but_the_end_state_read_still_happens(ctx):
    ctx.no_restore = True
    reader = FakeReader()
    rec = run(ctx, "job3", ("C", "sonnet"), FakeClaude(ctx), reader=reader)
    assert "restore" not in reader.log and any(isinstance(e, tuple) and e[0] == "end_state" for e in reader.log)
    assert rec["restore"]["ran"] is False and rec["end_state"]["stable"] is True


def test_a_failed_restore_is_retried_once_and_a_second_failure_stops_the_run(ctx):
    reader = FakeReader()
    ok, records, _ = rerun(ctx, "job3", FakeClaude(ctx), reader, restore_recorder(reader.log, codes=(1, 1)))
    assert ok is False and reader.log.count("restore") == 2
    assert records[0]["restore"]["ok"] is False and records[0]["restore"]["attempts"] == 2


def test_a_restore_that_succeeds_on_the_retry_lets_the_run_continue(ctx):
    reader = FakeReader()
    ok, records, _ = rerun(ctx, "job3", FakeClaude(ctx), reader, restore_recorder(reader.log, codes=(1, 0)))
    assert ok is True and records[0]["restore"]["attempts"] == 2 and records[0]["restore"]["ok"] is True


def test_restore_runs_even_when_the_end_state_read_fails_and_the_failure_is_upstream(ctx):
    reader = FakeReader(fail="end_state")
    rec = run(ctx, "job3", ("C", "sonnet"), FakeClaude(ctx), reader=reader)
    assert (rec["failure_class"], rec["failure_detail"]) == ("upstream", "end_state_read")
    assert rec["end_state"]["error"].startswith("RuntimeError") and reader.log[-1] == "restore"


def test_a_failed_read_check_is_an_upstream_failure(ctx):
    rec = run(ctx, "job1", ("C", "sonnet"), FakeClaude(ctx), reader=FakeReader(fail="read_check"))
    assert (rec["failure_class"], rec["failure_detail"]) == ("upstream", "read_check")


def test_stale_seed_state_is_restored_before_the_session_and_the_session_still_runs(ctx):
    reader = FakeReader(stale_then_ok=True)
    claude = FakeClaude(ctx)
    rec = run(ctx, "job1", ("C", "sonnet"), claude, reader=reader)
    assert reader.log[:3] == ["verify", "restore", "verify"] and len(claude.calls) == 1 and rec["failure_class"] is None


def test_stale_seed_state_that_a_restore_cannot_fix_is_upstream_and_claude_never_starts(ctx):
    claude = FakeClaude(ctx)
    rec = run(ctx, "job1", ("C", "sonnet"), claude, reader=FakeReader(always_stale=True))
    assert rec["failure_class"] == "upstream" and rec["failure_detail"].startswith("stale_state") and claude.calls == []


def test_make_restore_fn_runs_the_command_in_its_directory_with_the_token_under_the_name_restore_py_reads(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return SimpleNamespace(returncode=0, stdout="a" * 3000, stderr="tail")

    monkeypatch.setattr(ir.subprocess, "run", fake_run)
    restore = ir.make_restore_fn("python3 demo/seed/restore.py --expect-portal 123 --apply --yes", "/work/forecast",
                                 "HUBSPOT_TOKEN_123", {"HUBSPOT_TOKEN_123": "tok-test", "PATH": "/bin"})
    result = restore()
    assert seen["cmd"] == ["python3", "demo/seed/restore.py", "--expect-portal", "123", "--apply", "--yes"]
    assert seen["cwd"] == "/work/forecast" and seen["timeout"] == 600 and seen["capture_output"] is True
    assert seen["env"]["HUBSPOT_PRIVATE_APP_TOKEN"] == "tok-test"
    assert result["exit_code"] == 0 and len(result["output_tail"]) == 2000 and result["output_tail"].endswith("tail")


def test_restore_keeps_an_existing_private_app_token(monkeypatch):
    seen = {}
    monkeypatch.setattr(ir.subprocess, "run",
                        lambda cmd, **kw: seen.update(env=kw["env"]) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    ir.make_restore_fn("true", "/", "HUBSPOT_TOKEN_123", {"HUBSPOT_TOKEN_123": "a", "HUBSPOT_PRIVATE_APP_TOKEN": "b"})()
    assert seen["env"]["HUBSPOT_PRIVATE_APP_TOKEN"] == "b"


# --- failure classification --------------------------------------------------------------------------------------

OK_CHECKS = {"skills_unexpected": [], "skills_missing": [], "tools_ok": True, "tools_seen": [], "mcp_bad": []}


def classify(**over):
    args = {"checks": OK_CHECKS, "init_missing": False, "stderr": "",
            "result": {"subtype": "success", "is_error": False, "result": "fine"}, "error_tool_texts": [],
            "timed_out": False, "returncode": 0, "answer_error": None}
    args.update(over)
    return ir.classify_failure(**args)


def test_a_clean_session_has_no_failure_class():
    assert classify() == (None, "")


@pytest.mark.parametrize("stderr", [
    "API Error: 429 Too Many Requests", "HTTP 503 Service Unavailable", "overloaded_error", "Rate limit reached",
    "read ECONNRESET", "invalid_grant", "OAuth token expired", "server needs-auth",
])
def test_upstream_signatures_in_stderr_are_upstream(stderr):
    assert classify(stderr=stderr)[0] == "upstream"


def test_upstream_signatures_in_an_errored_result_are_upstream_but_a_successful_answer_is_never_scanned():
    errored = {"subtype": "error_during_execution", "is_error": True, "result": "API Error: 529 overloaded"}
    assert classify(result=errored)[0] == "upstream"
    fine = {"subtype": "success", "is_error": False, "result": "closed 429 deals; see the rate limit note"}
    assert classify(result=fine) == (None, "")


def test_one_failed_tool_result_is_not_upstream_but_two_are():
    assert classify(error_tool_texts=["HTTP 503 from upstream"]) == (None, "")
    assert classify(error_tool_texts=["HTTP 503", "429 Too Many Requests"]) == ("upstream", "signature_in_tool_results")


def test_an_mcp_server_that_is_not_connected_is_upstream_with_its_status():
    checks = {**OK_CHECKS, "mcp_bad": ["hubspot-bench:needs-auth"]}
    assert classify(checks=checks) == ("upstream", "mcp_hubspot-bench:needs-auth")


def test_a_skills_or_tools_violation_is_harness_even_when_the_mcp_server_is_down():
    checks = {**OK_CHECKS, "skills_unexpected": ["x"], "mcp_bad": ["s:failed"]}
    assert classify(checks=checks)[0] == "harness"
    assert classify(checks={**OK_CHECKS, "tools_ok": False, "tools_seen": ["Bash"]}) == ("harness", "tools: Bash")


def test_a_timeout_without_an_upstream_signature_is_a_model_failure_and_with_one_it_is_upstream():
    assert classify(timed_out=True, stderr="timeout", returncode=-1) == ("model", "timeout")
    assert classify(timed_out=True, stderr="overloaded")[0] == "upstream"


def test_no_init_event_without_a_signature_is_harness():
    assert classify(checks=None, init_missing=True, returncode=1)[0] == "harness"


@pytest.mark.parametrize("error", ["no_block", "invalid_json"])
def test_a_missing_or_unparsable_answer_is_a_model_failure_but_a_schema_miss_is_graded(error):
    assert classify(answer_error=error)[0] == "model"
    assert classify(answer_error="schema: $.figures.commit") == (None, "")


def test_upstream_failures_are_rerun_once_and_both_attempts_are_kept(ctx):
    claude = FakeClaude(ctx, script=[
        {"events": [], "stderr": "API Error: 429 Too Many Requests", "returncode": 1, "wall_ms": 3, "timed_out": False},
    ])
    reader = FakeReader()
    ok, records, counter = rerun(ctx, "job3", claude, reader, restore_recorder(reader.log))
    assert ok is True and len(records) == 2 and counter[0] == 2
    first, second = records
    assert (first["attempt"], first["failure_class"], first["rerun_of"]) == (1, "upstream", None)
    assert (second["attempt"], second["failure_class"], second["rerun_of"]) == (2, None, first["n"])
    assert reader.log.count("restore") == 2, "a write task restores after each attempt, so the re-run starts clean"
    assert len(claude.calls) == 2


def test_a_second_upstream_failure_is_not_retried_again(ctx):
    bad = {"events": [], "stderr": "HTTP 503 Service Unavailable", "returncode": 1, "wall_ms": 1, "timed_out": False}
    claude = FakeClaude(ctx, script=[bad, bad])
    reader = FakeReader()
    _, records, _ = rerun(ctx, "job1", claude, reader, restore_recorder(reader.log))
    assert [r["failure_class"] for r in records] == ["upstream", "upstream"] and len(claude.calls) == 2


def test_model_and_harness_failures_are_not_rerun(ctx):
    no_block = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text="I could not do it."))
    reader = FakeReader()
    _, records, _ = rerun(ctx, "job1", no_block, reader, restore_recorder(reader.log))
    assert [(r["failure_class"], r["attempt"]) for r in records] == [("model", 1)]
    wrong_model = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text=answer_text(t), model="claude-haiku-5"))
    _, records, _ = rerun(ctx, "job1", wrong_model, reader, restore_recorder(reader.log))
    assert [(r["failure_class"], r["attempt"]) for r in records] == [("harness", 1)]


@pytest.mark.parametrize("kwargs", [{"skills_extra": ["deal-management"]}, {"builtin_tools": ["Skill", "Bash"]}])
def test_a_skills_or_tools_leak_stops_the_run_after_its_session_and_its_restore(ctx, kwargs):
    leak = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text=answer_text(t), **kwargs))
    reader = FakeReader()
    records: list = []
    with pytest.raises(ir.HarnessLeak, match="C/sonnet job3"):
        ir.run_with_rerun(JOB["job3"], ("C", "sonnet"), 1, ctx, [0], claude_fn=leak, reader=reader,
                          restore_fn=restore_recorder(reader.log), sink=records.append)
    assert [r["failure_class"] for r in records] == ["harness"] and len(leak.calls) == 1
    assert reader.log[-1] == "restore", "the write task's restore ran before the run stopped"


def test_a_session_that_times_out_is_a_model_failure_and_a_missing_claude_is_harness(ctx):
    timed_out = FakeClaude(ctx, script=[{"events": [], "stderr": "timeout", "returncode": -1, "wall_ms": 9, "timed_out": True}])
    rec = run(ctx, "job1", ("A", "sonnet"), timed_out)
    assert (rec["failure_class"], rec["failure_detail"]) == ("model", "timeout")
    missing = FakeClaude(ctx, script=[RuntimeError("claude CLI not found on PATH")])
    rec = run(ctx, "job1", ("A", "sonnet"), missing)
    assert rec["failure_class"] == "harness" and "not found" in rec["failure_detail"]


# --- recorded calls and confirmations ------------------------------------------------------------------------------


def test_mcp_and_cli_calls_are_split_by_server_and_tool_with_bash_commands_capped(ctx):
    tools = [("mcp__hubspot-bench__search_crm_objects", {"q": "x"}, "{}", False),
             ("Bash", {"command": "hubspot deals list " + "x" * 400}, "ok", False),
             ("mcp__hubspot-bench__manage_crm_objects", {}, "boom", True)]
    claude = FakeClaude(ctx, lambda arm, t: events_for(arm, ctx, text=answer_text(t), tools=tools))
    rec = run(ctx, "job1", ("B", "sonnet"), claude)
    assert [(c["server"], c["tool"], c["is_error"]) for c in rec["mcp_calls"]] == [
        ("hubspot-bench", "search_crm_objects", False), ("hubspot-bench", "manage_crm_objects", True)]
    assert len(rec["cli_calls"]) == 1 and len(rec["cli_calls"][0]["command"]) == 300


def test_preview_envelopes_on_c_and_d_count_as_confirmations_and_writes_are_counted():
    preview = json.dumps({"status": "preview", "confirmation_id": "chg_abc", "change_count": 3})
    summary = {"tool_calls": [
        {"name": "mcp__hubspot-intent__pronova_propose_changes", "input": {}, "result_text": preview},
        {"name": "mcp__hubspot-intent__pronova_apply_changes", "input": {}, "result_text": json.dumps({"status": "applied"})},
        {"name": "mcp__hubspot-intent__pronova_get_deal", "input": {}, "result_text": "{}"},
    ]}
    assert ir.confirmations(summary, "C") == {"requested": 1, "write_tool_calls": 2}
    grouped = {"tool_calls": [
        {"name": "mcp__hubspot-grouped__manage_deal", "input": {"action": "update"}, "result_text": preview},
        {"name": "mcp__hubspot-grouped__write_actions", "input": {"action": "approve"}, "result_text": "{}"},
        {"name": "mcp__hubspot-grouped__write_actions", "input": {"action": "list"}, "result_text": "{}"},
        {"name": "mcp__hubspot-grouped__associations", "input": {"action": "create"}, "result_text": "{}"},
        {"name": "mcp__hubspot-grouped__associations", "input": {"action": "list"}, "result_text": "{}"},
    ]}
    assert ir.confirmations(grouped, "D") == {"requested": 1, "write_tool_calls": 3}


def test_a_and_b_count_confirm_text_on_write_tools_and_cli_write_commands():
    summary = {"tool_calls": [
        {"name": "mcp__hubspot-bench__manage_crm_objects", "input": {}, "result_text": "Please confirm this update"},
        {"name": "mcp__hubspot-bench__search_crm_objects", "input": {}, "result_text": "confirm?"},
        {"name": "Bash", "input": {"command": "hubspot deals update 1 --closedate 2026-12-01"}, "result_text": "done"},
        {"name": "Bash", "input": {"command": "hubspot deals list"}, "result_text": "confirm"},
    ]}
    assert ir.confirmations(summary, "A") == {"requested": 1, "write_tool_calls": 2}
    assert ir.confirmations(summary, "B") == {"requested": 1, "write_tool_calls": 2}


# --- pre-flight ------------------------------------------------------------------------------------------------------

RULES_TEXT = "# Sales rules\n\nAs-of date: 2026-11-10. Every test counts from it.\nTimezone: UTC. Rules fingerprint: abc123def456.\n"


def test_the_rendered_rules_must_state_the_truth_files_as_of_date():
    assert ir.check_rules_text(RULES_TEXT, "2026-11-10") == ("2026-11-10", "abc123def456")
    with pytest.raises(ir.PreflightError, match="differs"):
        ir.check_rules_text(RULES_TEXT, "2026-11-11")
    with pytest.raises(ir.PreflightError, match="no as-of"):
        ir.check_rules_text("nothing", "2026-11-10")


def test_rules_are_rendered_by_the_package_cli_with_the_rules_dir_in_its_environment(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"])
        Path(cmd[cmd.index("--out") + 1]).write_text(RULES_TEXT, encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ir.subprocess, "run", fake_run)
    text = ir.render_rules("/rules", "149614891", tmp_path / "sales-rules.md", {"PATH": "/bin"})
    assert text == RULES_TEXT and seen["cmd"][1:4] == ["-m", "hubspot_mcp.intent.rules", "--portal"]
    assert seen["env"]["HUBSPOT_MCP_RULES_DIR"] == "/rules"


def test_an_invalid_rules_file_aborts_with_the_renderers_message(monkeypatch, tmp_path):
    monkeypatch.setattr(ir.subprocess, "run", lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="error: bad yaml"))
    with pytest.raises(ir.PreflightError, match="bad yaml"):
        ir.render_rules("/rules", "1", tmp_path / "r.md", {})


class FakeSurface:
    def __init__(self, url, counts, hashes):
        self.key, self.counts, self.hashes = url.rsplit("/", 1)[1], counts, hashes

    def initialize(self):
        return {}

    def list_tools(self):
        return [{"name": f"t{i}"} for i in range(self.counts[self.key])]

    def call_tool(self, name, arguments=None):
        data = {"rules_hash": self.hashes[self.key]}
        if self.key == "grouped":
            data["force_preview"] = FORCE_PREVIEW_SERVED["value"]
        return data, False


FORCE_PREVIEW_SERVED = {"value": True}


GOOD = "abc123def456"


@pytest.mark.parametrize(("counts", "hashes", "message"), [
    ({"intent": 13, "grouped": 12}, {"intent": GOOD, "grouped": GOOD}, None),
    ({"intent": 12, "grouped": 12}, {"intent": GOOD, "grouped": GOOD}, "expected 13"),
    ({"intent": 13, "grouped": 11}, {"intent": GOOD, "grouped": GOOD}, "expected 12"),
    ({"intent": 13, "grouped": 12}, {"intent": GOOD, "grouped": "000000000000"}, "stale server"),
])
def test_both_local_surfaces_must_list_the_pinned_tool_counts_and_serve_the_rendered_rules(monkeypatch, counts, hashes, message):
    monkeypatch.setattr(ir, "Surface", lambda url, secret=None: FakeSurface(url, counts, hashes))
    if message is None:
        listed = ir.check_server_rules("http://127.0.0.1:8000", SECRET, GOOD)
        assert listed["C"]["tools"] == 13 and listed["D"]["tools"] == 12 and listed["D"]["force_preview"] is True
    else:
        with pytest.raises(ir.PreflightError, match=message):
            ir.check_server_rules("http://127.0.0.1:8000", SECRET, GOOD)


@pytest.mark.parametrize(("served", "wanted", "message"), [
    (True, True, None), (False, False, None), (False, True, "unset, but this run needs arm D at the preview-everything"),
    (True, False, "=1, but this run needs arm D at the AUTO tier"), (False, None, None), (True, None, None),
])
def test_arm_d_must_run_at_the_tier_the_run_asks_for_in_both_directions(monkeypatch, served, wanted, message):
    monkeypatch.setattr(ir, "Surface", lambda url, secret=None: FakeSurface(
        url, {"intent": 13, "grouped": 12}, {"intent": GOOD, "grouped": GOOD}))
    FORCE_PREVIEW_SERVED["value"] = served
    try:
        if message is None:
            assert ir.check_server_rules("http://h", SECRET, GOOD, d_force_preview=wanted)["D"]["force_preview"] is served
        else:
            with pytest.raises(ir.PreflightError, match=message):
                ir.check_server_rules("http://h", SECRET, GOOD, d_force_preview=wanted)
    finally:
        FORCE_PREVIEW_SERVED["value"] = True


def test_a_local_server_that_is_down_aborts_the_preflight_instead_of_a_traceback(monkeypatch, tmp_path):
    args = SimpleNamespace(skills_b=str(make_skills(tmp_path / "b", ["x"])), skills_cd=str(make_skills(tmp_path / "cd", ["y"])),
                           hubspot_cli="/nonexistent/hubspot", rules_dir=str(tmp_path), portal="123", secret_env=SECRET_ENV,
                           server="http://127.0.0.1:59999", arm_d_auto_tier=False)
    (tmp_path / "123.yaml").write_text("as_of: 2026-11-10\n", encoding="utf-8")
    monkeypatch.setattr(ir, "tool_version", lambda cmd: "2.1.314 (Claude Code)")
    monkeypatch.setattr(ir, "render_rules", lambda *a, **k: "As-of date: 2026-11-10. Rules fingerprint: abc123def456.")
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ir.PreflightError, match="local server unreachable"):
        ir._live_setup(args, make_truth(), [("C", "sonnet")], out, SECRET, {})
    with pytest.raises(httpx.ConnectError):
        ir.Surface("http://127.0.0.1:59999/mcp/intent", SECRET).initialize()


def test_a_missing_claude_binary_aborts_the_official_server_check(monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory: 'claude'")
    monkeypatch.setattr(ir.subprocess, "run", boom)
    with pytest.raises(ir.PreflightError, match="cannot run `claude mcp get hubspot-bench`"):
        ir.check_official_server()


class IdentityClaude:
    """claude_fn for the portal identity check: replies with the given JSON text and keeps what it was handed."""

    def __init__(self, reply, **raw):
        self.reply, self.raw, self.calls = reply, raw, []

    def __call__(self, prompt, cfg_path, argv_extra, cwd, env, timeout):
        self.calls.append({"prompt": prompt, "argv_extra": argv_extra, "env": env, "config": json.loads(Path(cfg_path).read_text()),
                           "settings": (Path(cwd) / ".claude" / "settings.json").is_file()})
        events = [{"type": "result", "subtype": "success", "is_error": False, "result": f"```json\n{self.reply}\n```"}]
        return {"events": events, "stderr": "", "returncode": 0, "wall_ms": 1, "timed_out": False, **self.raw}


def identity(cells, claude, portal="149614891"):
    return ir.check_portal_identity(portal, cells, claude_fn=claude, base_env={"PATH": "/usr/bin", "GITHUB_TOKEN": "gh-1"},
                                    secret_env=SECRET_ENV, cli_dir="/opt/hubspot/bin", rules_path=Path("/run/sales-rules.md"),
                                    model_ids=ir.MODEL_IDS, deny=[Path("/work")])


def test_the_portal_identity_check_for_arm_a_reads_the_official_mcp_once_and_passes_on_a_match():
    claude = IdentityClaude('{"mcp_portal_id": 149614891}')
    assert identity([("A", "sonnet"), ("C", "sonnet")], claude) == {"mcp": "149614891", "cli": None}
    call = claude.calls[0]
    assert "get_organization_details" in call["prompt"] and "whoami" not in call["prompt"] and "Change nothing" in call["prompt"]
    assert list(call["config"]["mcpServers"]) == ["hubspot-bench"] and call["settings"] is False


def test_the_portal_identity_check_for_arm_b_also_runs_the_cli_whoami_without_printing_the_user():
    claude = IdentityClaude('{"mcp_portal_id": "149614891", "cli_portal_id": 149614891}')
    assert identity([("A", "sonnet"), ("B", "sonnet")], claude) == {"mcp": "149614891", "cli": "149614891"}
    call = claude.calls[0]
    assert "hubspot whoami | python3 -c" in call["prompt"] and '["portalId"]' in call["prompt"]
    assert call["settings"] is True and "GITHUB_TOKEN" not in call["env"], "the identity session is arm B as configured for the run"


@pytest.mark.parametrize(("reply", "cells", "message"), [
    ('{"mcp_portal_id": 111}', [("A", "sonnet")], "official MCP grant points at portal 111, not 149614891"),
    ('{"mcp_portal_id": 149614891, "cli_portal_id": 222}', [("B", "sonnet")], "hubspot CLI points at portal 222"),
    ('{"mcp_portal_id": 149614891}', [("B", "sonnet")], "no cli portal id"),
    ('{"unrelated": 1}', [("A", "opus")], "no mcp portal id"),
])
def test_a_portal_identity_mismatch_or_gap_aborts_before_any_session(reply, cells, message):
    with pytest.raises(ir.PreflightError, match=message):
        identity(cells, IdentityClaude(reply))


def test_a_timed_out_or_unstartable_identity_session_aborts():
    with pytest.raises(ir.PreflightError, match="timed out"):
        identity([("A", "sonnet")], IdentityClaude("{}", timed_out=True))

    def boom(*a):
        raise RuntimeError("claude CLI not found on PATH")
    with pytest.raises(ir.PreflightError, match="could not start"):
        identity([("A", "sonnet")], boom)


def test_the_official_server_must_show_connected_and_the_error_says_how_to_add_it(monkeypatch):
    out = {"rc": 0, "text": "hubspot-bench:\n  Scope: User config\n  Status: ✔ Connected\n"}
    monkeypatch.setattr(ir.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=out["rc"], stdout=out["text"], stderr=""))
    assert "Connected" in ir.check_official_server()
    out.update(rc=0, text="hubspot-bench:\n  Status: ! Needs authentication\n")
    with pytest.raises(ir.PreflightError, match="claude mcp add --scope user --transport http hubspot-bench https://mcp.hubspot.com"):
        ir.check_official_server()
    out.update(rc=1, text='No MCP server named "hubspot-bench".')
    with pytest.raises(ir.PreflightError, match="not connected"):
        ir.check_official_server()


# --- dry run end to end ---------------------------------------------------------------------------------------------


def run_main(tmp_path, monkeypatch, *extra):
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps(make_truth()), encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setenv(SECRET_ENV, SECRET)
    monkeypatch.setenv("HUBSPOT_TOKEN_149614891", "tok-test")
    code = ir.main(["--truth", str(truth), "--dry-run", "--out", str(out), *extra])
    return code, out


def test_a_dry_run_writes_the_whole_run_directory_from_fakes_and_leaks_no_secret(tmp_path, monkeypatch, capsys):
    code, out = run_main(tmp_path, monkeypatch)
    printed = capsys.readouterr().out
    assert code == 0 and "plan: 7 tasks x 7 cells x 1 rep(s) = 49 sessions" in printed
    rows = [json.loads(line) for line in (out / "sessions.jsonl").read_text().splitlines()]
    assert len(rows) == 49 and {r["failure_class"] for r in rows} == {None}
    assert {(r["arm"], r["model"]) for r in rows} == set(ir.CELLS)
    assert len(list(out.glob("events-*.jsonl"))) == 49 and (out / "jobs.jsonl").is_file() and (out / "sales-rules.md").is_file()
    meta = json.loads((out / "meta.json").read_text())
    for key in ("claude_version", "hubspot_cli_version", "models", "arm_expected_skills", "builtin_skills", "suffix_read",
                "suffix_write", "answer_prefix", "answers", "env_stripped", "arm_d_tier", "rules_hash", "as_of",
                "truth_sha256", "jobs_sha256", "sales_rules_sha256", "restore_cmd", "timeout", "portal_identity",
                "arm_d_force_preview_observed", "env_arm_b", "arm_b_fs_deny"):
        assert key in meta, key
    assert meta["models"] == {"sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"} and set(meta["answers"]) == set(JOB)
    assert "CLAUDE_CODE_SUBAGENT_MODEL_FORCE" in meta["env_stripped"] and "HUBSPOT_TOKEN_*" in meta["env_stripped"]
    blob = printed + "".join(p.read_text() for p in out.iterdir() if p.is_file())
    assert SECRET not in blob and "tok-test" not in blob


def test_the_main_loop_exits_4_on_the_first_leaked_session(tmp_path, monkeypatch, capsys):
    calls = []

    def leak(*args, **kw):
        calls.append(args)
        raise ir.HarnessLeak("C/sonnet job1: skills_unexpected: deal-management")

    monkeypatch.setattr(ir, "run_with_rerun", leak)
    code, _ = run_main(tmp_path, monkeypatch)
    assert code == 4 and len(calls) == 1
    assert "skills_unexpected: deal-management" in capsys.readouterr().out


def test_the_dry_run_records_arm_d_tier_as_preview_unless_the_flag_is_passed(tmp_path, monkeypatch, capsys):
    _, out = run_main(tmp_path, monkeypatch)
    assert json.loads((out / "meta.json").read_text())["arm_d_tier"] == "preview"
    assert ir.D_FORCE_PREVIEW_ENV in capsys.readouterr().out
    other = tmp_path / "again"
    other.mkdir()
    _, out2 = run_main(other, monkeypatch, "--arm-d-auto-tier")
    assert json.loads((out2 / "meta.json").read_text())["arm_d_tier"] == "auto"


def test_the_runner_aborts_with_exit_2_when_a_prompt_differs_from_the_truth_file(tmp_path, capsys):
    truth = make_truth()
    truth["job1"]["prompt"] = "Something else"
    path = tmp_path / "truth.json"
    path.write_text(json.dumps(truth), encoding="utf-8")
    assert ir.main(["--truth", str(path), "--dry-run", "--out", str(tmp_path / "o")]) == 2
    assert "job1 prompt differs" in capsys.readouterr().out


def test_only_and_arm_filters_shrink_the_dry_run(tmp_path, monkeypatch):
    code, out = run_main(tmp_path, monkeypatch, "--only", "job1,job3", "--arms", "C,D", "--models", "sonnet")
    assert code == 0
    rows = [json.loads(line) for line in (out / "sessions.jsonl").read_text().splitlines()]
    assert len(rows) == 4 and {r["task_id"] for r in rows} == {"job1", "job3"}


def test_each_session_waits_for_the_search_index_and_names_the_contact_job3_creates(ctx):
    reader = FakeReader()
    rec = run(ctx, "job1", ("C", "sonnet"), FakeClaude(ctx), reader=reader)
    assert rec["failure_class"] is None
    assert len(reader.index_calls) == 1 and reader.index_calls[0][0] == ctx.index_wait
    assert reader.index_calls[0][1] == ir.absent_contacts(ctx.truth)
    assert reader.index_calls[0][2] == ctx.truth["meta"]["as_of"]
    assert rec["search_index"]["ok"] is True


def test_a_search_index_that_never_catches_up_is_upstream_stale_state_and_claude_never_starts(ctx):
    claude = FakeClaude(ctx)
    rec = run(ctx, "job5", ("C", "sonnet"), claude, reader=FakeReader(index_lag=True))
    assert rec["failure_class"] == "upstream"
    assert rec["failure_detail"].startswith("stale_state: search index")
    assert claude.calls == []


@pytest.mark.parametrize("task_id", ["job3", "job5", "off_globex"])
def test_not_done_entries_may_be_text_or_objects_since_only_emptiness_is_graded(task_id):
    # Smoke runs saw five sessions write {"item": ..., "reason": ...}; a strict string schema failed the whole answer.
    schema = JOB[task_id]["answer_schema"]
    for not_done in (["none"], [{"item": "Move deal", "reason": "no access"}], []):
        answer = {**json.loads(JOB[task_id]["answer_example"]), "not_done": not_done}
        assert ir.validate_schema(answer, schema) is None


# --- resume and stop-before -------------------------------------------------------------------------------------------


def _keys(rows):
    return [(r["rep"], r["task_id"], r["arm"], r["model"]) for r in rows]


def test_a_resumed_run_finishes_exactly_the_missing_sessions_after_a_crash_mid_write(tmp_path, monkeypatch, capsys):
    code, out = run_main(tmp_path, monkeypatch)
    assert code == 0
    full = (out / "sessions.jsonl").read_text().splitlines()
    assert len(full) == 49
    # The machine died after session 20, halfway through writing session 21.
    (out / "sessions.jsonl").write_text("\n".join(full[:20]) + "\n" + full[20][: len(full[20]) // 2], encoding="utf-8")
    truth = tmp_path / "truth.json"
    code = ir.main(["--truth", str(truth), "--dry-run", "--resume", str(out)])
    assert code == 0
    assert "resume: 20 session(s) already finished" in capsys.readouterr().out
    rows = ir.read_sessions(out / "sessions.jsonl")
    assert len(rows) == 49 and len(set(_keys(rows))) == 49
    assert [r["n"] for r in rows] == list(range(1, 50))
    meta = json.loads((out / "meta.json").read_text())
    assert len(meta["resumes"]) == 1 and meta["resumes"][0]["sessions_done"] == 20
    assert (out / "sessions.jsonl.partial").read_text() == full[20][: len(full[20]) // 2] + "\n"


def test_a_resume_with_different_settings_is_refused(tmp_path, monkeypatch, capsys):
    code, out = run_main(tmp_path, monkeypatch, "--only", "job1")
    assert code == 0
    truth = tmp_path / "truth.json"
    assert ir.main(["--truth", str(truth), "--dry-run", "--resume", str(out), "--only", "job1,job2"]) == 2
    assert "different tasks" in capsys.readouterr().out
    assert ir.main(["--truth", str(truth), "--dry-run", "--resume", str(tmp_path / "nowhere")]) == 2


def test_stop_before_exits_5_without_starting_a_session_and_the_run_resumes(tmp_path, monkeypatch, capsys):
    code, out = run_main(tmp_path, monkeypatch, "--only", "job1", "--stop-before", "2000-01-01T00:00:00Z")
    assert code == 5 and "--resume" in capsys.readouterr().out
    assert ir.read_sessions(out / "sessions.jsonl") == []
    truth = tmp_path / "truth.json"
    assert ir.main(["--truth", str(truth), "--dry-run", "--resume", str(out), "--only", "job1"]) == 0
    assert len(ir.read_sessions(out / "sessions.jsonl")) == 7


def test_a_lone_upstream_attempt_is_run_again_but_a_rerun_one_is_done():
    base = {"rep": 1, "task_id": "job1", "arm": "A", "model": "sonnet"}
    assert ir.completed_keys([{**base, "failure_class": "upstream", "attempt": 1}]) == set()
    assert ir.completed_keys([{**base, "failure_class": "upstream", "attempt": 1},
                              {**base, "failure_class": "upstream", "attempt": 2}]) == {(1, "job1", "A", "sonnet")}
    assert ir.completed_keys([{**base, "failure_class": "model", "attempt": 1}]) == {(1, "job1", "A", "sonnet")}
