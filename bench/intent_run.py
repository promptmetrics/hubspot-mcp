#!/usr/bin/env python3
"""Intent benchmark runner: arms A (official MCP), B (official MCP + HubSpot CLI), C (/mcp/intent), D (/mcp/grouped).

One headless Claude Code session per task x arm x model x rep, strictly sequential. Each session runs from a
fresh temp directory that holds only its arm's skills; the runner records which skills the session saw and
which it invoked, and fails the session as a harness error when an arm sees skills it should not. After a write
task the runner reads the portal's end state (read-only), then runs restore.py; after a read task it checks that
nothing changed. The contract lives in bench/intent_interface.md; usage and the one-time setup are in
bench/README.md.

    .venv/bin/python bench/intent_run.py --truth <truth.json> --rules-dir <dir> --smoke
    .venv/bin/python bench/intent_run.py --truth <truth.json> --dry-run        # fakes only

Transcripts land in bench/runs/<UTC stamp>-intent/ (gitignored: they carry portal data).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import Surface, measure_tools_list  # noqa: E402
from run import _walk, summarize  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_PORTAL = "149614891"
OFFICIAL_URL = "https://mcp.hubspot.com"
EXPECTED_CLI = "0.17.0"
EXPECTED_CLAUDE = "2.1.314"
RULES_AS_OF_RE = re.compile(r"As-of date: (\d{4}-\d{2}-\d{2})")
FINGERPRINT_RE = re.compile(r"Rules fingerprint: ([0-9a-f]{12})")

CELLS: list[tuple[str, str]] = [("A", "sonnet"), ("A", "opus"), ("B", "sonnet"), ("B", "opus"),
                                ("C", "sonnet"), ("C", "opus"), ("D", "sonnet")]
MODEL_IDS = {"sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}

# Skills Claude Code 2.1.314 lists in every session whatever the configuration (observed with a dummy skill).
BUILTIN_SKILLS = frozenset({
    "deep-research", "design", "design-sync", "dataviz", "update-config", "verify", "debug", "code-review", "simplify",
    "batch", "fewer-permission-prompts", "doctor", "loop", "schedule", "claude-api", "workflow-authoring", "run",
    "run-skill-generator", "plugin-authoring",
})

# Environment the model's own tools could read: the subagent model pins (they would change the model under test)
# and every credential the harness holds. HUBSPOT_TOKEN_* is matched by prefix; the --secret-env name is added.
STRIP_ENV = frozenset({"CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "HUBSPOT_PRIVATE_APP_TOKEN",
                       "SEED_DEMO_USER_EMAIL", "AI_GATEWAY_API_KEY"})
STRIP_PREFIX = "HUBSPOT_TOKEN_"
# Arm B's Bash tool inherits its session's environment, so B gets an allowlist instead of the operator's whole
# shell: anything not named here (GITHUB_TOKEN, VERCEL_TOKEN, other AI keys, HUBSPOT_* names) never reaches the
# model. ANTHROPIC_* and CLAUDE_* settings stay (the claude CLI reads them), except names that look like credentials:
# the session authenticates through the operator's `claude login`, not an exported key. (CLAUDE_CODE_SUBPROCESS_ENV_SCRUB
# would scrub Claude's own credentials too, but it forces permission mode "default" in claude 2.1.314: do not set it.)
B_ENV_ALLOW = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TERM", "TMPDIR", "TZ", "NO_COLOR", "ENABLE_TOOL_SEARCH",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy",
    "https_proxy", "no_proxy", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
})
B_ENV_ALLOW_PREFIXES = ("LC_", "ANTHROPIC_", "CLAUDE_")
CREDENTIAL_NAME_RE = re.compile(r"(?i)token|secret|password|passwd|credential|key")
D_FORCE_PREVIEW_ENV = "HUBSPOT_MCP_GROUPED_FORCE_PREVIEW"
RESULT_TEXT_CAP = 60000  # the full final reply, for graders that scan prose (summary.final_text keeps 8000)

SUFFIX_READ = "Do not change anything in HubSpot."
SUFFIX_WRITE = "You have my approval to make these changes in HubSpot; if a tool asks for confirmation, confirm it."
ANSWER_PREFIX = (
    "When you are done, end your reply with one fenced ```json block and nothing after it, in exactly this shape. "
    "Dates are YYYY-MM-DD, amounts are plain numbers, and deals are named by their exact deal name."
)

# server name, MCP path on --server (None: the official URL), --tools, --setting-sources, skills source, built-in tools
ARMS: dict[str, dict[str, Any]] = {
    "A": {"server": "hubspot-bench", "path": None, "tools": "", "sources": "", "skills": None, "builtin_tools": frozenset()},
    "B": {"server": "hubspot-bench", "path": None, "tools": "Bash,Skill,Read", "sources": "project", "skills": "b",
          "builtin_tools": frozenset({"Bash", "Skill", "Read"})},
    "C": {"server": "hubspot-intent", "path": "/mcp/intent", "tools": "Skill", "sources": "project", "skills": "cd",
          "builtin_tools": frozenset({"Skill"})},
    "D": {"server": "hubspot-grouped", "path": "/mcp/grouped", "tools": "Skill", "sources": "project", "skills": "cd",
          "builtin_tools": frozenset({"Skill"})},
}
EXPECTED_TOOL_COUNTS = {"C": 13, "D": 12}

C_WRITE_TOOLS = frozenset({"pronova_apply_changes", "pronova_propose_changes", "pronova_log_meeting_outcome",
                           "pronova_fix_past_due_close_dates"})
D_WRITE_TOOLS = frozenset({"manage_deal", "manage_contact", "manage_engagement"})
CLI_WRITE_RE = re.compile(r"\b(create|update|delete|batch|associate)\b", re.I)
CONFIRM_RE = re.compile(r"confirm", re.I)

UPSTREAM_RE = re.compile(
    r"(?i)(?:\b(?:http|status(?: code)?|error)\W{0,3}(?:429|5\d\d)\b"
    r"|\b(?:429|5\d\d)\s+(?:too many|internal server|bad gateway|service unavailable|gateway time-?out)"
    r"|too many requests|overloaded|rate[ _-]?limit|econnreset|invalid_grant|token (?:has )?expired|needs[- ]auth)"
)


LEAK_RE = re.compile(r"(?:^|; )(?:skills_unexpected|tools):")


class PreflightError(RuntimeError):
    """A pre-flight check failed; the run aborts with exit code 2 before any session starts."""


class HarnessLeak(RuntimeError):
    """A session saw skills or built-in tools its arm must not have; the leak would repeat, so the run stops (exit 4)."""


# --- shared text ---------------------------------------------------------------------------------------------


def load_jobs(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def suffix_for(kind: str) -> str:
    return SUFFIX_WRITE if kind == "write" else SUFFIX_READ


def answer_instruction(job: dict[str, Any]) -> str:
    return f"{ANSWER_PREFIX} {job['answer_rule']}\n```json\n{job['answer_example']}\n```"


def compose_prompt(job: dict[str, Any]) -> str:
    """The rep prompt verbatim, then the suffix for its kind, then the answer block. Identical for every arm."""
    return f"{job['prompt']}\n\n{suffix_for(job['kind'])}\n\n{answer_instruction(job)}"


def truth_at(truth: dict[str, Any], dotted: str) -> Any:
    node: Any = truth
    for part in dotted.split("."):
        node = node[part]
    return node


def target_names(task: dict[str, Any], truth: dict[str, Any]) -> list[str]:
    """Deals a write task must change, from the truth file (read tasks have none)."""
    node = truth_at(truth, task["truth_key"])
    if task["id"] == "job5":
        return [d["dealname"] for d in node["deals"]]
    if task["id"] in ("job3", "off_globex"):
        return [node["target_deal"]["dealname"]]
    return []


# --- answer block --------------------------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^[ \t]*```json[ \t]*\n(.*?)^[ \t]*```[ \t]*$", re.S | re.M)
_JSON_TYPES: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> str | None:
    """First violation path of a JSON-Schema subset (type, required, properties, items, enum), else None."""
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_JSON_TYPES[t](value) for t in types):
            return path
    if "enum" in schema and value not in schema["enum"]:
        return path
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                return f"{path}.{key}"
        for key, sub in schema.get("properties", {}).items():
            if key in value and (bad := validate_schema(value[key], sub, f"{path}.{key}")):
                return bad
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            if bad := validate_schema(item, schema["items"], f"{path}[{i}]"):
                return bad
    return None


def extract_answer_block(text: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse the LAST fenced ```json block. No fallback to earlier blocks."""
    blocks = _FENCE_RE.findall(text or "")
    out: dict[str, Any] = {"parsed": None, "raw": None, "block_count": len(blocks), "error": None}
    if not blocks:
        out["error"] = "no_block"
        return out
    out["raw"] = blocks[-1].strip()
    try:
        out["parsed"] = json.loads(out["raw"])
    except ValueError:
        out["error"] = "invalid_json"
        return out
    if schema is not None and (bad := validate_schema(out["parsed"], schema)):
        out["error"] = f"schema: {bad}"
    return out


# --- session setup -------------------------------------------------------------------------------------------


def skill_names(source: Path) -> list[str]:
    """The ``name:`` of every ``*/SKILL.md`` under ``source`` (the directory name when the frontmatter has none)."""
    names = []
    for skill in sorted(source.glob("*/SKILL.md")):
        name = skill.parent.name
        lines = skill.read_text(encoding="utf-8").splitlines()
        if lines and lines[0].strip() == "---":
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip("'\"") or name
        names.append(name)
    return names


def mcp_config(arm: str, server: str, secret: str | None) -> dict[str, Any]:
    spec = ARMS[arm]
    url = OFFICIAL_URL if spec["path"] is None else server.rstrip("/") + spec["path"]
    entry: dict[str, Any] = {"type": "http", "url": url}
    if spec["path"] is not None and secret:
        entry["headers"] = {"Authorization": f"Bearer {secret}"}
    return {"mcpServers": {spec["server"]: entry}}


def masked(config: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(config))
    for entry in out.get("mcpServers", {}).values():
        if "headers" in entry:
            entry["headers"] = {k: "Bearer ***" for k in entry["headers"]}
    return out


def arm_argv_extra(arm: str, model_id: str, rules_path: Path) -> list[str]:
    spec = ARMS[arm]
    return ["--model", model_id, "--append-system-prompt-file", str(rules_path),
            "--setting-sources", spec["sources"], "--tools", spec["tools"]]


def default_deny_paths() -> list[Path]:
    """What arm B's Bash and Read must not reach: the workspace that holds the answer key, the seed output, the run
    transcripts and the thin skills, plus the operator's Claude, SSH and AWS directories."""
    home = Path.home()
    return [HERE.parent.parent, home / ".claude", home / ".ssh", home / ".aws"]


def isolation_settings(deny: list[Path]) -> dict[str, Any]:
    """Project settings written into arm B's session directory. Verified with claude 2.1.314 under
    ``--permission-mode bypassPermissions``: the Read deny rules and the sandbox's ``denyRead`` both hold, and
    ``allowUnsandboxedCommands: false`` stops the model from retrying a denied command outside the sandbox."""
    paths = sorted({str(p) for p in deny})
    return {
        "permissions": {"deny": [f"Read(/{p}/**)" for p in paths]},
        "sandbox": {
            "enabled": True,
            "allowUnsandboxedCommands": False,
            # The CLI refreshes its OAuth session in Application Support; without write access there every call
            # fails with "Session is no longer valid" once the access token expires.
            "filesystem": {"denyRead": paths, "allowWrite": [str(Path.home() / ".hubspot"),
                                                             str(Path.home() / "Library" / "Application Support" / "hubspot")]},
        },
    }


def base_argv(claude: str, prompt: str, cfg_path: str) -> list[str]:
    return [claude, "-p", prompt, "--output-format", "stream-json", "--verbose", "--no-session-persistence",
            "--strict-mcp-config", "--mcp-config", str(cfg_path), "--permission-mode", "bypassPermissions"]


def session_env(arm: str, base_env: dict[str, str], secret_env: str, cli_dir: str,
                workdir: Path | None = None) -> tuple[dict[str, str], list[str]]:
    """The environment a session's tools inherit: no subagent-model pins, no credentials. Arm B, whose Bash tool can
    run ``env``, starts from an allowlist and, given ``workdir``, runs zsh with an empty ZDOTDIR so the operator's shell
    profile (which often exports API keys) is not sourced either; the other arms have no shell and start from the
    operator's environment."""
    env = dict(base_env)
    if arm == "B":
        env = {k: v for k, v in env.items()
               if (k in B_ENV_ALLOW or k.startswith(B_ENV_ALLOW_PREFIXES)) and not CREDENTIAL_NAME_RE.search(k)}
    for name in list(env):
        if name in STRIP_ENV or name == secret_env or name.startswith(STRIP_PREFIX):
            del env[name]
    removed = sorted(set(base_env) - set(env))
    if arm == "B":
        env["HUBSPOT_NO_AUTO_UPGRADE"] = "1"
        zsh = shutil.which("zsh", path=env.get("PATH"))
        if workdir is not None and zsh:
            env["SHELL"], env["ZDOTDIR"] = zsh, str(workdir)
        env["PATH"] = cli_dir + os.pathsep + env.get("PATH", "")
    return env, removed


def prepare_cwd(cwd: Path, arm: str, config: dict[str, Any], skills_src: Path | None,
                deny: list[Path] | None = None) -> Path:
    """Write the MCP config and copy exactly this arm's skills; arm A gets no ``.claude`` directory. Arm B, the only
    arm with a shell and a file reader, also gets the filesystem deny settings."""
    cfg_path = cwd / "mcp-config.json"
    cfg_path.write_text(json.dumps(config), encoding="utf-8")
    if "Bash" in ARMS[arm]["builtin_tools"] or "Read" in ARMS[arm]["builtin_tools"]:
        (cwd / ".claude").mkdir(exist_ok=True)
        (cwd / ".claude" / "settings.json").write_text(
            json.dumps(isolation_settings(default_deny_paths() if deny is None else deny), indent=1), encoding="utf-8")
    if ARMS[arm]["skills"] and skills_src is not None:
        target = cwd / ".claude" / "skills"
        target.mkdir(parents=True, exist_ok=True)
        for skill in sorted(skills_src.glob("*/SKILL.md")):
            shutil.copytree(skill.parent, target / skill.parent.name)
    return cfg_path


# --- reading a session ---------------------------------------------------------------------------------------


def init_info(events: list[dict[str, Any]]) -> dict[str, Any]:
    for ev in events:
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            return {"tools": list(ev.get("tools") or []), "mcp_servers": list(ev.get("mcp_servers") or []),
                    "skills": list(ev.get("skills") or []), "model": ev.get("model") or ""}
    return {"tools": [], "mcp_servers": [], "skills": [], "model": "", "missing": True}


def check_init(arm: str, init: dict[str, Any], expected_skills: set[str], builtin: frozenset[str] = BUILTIN_SKILLS,
               foreign: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    """Skills, built-in tools and MCP status of one session against what its arm may see.

    A leak is a skill another arm provides (``foreign``): HubSpot's CLI skills outside arm B, or the thin Pronova skills
    in A or B. A skill no arm provides and not in BUILTIN_SKILLS is Claude Code's own and reaches every arm alike
    (``send-to-project`` appeared on 2026-10-31 with 2.1.314 unchanged), so it is recorded as unknown, not a failure."""
    spec = ARMS[arm]
    seen = set(init["skills"])
    extra = seen - builtin - expected_skills
    unexpected = sorted(extra & set(foreign))
    unknown = sorted(extra - set(foreign))
    missing = sorted(expected_skills - seen)
    non_mcp = {t for t in init["tools"] if not t.startswith("mcp__")}
    tools_ok = non_mcp == set(spec["builtin_tools"])
    servers = {s.get("name"): s.get("status") for s in init["mcp_servers"] if isinstance(s, dict)}
    mcp_bad = [f"{spec['server']}:{servers.get(spec['server'], 'missing')}"] if servers.get(spec["server"]) != "connected" else []
    mcp_bad += [f"{n}:{s}" for n, s in servers.items() if n != spec["server"] and s != "connected"]
    return {"skills_unexpected": unexpected, "skills_unknown": unknown, "skills_missing": missing, "tools_ok": tools_ok,
            "tools_seen": sorted(non_mcp), "mcp_bad": mcp_bad}


def skills_loaded(summary: dict[str, Any]) -> list[str]:
    """Skills the model invoked, in order, duplicates kept. The Skill tool's input key is ``skill`` (observed)."""
    out = []
    for call in summary["tool_calls"]:
        if call.get("name") == "Skill" and isinstance(call.get("input"), dict):
            name = call["input"].get("skill") or call["input"].get("name")
            if name:
                out.append(str(name))
    return out


def final_result_text(events: list[dict[str, Any]]) -> str:
    """The full ``result`` string; the text of the last assistant message when there is no result event."""
    for ev in reversed(events):
        if ev.get("type") == "result" and isinstance(ev.get("result"), str):
            return ev["result"]
    for ev in reversed(events):
        if ev.get("type") == "assistant":
            texts = [b.get("text", "") for b in ev.get("message", {}).get("content", []) or []
                     if isinstance(b, dict) and b.get("type") == "text"]
            if texts:
                return "\n".join(texts)
    return ""


def split_tool(name: str) -> tuple[str, str]:
    parts = str(name).split("__", 2)
    return (parts[1], parts[2]) if len(parts) == 3 and parts[0] == "mcp" else ("", str(name))


def mcp_calls(summary: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for c in summary["tool_calls"]:
        server, tool = split_tool(c.get("name", ""))
        if server:
            out.append({"server": server, "tool": tool, "is_error": bool(c.get("is_error")), "latency_ms": c.get("latency_ms", 0)})
    return out


def cli_calls(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"command": str((c.get("input") or {}).get("command", ""))[:300], "is_error": bool(c.get("is_error"))}
            for c in summary["tool_calls"] if c.get("name") == "Bash"]


def _is_write_call(arm: str, call: dict[str, Any]) -> bool:
    server, tool = split_tool(call.get("name", ""))
    args = call.get("input") if isinstance(call.get("input"), dict) else {}
    if arm in ("A", "B"):
        if server:
            return tool.startswith("manage_")
        return call.get("name") == "Bash" and bool(CLI_WRITE_RE.search(str(args.get("command", ""))))
    if arm == "C":
        return tool in C_WRITE_TOOLS
    if tool in D_WRITE_TOOLS:
        return True
    if tool == "associations":
        return args.get("action") in ("create", "remove")
    return tool == "write_actions" and args.get("action") == "approve"


def confirmations(summary: dict[str, Any], arm: str) -> dict[str, int]:
    """Tool results that ask for a confirmation, and calls to write-capable tools. Reported, never scored.

    C and D (and any gate that answers ``status: preview``): a result carrying a preview or a declined inline
    confirmation. A and B: a write tool's result whose text mentions "confirm" (UNVERIFIED against a real
    official-MCP write result; pin this once one exists)."""
    requested = 0
    writes = 0
    for call in summary["tool_calls"]:
        write = _is_write_call(arm, call)
        writes += write
        text = call.get("result_text") or ""
        if arm in ("C", "D"):
            try:
                data = json.loads(text)
            except ValueError:
                continue
            if any(isinstance(d, dict) and (d.get("status") == "preview" or d.get("rejected")) for d in _walk(data)):
                requested += 1
        elif write and CONFIRM_RE.search(text):
            requested += 1
    return {"requested": requested, "write_tool_calls": writes}


def classify_failure(*, checks: dict[str, Any] | None, init_missing: bool, stderr: str, result: dict[str, Any],
                     error_tool_texts: list[str], timed_out: bool, returncode: int,
                     answer_error: str | None, model_mismatch: str | None = None) -> tuple[str | None, str]:
    """(failure_class, detail). Harness beats upstream beats model; a wrong answer is graded, not a failure.

    Upstream signatures are looked for in stderr, in the result text of a session that ended in error (the
    model's own final answer in a successful session is never scanned: a 429 figure or the words "rate limit" in
    a CRM note would otherwise look like an outage) and in two or more failed tool results."""
    problems = []
    if checks:
        if checks["skills_unexpected"]:
            problems.append("skills_unexpected: " + ",".join(checks["skills_unexpected"]))
        if checks["skills_missing"]:
            problems.append("skills_missing: " + ",".join(checks["skills_missing"]))
        if not checks["tools_ok"]:
            problems.append("tools: " + ",".join(checks["tools_seen"]))
    if model_mismatch:
        problems.append("model_mismatch: " + model_mismatch)
    if problems:
        return "harness", "; ".join(problems)
    if checks and checks["mcp_bad"]:
        return "upstream", "mcp_" + ",".join(checks["mcp_bad"])
    errored = bool(result.get("is_error")) or (result.get("subtype") not in (None, "success"))
    if UPSTREAM_RE.search(stderr or ""):
        return "upstream", "signature_in_stderr"
    if errored and UPSTREAM_RE.search(str(result.get("result") or "")):
        return "upstream", "signature_in_result"
    if sum(1 for t in error_tool_texts if UPSTREAM_RE.search(t)) >= 2:
        return "upstream", "signature_in_tool_results"
    if timed_out:
        return "model", "timeout"
    if init_missing:
        return "harness", f"no_init_event (exit {returncode})"
    if answer_error in ("no_block", "invalid_json"):
        subtype = result.get("subtype")
        return "model", answer_error + (f" (result {subtype})" if subtype not in (None, "success") else "")
    return None, ""


# --- running claude -------------------------------------------------------------------------------------------


def _parse_events(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            events.append({"type": "nonjson", "raw": line[:500]})
    return events


def run_claude(prompt: str, cfg_path: str, argv_extra: list[str], cwd: Path, env: dict[str, str], timeout: int) -> dict[str, Any]:
    claude = shutil.which("claude")
    if not claude:
        raise RuntimeError("claude CLI not found on PATH")
    cmd = [*base_argv(claude, prompt, cfg_path), *argv_extra]
    started = time.perf_counter()
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, no shell; the prompt is our own task text
            cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout
        )
        stdout, stderr, code, timed_out = proc.stdout, proc.stderr, proc.returncode, False
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or ""
        stdout = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
        stderr, code, timed_out = "timeout", -1, True
    wall_ms = int((time.perf_counter() - started) * 1000)
    return {"events": _parse_events(stdout), "stderr": (stderr or "")[-4000:], "returncode": code,
            "wall_ms": wall_ms, "timed_out": timed_out}


def make_restore_fn(cmd: str, cwd: str, token_env: str, base_env: dict[str, str]) -> Callable[[], dict[str, Any]]:
    """restore.py reads its token from HUBSPOT_PRIVATE_APP_TOKEN; hand it the value of ``--token-env`` when unset."""
    def restore() -> dict[str, Any]:
        env = dict(base_env)
        if token_env in env and not env.get("HUBSPOT_PRIVATE_APP_TOKEN"):
            env["HUBSPOT_PRIVATE_APP_TOKEN"] = env[token_env]
        proc = subprocess.run(  # noqa: S603 - argv from shlex.split of the operator's own --restore-cmd
            shlex.split(cmd), cwd=cwd, env=env, capture_output=True, text=True, timeout=600
        )
        return {"exit_code": proc.returncode, "output_tail": ((proc.stdout or "") + (proc.stderr or ""))[-2000:]}
    return restore


def restore_with_retry(restore_fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run restore; one retry when it exits non-zero. ``ok`` is false when both attempts failed."""
    result: dict[str, Any] = {}
    for attempt in (1, 2):
        try:
            result = restore_fn()
        except Exception as exc:  # noqa: BLE001 - a restore that cannot start is reported, not raised
            result = {"exit_code": -1, "output_tail": f"{type(exc).__name__}: {exc}"[-2000:]}
        if result["exit_code"] == 0:
            return {"ran": True, "ok": True, "attempts": attempt, **result}
    return {"ran": True, "ok": False, "attempts": 2, **result}


# --- one session ----------------------------------------------------------------------------------------------


@dataclass
class Ctx:
    out: Path
    truth: dict[str, Any]
    server: str = "http://127.0.0.1:8000"
    secret: str | None = None
    secret_env: str = "HUBSPOT_MCP_SERVER_SECRET"  # noqa: S105 - the env var's name, not a secret
    rules_path: Path = field(default_factory=lambda: Path("sales-rules.md"))
    skills_b: Path | None = None
    skills_cd: Path | None = None
    expected_skills: dict[str, set[str]] = field(default_factory=dict)
    model_ids: dict[str, str] = field(default_factory=lambda: dict(MODEL_IDS))
    base_env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    cli_dir: str = str(Path.home() / ".hubspot" / "bin")
    deny_paths: list[Path] = field(default_factory=default_deny_paths)
    timeout: int = 1200
    no_restore: bool = False
    rerun_wait: float = 30.0
    index_wait: float = 300.0
    sleep: Callable[[float], None] = time.sleep


def _skills_src(ctx: Ctx, arm: str) -> Path | None:
    key = ARMS[arm]["skills"]
    return {"b": ctx.skills_b, "cd": ctx.skills_cd}.get(key) if key else None


def run_session(task: dict[str, Any], cell: tuple[str, str], rep: int, ctx: Ctx, *, claude_fn, reader, restore_fn,
                n: int = 1, attempt: int = 1, rerun_of: int | None = None) -> dict[str, Any]:
    arm, model = cell
    prompt = compose_prompt(task)
    expected = set(ctx.expected_skills.get(arm, set()))
    record: dict[str, Any] = {
        "n": n, "rep": rep, "task_id": task["id"], "kind": task["kind"], "truth_key": task["truth_key"],
        "arm": arm, "model": model, "model_id": ctx.model_ids[model], "model_observed": None,
        "attempt": attempt, "rerun_of": rerun_of, "since": None, "wall_ms": 0, "returncode": None,
        "prompt_sent": prompt, "init": {"tools": [], "mcp_servers": [], "skills": [], "model": ""},
        "skills_expected": sorted(expected), "skills_unexpected": [], "skills_missing": [], "skills_loaded": [],
        "tools_ok": None, "summary": None, "mcp_calls": [], "cli_calls": [],
        "answer_block": {"parsed": None, "raw": None, "block_count": 0, "error": None}, "result_text": "",
        "confirmations": {"requested": 0, "write_tool_calls": 0}, "end_state": None, "read_check": None,
        "restore": {"ran": False, "exit_code": None, "output_tail": "", "attempts": 0},
        "failure_class": None, "failure_detail": "", "stderr_tail": "",
    }

    stale = _start_state(reader, restore_fn, ctx, record)
    if stale:
        record.update(failure_class="upstream", failure_detail=stale)
        return record

    cwd = Path(tempfile.mkdtemp(prefix="hs-intent-"))
    try:
        cfg_path = prepare_cwd(cwd, arm, mcp_config(arm, ctx.server, ctx.secret), _skills_src(ctx, arm), ctx.deny_paths)
        env, _ = session_env(arm, ctx.base_env, ctx.secret_env, ctx.cli_dir, cwd)
        record["since"] = datetime.now(UTC).isoformat()
        try:
            raw = claude_fn(prompt, str(cfg_path), arm_argv_extra(arm, ctx.model_ids[model], ctx.rules_path), cwd, env, ctx.timeout)
        except RuntimeError as exc:
            record.update(failure_class="harness", failure_detail=str(exc)[:300])
            return record
    finally:
        shutil.rmtree(cwd, ignore_errors=True)

    events = raw["events"]
    (ctx.out / f"events-{n:03d}-{task['id']}-{arm}-{model}-r{rep}-a{attempt}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8")
    init = init_info(events)
    foreign = set().union(*ctx.expected_skills.values()) - expected if ctx.expected_skills else set()
    checks = check_init(arm, init, expected, foreign=foreign) if not init.get("missing") else None
    summary = summarize(events)
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    result_text = final_result_text(events)
    answer = extract_answer_block(result_text, task["answer_schema"])
    calls = mcp_calls(summary)
    # summarize() filters on run.py's "mcp__hubspot__" prefix, which never matches this runner's server names.
    summary["hubspot_tool_names"] = [c["tool"] for c in calls]
    summary["hubspot_tool_errors"] = sum(1 for c in calls if c["is_error"])
    requested_model, observed_model = ctx.model_ids[model], init["model"]
    mismatch = f"{observed_model} != {requested_model}" if observed_model and not observed_model.startswith(requested_model) else None
    record.update(result_text=result_text[:RESULT_TEXT_CAP],
        wall_ms=raw["wall_ms"], returncode=raw["returncode"], stderr_tail=(raw["stderr"] or "")[-4000:],
        init={k: init[k] for k in ("tools", "mcp_servers", "skills", "model")}, model_observed=init["model"] or None,
        skills_loaded=skills_loaded(summary), mcp_calls=calls, cli_calls=cli_calls(summary),
        answer_block=answer, confirmations=confirmations(summary, arm), summary=summary,
    )
    if checks:
        record.update(skills_unexpected=checks["skills_unexpected"], skills_unknown=checks["skills_unknown"],
                      skills_missing=checks["skills_missing"], tools_ok=checks["tools_ok"])
    errors = [c.get("result_text") or "" for c in summary["tool_calls"] if c.get("is_error")]
    record["failure_class"], record["failure_detail"] = classify_failure(
        checks=checks, init_missing=bool(init.get("missing")), stderr=raw["stderr"] or "", result=result,
        error_tool_texts=errors, timed_out=bool(raw.get("timed_out")), returncode=raw["returncode"], answer_error=answer["error"],
        model_mismatch=mismatch)

    _after_session(task, record, ctx, reader, restore_fn)
    check = record["read_check"]
    summary["unchanged"] = check["ok"] if isinstance(check, dict) and "ok" in check else None
    return record


def _start_state(reader, restore_fn, ctx: Ctx, record: dict[str, Any]) -> str:
    """Empty string when the seeded records hold their baseline values; else why not (after one restore)."""
    try:
        state = reader.verify_baseline()
        if not state["ok"] and not ctx.no_restore:
            record["restore"] = restore_with_retry(restore_fn)
            if record["restore"]["ok"]:
                state = reader.verify_baseline()
        if not state["ok"]:
            return f"stale_state: {len(state['differs'])} seeded record(s) differ from baseline"
        # The records hold their baseline values; the arms find them through search, which lags a restore.
        index = reader.wait_search_index(ctx.index_wait, absent_contacts=absent_contacts(ctx.truth),
                                         as_of=(ctx.truth.get("meta") or {}).get("as_of"))
        record["search_index"] = index
        if not index["ok"]:
            return f"stale_state: search index still differs after {index['waited_s']}s ({len(index['differs'])} record(s))"
        return ""
    except Exception as exc:  # noqa: BLE001 - reader errors are upstream (HubSpot), recorded not raised
        return f"stale_state_check: {type(exc).__name__}: {str(exc)[:200]}"


def absent_contacts(truth: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Contacts a write task creates that restore.py archives, which search must stop returning (job 3's new contact)."""
    contact = ((truth.get("job3") or {}).get("expected_end_state") or {}).get("contact") or {}
    if contact.get("pre_existing") is False and contact.get("firstname") and contact.get("lastname"):
        return ((contact["firstname"], contact["lastname"]),)
    return ()


def _after_session(task: dict[str, Any], record: dict[str, Any], ctx: Ctx, reader, restore_fn) -> None:
    """End-state read and restore after a write task; a changed-nothing check after a read task."""
    needs_restore = False
    if task["kind"] == "write":
        needs_restore = True
        try:
            record["end_state"] = reader.end_state(record["since"], target_names(task, ctx.truth))
        except Exception as exc:  # noqa: BLE001 - recorded as an upstream failure; the restore below still runs
            record["end_state"] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
            record["failure_class"] = record["failure_class"] or "upstream"
            record["failure_detail"] = record["failure_detail"] or "end_state_read"
    else:
        try:
            record["read_check"] = reader.read_check(record["since"])
        except Exception as exc:  # noqa: BLE001 - recorded as an upstream failure
            record["read_check"] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
            record["failure_class"] = record["failure_class"] or "upstream"
            record["failure_detail"] = record["failure_detail"] or "read_check"
        else:
            if not record["read_check"]["ok"]:
                needs_restore = True
                if record["failure_class"] not in ("harness", "upstream"):
                    record["failure_class"], record["failure_detail"] = "model", "wrote_on_read_task"
    if needs_restore:
        record["restore"] = (restore_with_retry(restore_fn) if not ctx.no_restore
                             else {"ran": False, "exit_code": None, "output_tail": "", "attempts": 0})


def run_with_rerun(task, cell, rep, ctx: Ctx, counter: list[int], *, claude_fn, reader, restore_fn, sink) -> bool:
    """One session, plus one re-run when it failed upstream. ``sink(record)`` stores each attempt.
    Returns False when a restore failed twice (the portal is dirty and the run must stop). Raises HarnessLeak
    after a session that saw skills or built-in tools its arm must not have (the restore has already run)."""
    counter[0] += 1
    first = run_session(task, cell, rep, ctx, claude_fn=claude_fn, reader=reader, restore_fn=restore_fn, n=counter[0])
    sink(first)
    if first["restore"].get("ran") and not first["restore"].get("ok", True):
        return False
    if first["failure_class"] == "harness" and LEAK_RE.search(first["failure_detail"]):
        raise HarnessLeak(f"{cell[0]}/{cell[1]} {task['id']}: {first['failure_detail']}")
    if first["failure_class"] == "upstream":
        ctx.sleep(ctx.rerun_wait)
        counter[0] += 1
        second = run_session(task, cell, rep, ctx, claude_fn=claude_fn, reader=reader, restore_fn=restore_fn,
                             n=counter[0], attempt=2, rerun_of=first["n"])
        sink(second)
        if second["restore"].get("ran") and not second["restore"].get("ok", True):
            return False
    return True


def rotated(cells: list[tuple[str, str]], rep: int) -> list[tuple[str, str]]:
    k = rep % len(cells)
    return cells[k:] + cells[:k]


def session_plan(tasks: list[dict[str, Any]], cells: list[tuple[str, str]], reps: int):
    for rep in range(1, reps + 1):
        for task in tasks:
            for cell in rotated(cells, rep):
                yield rep, task, cell


# --- dry-run fakes ---------------------------------------------------------------------------------------------


def dry_run_claude(task: dict[str, Any], expected_skills: dict[str, set[str]]):
    """A claude_fn that returns a plausible session without starting claude."""
    def fake(prompt, cfg_path, argv_extra, cwd, env, timeout):
        sources = argv_extra[argv_extra.index("--setting-sources") + 1]
        tools_arg = argv_extra[argv_extra.index("--tools") + 1]
        arm = next(a for a, s in ARMS.items() if s["sources"] == sources and s["tools"] == tools_arg)
        server = next(iter(json.loads(Path(cfg_path).read_text())["mcpServers"]))
        events = [
            {"type": "system", "subtype": "init", "tools": sorted(ARMS[arm]["builtin_tools"]),
             "model": argv_extra[argv_extra.index("--model") + 1],
             "mcp_servers": [{"name": server, "status": "connected"}],
             "skills": sorted(BUILTIN_SKILLS | expected_skills.get(arm, set()))},
            {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "duration_ms": 1,
             "total_cost_usd": 0.0, "result": "dry run\n```json\n" + task["answer_example"] + "\n```"},
        ]
        return {"events": events, "stderr": "", "returncode": 0, "wall_ms": 1, "timed_out": False}
    return fake


class DryRunReader:
    def verify_baseline(self) -> dict[str, Any]:
        return {"ok": True, "differs": []}

    def wait_search_index(self, timeout: float, interval: float = 10.0,
                          absent_contacts: tuple[tuple[str, str], ...] = (), as_of: str | None = None) -> dict[str, Any]:
        return {"ok": True, "waited_s": 0.0, "differs": []}

    def baseline(self) -> dict[str, Any]:
        return {"deals": {}, "contacts": {}, "companies": {}}

    def end_state(self, since: str, targets: list[str]) -> dict[str, Any]:
        kinds = ("tasks", "notes", "meetings", "calls", "emails", "contacts")
        return {"read_at": since, "tz": "Europe/Berlin", "rounds": 2, "stable": True, "target_names": list(targets),
                "deals_target": {}, "deals_changed": {}, "deals_missing": [], "deals_created": [], "contacts_changed": {},
                "companies_changed": {}, "created": {k: [] for k in kinds},
                "counts": {**{k: 0 for k in kinds}, "deals_created": 0}}

    def read_check(self, since: str) -> dict[str, Any]:
        return {"ok": True, "deals_changed": 0, "contacts_changed": 0, "companies_changed": 0,
                "created": {k: 0 for k in ("tasks", "notes", "meetings", "calls", "emails", "contacts")}, "changed_names": []}


def dry_run_restore() -> dict[str, Any]:
    return {"exit_code": 0, "output_tail": "dry run: restore not executed"}


# --- pre-flight --------------------------------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tool_version(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout or proc.stderr or "").strip()


def check_official_server(name: str = "hubspot-bench") -> str:
    """``claude mcp get`` costs no model tokens. Returns its Status line; raises when the server is not connected."""
    claude = shutil.which("claude") or "claude"
    try:
        proc = subprocess.run([claude, "mcp", "get", name], capture_output=True, text=True, timeout=120,  # noqa: S603
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightError(f"cannot run `claude mcp get {name}`: {type(exc).__name__}: {str(exc)[:120]}") from exc
    text = (proc.stdout or "") + (proc.stderr or "")
    status = next((ln.strip() for ln in text.splitlines() if ln.strip().startswith("Status:")), "")
    if proc.returncode != 0 or "Connected" not in status:
        raise PreflightError(
            f"the official HubSpot MCP is not connected as {name!r} ({status or text.strip()[:120]!r}). Add it once with "
            f"`claude mcp add --scope user --transport http {name} {OFFICIAL_URL}`, then authenticate it in `/mcp` inside an interactive "
            "session (user scope, so the check passes from any directory)")
    return status


def render_rules(rules_dir: str, portal: str, out_path: Path, base_env: dict[str, str]) -> str:
    env = {**base_env, "HUBSPOT_MCP_RULES_DIR": rules_dir}
    proc = subprocess.run(  # noqa: S603 - fixed module; the portal id is digits-only (the rules loader rejects others)
        [sys.executable, "-m", "hubspot_mcp.intent.rules", "--portal", portal, "--out", str(out_path)],
        env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise PreflightError(f"rules render failed: {(proc.stderr or proc.stdout).strip()[:200]}")
    return out_path.read_text(encoding="utf-8")


def check_rules_text(text: str, truth_as_of: str) -> tuple[str, str]:
    """(as_of, fingerprint) from the rendered rules; the as-of date must equal the truth file's."""
    as_of = RULES_AS_OF_RE.search(text)
    fingerprint = FINGERPRINT_RE.search(text)
    if not as_of or not fingerprint:
        raise PreflightError("rendered sales rules carry no as-of date or fingerprint")
    if as_of.group(1) != truth_as_of:
        raise PreflightError(f"rules as-of {as_of.group(1)} differs from the truth file's {truth_as_of}")
    return as_of.group(1), fingerprint.group(1)


def check_server_rules(server: str, secret: str | None, fingerprint: str, *,
                       d_force_preview: bool | None = True) -> dict[str, Any]:
    """Both local surfaces must run the rules the arms were given; their tool counts go to tools_list.json.

    ``d_force_preview`` is the write tier arm D is meant to run at (True: preview-everything, False: AUTO); the
    grouped server reports the tier it runs at in get_sales_rules and a mismatch aborts. None skips the check."""
    listed: dict[str, Any] = {}
    for arm, path, tool in (("C", "/mcp/intent", "pronova_get_sales_rules"), ("D", "/mcp/grouped", "get_sales_rules")):
        surface = Surface(server.rstrip("/") + path, secret)
        surface.initialize()
        tools = surface.list_tools()
        listed[arm] = measure_tools_list(tools)
        if len(tools) != EXPECTED_TOOL_COUNTS[arm]:
            raise PreflightError(f"{path} lists {len(tools)} tools, expected {EXPECTED_TOOL_COUNTS[arm]}")
        data, is_error = surface.call_tool(tool)
        served = data.get("rules_hash") if isinstance(data, dict) else None
        if is_error or served != fingerprint:
            raise PreflightError(f"{path} serves rules {served!r}, the rendered rules are {fingerprint!r} "
                                 "(wrong rules dir, or a stale server process)")
        if arm == "D":
            observed = bool(data.get("force_preview")) if isinstance(data, dict) else False
            listed[arm] = {**listed[arm], "force_preview": observed}
            if d_force_preview is not None and observed != d_force_preview:
                raise PreflightError(
                    f"{path} runs with {D_FORCE_PREVIEW_ENV}={'1' if observed else 'unset'}, but this run needs arm D at the "
                    f"{'preview-everything' if d_force_preview else 'AUTO'} tier (restart the server"
                    f"{'' if d_force_preview else ' without that variable'}, or flip --arm-d-auto-tier)")
    return listed


IDENTITY_MCP = ("Call the HubSpot tool get_organization_details; it only reads. Change nothing in HubSpot. ")
IDENTITY_CLI = ("Then run exactly this one command with the Bash tool: hubspot whoami | python3 -c "
                "'import json,sys; print(json.load(sys.stdin)[\"portalId\"])' and do not print anything else from that command. ")
PORTAL_ID_RE = re.compile(r'"(mcp|cli)_portal_id"\s*:\s*"?(\d+)')


def check_portal_identity(portal: str, cells: list[tuple[str, str]], *, claude_fn, base_env: dict[str, str],
                          secret_env: str, cli_dir: str, rules_path: Path, model_ids: dict[str, str],
                          deny: list[Path] | None = None, timeout: int = 300) -> dict[str, str | None]:
    """Arms A and B hold write access to whatever portal the official MCP grant and the HubSpot CLI point at, and
    nothing else reads that portal. One cheap read-only session per run asks the account itself: the official MCP's
    ``get_organization_details`` and, when arm B runs, the CLI's ``hubspot whoami`` (only its portalId is printed,
    never the user). Aborts unless every id seen equals ``portal``. Returns the ids seen."""
    uses_b = any(a == "B" for a, _ in cells)
    arm = "B" if uses_b else "A"
    prompt = (IDENTITY_MCP + (IDENTITY_CLI if uses_b else "") + "Reply with one fenced ```json block and nothing else: "
              + ('{"mcp_portal_id": <portal id as a number>, "cli_portal_id": <the number the command printed>}' if uses_b
                 else '{"mcp_portal_id": <portal id as a number>}'))
    cwd = Path(tempfile.mkdtemp(prefix="hs-identity-"))
    try:
        cfg_path = prepare_cwd(cwd, arm, mcp_config(arm, "", None), None, deny)
        env, _ = session_env(arm, base_env, secret_env, cli_dir, cwd)
        try:
            raw = claude_fn(prompt, str(cfg_path), arm_argv_extra(arm, model_ids["sonnet"], rules_path), cwd, env, timeout)
        except (RuntimeError, OSError) as exc:
            raise PreflightError(f"portal identity check could not start: {str(exc)[:200]}") from exc
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
    if raw.get("timed_out"):
        raise PreflightError("portal identity check timed out")
    seen: dict[str, str | None] = {"mcp": None, "cli": None}
    for kind, value in PORTAL_ID_RE.findall(final_result_text(raw["events"])):
        seen[kind] = value
    wanted = ["mcp", "cli"] if uses_b else ["mcp"]
    for kind in wanted:
        if seen[kind] is None:
            raise PreflightError(f"portal identity check: no {kind} portal id came back (is the official MCP authorised"
                                 f"{', and is the hubspot CLI logged in' if kind == 'cli' else ''}?)")
        if seen[kind] != portal:
            raise PreflightError(f"the {'official MCP grant' if kind == 'mcp' else 'hubspot CLI'} points at portal "
                                 f"{seen[kind]}, not {portal}; sessions with write access would change the wrong portal")
    return seen


def build_expected(args: argparse.Namespace, cells: list[tuple[str, str]]) -> tuple[dict[str, set[str]], Path, Path]:
    sources = {"b": Path(args.skills_b), "cd": Path(args.skills_cd)}
    expected: dict[str, set[str]] = {}
    for arm in sorted({a for a, _ in cells}):
        key = ARMS[arm]["skills"]
        if not key:
            expected[arm] = set()
            continue
        src = sources[key]
        names = skill_names(src) if src.is_dir() else []
        if not names:
            raise PreflightError(f"no */SKILL.md under {src} (arm {arm})")
        expected[arm] = set(names)
    return expected, sources["b"], sources["cd"]


# --- main ----------------------------------------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--truth", required=True)
    ap.add_argument("--portal", default=DEFAULT_PORTAL)
    ap.add_argument("--rules-dir", default=os.environ.get("HUBSPOT_MCP_RULES_DIR"))
    ap.add_argument("--jobs", default=str(HERE / "jobs.jsonl"))
    ap.add_argument("--skills-b", default="/Users/izzy/Documents/hubspot-plugin/.claude/skills")
    ap.add_argument("--skills-cd", default="/Users/izzy/Documents/hubspot-plugin/skills")
    ap.add_argument("--arms", default="A,B,C,D")
    ap.add_argument("--models", default="sonnet,opus")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", default=None, help="comma-separated task ids")
    ap.add_argument("--smoke", action="store_true", help="1 rep of every selected task and cell (all: 7 tasks, 7 cells, 49 sessions)")
    ap.add_argument("--dry-run", action="store_true", help="fakes only: no claude, HubSpot, restore or server")
    ap.add_argument("--no-restore", action="store_true", help="skip restore.py (the end-state read still runs)")
    ap.add_argument("--arm-d-auto-tier", action="store_true",
                    help=f"arm D keeps /mcp's AUTO tier; the default is preview-everything, which needs the server started with {D_FORCE_PREVIEW_ENV}=1")
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--settle-seconds", type=float, default=8.0)
    ap.add_argument("--rerun-wait", type=float, default=30.0)
    ap.add_argument("--index-wait", type=float, default=300.0,
                    help="seconds to wait, before each session, for the Search API to show the restored records")
    ap.add_argument("--out", default=None)
    ap.add_argument("--secret-env", default="HUBSPOT_MCP_SERVER_SECRET")
    ap.add_argument("--token-env", default="HUBSPOT_TOKEN_149614891")
    ap.add_argument("--restore-cmd", default=None)
    ap.add_argument("--restore-cwd", default="/Users/izzy/Documents/hubspot-forecast")
    ap.add_argument("--hubspot-cli", default=str(Path.home() / ".hubspot" / "bin" / "hubspot"))
    return ap.parse_args(argv)


def select_cells(arms: str, models: str) -> list[tuple[str, str]]:
    wanted_arms, wanted_models = set(arms.split(",")), set(models.split(","))
    return [c for c in CELLS if c[0] in wanted_arms and c[1] in wanted_models]


def default_restore_cmd(portal: str) -> str:
    return f"python3 demo/seed/restore.py --expect-portal {portal} --apply --yes"


def print_plan(args: argparse.Namespace, tasks: list[dict[str, Any]], cells: list[tuple[str, str]], reps: int,
               expected: dict[str, set[str]]) -> None:
    print(f"plan: {len(tasks)} tasks x {len(cells)} cells x {reps} rep(s) = {len(tasks) * len(cells) * reps} sessions")
    print("cells:", ", ".join(f"{a}/{m}" for a, m in cells))
    shown = set()
    for arm, model in cells:
        if arm in shown:
            continue
        shown.add(arm)
        spec = ARMS[arm]
        argv = [*base_argv("claude", "<prompt>", "<cwd>/mcp-config.json"), *arm_argv_extra(arm, MODEL_IDS[model], Path("<run dir>/sales-rules.md"))]
        print(f"\narm {arm}: server {spec['server']}, --tools {spec['tools']!r}, --setting-sources {spec['sources']!r}, "
              f"skills copied: {sorted(expected.get(arm, set())) or 'none'}")
        print("  argv:", " ".join(shlex.quote(a) for a in argv))
        print("  mcp config:", json.dumps(masked(mcp_config(arm, args.server, "<secret>"))))
        if arm == "B":
            print(f"  env: allowlist only ({len(B_ENV_ALLOW)} names + {', '.join(p + '*' for p in B_ENV_ALLOW_PREFIXES)} minus credential-like names), "
                  "empty ZDOTDIR; added HUBSPOT_NO_AUTO_UPGRADE=1, PATH + ~/.hubspot/bin")
            print(f"  filesystem: Read and sandbox denyRead on {[str(p) for p in default_deny_paths()]}")
        else:
            print(f"  env: stripped {sorted(STRIP_ENV)} + {STRIP_PREFIX}* + --secret-env; added none")
        if arm == "D":
            tier = "AUTO (--arm-d-auto-tier)" if args.arm_d_auto_tier else f"preview-everything (server must run with {D_FORCE_PREVIEW_ENV}=1)"
            print(f"  write tier: {tier}")
    for task in tasks:
        print(f"\n--- {task['id']} ({task['kind']}) composed prompt ---\n{compose_prompt(task)}")


def _dry_run_setup(truth: dict[str, Any], cells: list[tuple[str, str]], out: Path):
    expected = {arm: ({f"dry-{ARMS[arm]['skills']}-skill"} if ARMS[arm]["skills"] else set()) for arm in {a for a, _ in cells}}
    as_of = truth["meta"]["as_of"]
    (out / "sales-rules.md").write_text(f"# Sales rules (dry run placeholder)\nAs-of date: {as_of}. Rules fingerprint: 000000000000.\n", encoding="utf-8")
    return expected, None, None, "dry-run", "dry-run", "", as_of, "000000000000", {}, None


def _live_setup(args: argparse.Namespace, truth: dict[str, Any], cells: list[tuple[str, str]], out: Path,
                secret: str | None, base_env: dict[str, str]):
    claude_version = tool_version([shutil.which("claude") or "claude", "--version"])
    if EXPECTED_CLAUDE not in claude_version:
        print(f"warning: claude is {claude_version!r}; BUILTIN_SKILLS is pinned to {EXPECTED_CLAUDE}")
    expected, skills_b, skills_cd = build_expected(args, cells)
    uses_b = any(a == "B" for a, _ in cells)
    hubspot_version = tool_version([args.hubspot_cli, "--version"]) if uses_b else ""
    if uses_b and EXPECTED_CLI not in hubspot_version:
        print(f"warning: hubspot CLI is {hubspot_version!r}, expected {EXPECTED_CLI}; pin it (bench/README.md)")
    status_line = check_official_server() if any(a in ("A", "B") for a, _ in cells) else ""
    if not args.rules_dir:
        raise PreflightError("--rules-dir (or HUBSPOT_MCP_RULES_DIR) is required")
    if not (Path(args.rules_dir) / f"{args.portal}.yaml").is_file():
        raise PreflightError(f"{args.rules_dir}/{args.portal}.yaml not found (copy the seed's rules YAML there)")
    rules_text = render_rules(args.rules_dir, args.portal, out / "sales-rules.md", base_env)
    as_of, fingerprint = check_rules_text(rules_text, truth["meta"]["as_of"])
    tools_list: dict[str, Any] = {}
    if any(a in ("C", "D") for a, _ in cells):
        if not secret:
            raise PreflightError(f"set {args.secret_env} (the bearer secret the local server was started with)")
        try:
            tools_list = check_server_rules(args.server, secret, fingerprint,
                                            d_force_preview=(not args.arm_d_auto_tier) if any(a == "D" for a, _ in cells) else None)
        except (RuntimeError, httpx.HTTPError) as exc:
            raise PreflightError(f"local server unreachable or refused: {exc}") from exc
    (out / "tools_list.json").write_text(json.dumps(tools_list, indent=2))
    identity: dict[str, str | None] = {}
    if any(a in ("A", "B") for a, _ in cells):
        identity = check_portal_identity(args.portal, cells, claude_fn=run_claude, base_env=base_env, secret_env=args.secret_env,
                                         cli_dir=str(Path(args.hubspot_cli).expanduser().parent), rules_path=out / "sales-rules.md",
                                         model_ids=MODEL_IDS)
    return (expected, skills_b, skills_cd, claude_version, hubspot_version, status_line, as_of, fingerprint,
            identity, tools_list.get("D", {}).get("force_preview"))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cells = select_cells(args.arms, args.models)
    if not cells:
        print("abort: no cells selected by --arms/--models")
        return 2
    reps = 1 if args.smoke or args.dry_run else args.reps
    truth_path, jobs_path = Path(args.truth), Path(args.jobs)
    try:
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        tasks = load_jobs(jobs_path)
    except (OSError, ValueError) as exc:
        print(f"abort: cannot read truth or jobs: {exc}")
        return 2
    if args.only:
        wanted = set(args.only.split(","))
        tasks = [t for t in tasks if t["id"] in wanted]
    if not tasks:
        print("abort: no tasks selected")
        return 2
    for task in tasks:
        if truth_at(truth, task["truth_key"]).get("prompt") != task["prompt"]:
            print(f"abort: {task['id']} prompt differs from the truth file's")
            return 2

    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    out = Path(args.out) if args.out else (Path(tempfile.mkdtemp(prefix="hs-intent-dry-")) if args.dry_run
                                           else HERE / "runs" / f"{stamp}-intent")
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(jobs_path, out / "jobs.jsonl")
    secret = os.environ.get(args.secret_env) or None
    base_env = dict(os.environ)

    try:
        setup = _dry_run_setup(truth, cells, out) if args.dry_run else _live_setup(args, truth, cells, out, secret, base_env)
    except PreflightError as exc:
        print(f"abort: {exc}")
        return 2
    (expected, skills_b, skills_cd, claude_version, hubspot_version, status_line, as_of, fingerprint, identity,
     d_force_observed) = setup
    restore_cmd = args.restore_cmd or default_restore_cmd(args.portal)
    meta = {
        "started": stamp, "argv": list(argv if argv is not None else sys.argv[1:]), "claude_version": claude_version,
        "hubspot_cli_version": hubspot_version, "expected_cli": EXPECTED_CLI, "official_server_status": status_line,
        "models": {m: MODEL_IDS[m] for _, m in cells}, "cells": [f"{a}/{m}" for a, m in cells],
        "tasks": [t["id"] for t in tasks], "reps": reps, "portal": args.portal, "truth_path": str(truth_path),
        "truth_sha256": _sha256(truth_path), "jobs_sha256": _sha256(jobs_path), "rules_hash": fingerprint,
        "sales_rules_sha256": _sha256(out / "sales-rules.md"), "as_of": as_of, "rules_dir": args.rules_dir,
        "suffix_read": SUFFIX_READ, "suffix_write": SUFFIX_WRITE, "answer_prefix": ANSWER_PREFIX,
        "answers": {t["id"]: answer_instruction(t) for t in tasks}, "builtin_skills": sorted(BUILTIN_SKILLS),
        "arm_expected_skills": {a: sorted(s) for a, s in expected.items()},
        "arm_d_tier": "auto" if args.arm_d_auto_tier else "preview",
        "arm_d_force_preview_observed": d_force_observed, "portal_identity": identity,
        "env_stripped": [*sorted(STRIP_ENV | {args.secret_env}), STRIP_PREFIX + "*"],
        "env_arm_b": "allowlist: " + ", ".join(sorted(B_ENV_ALLOW)) + ", " + ", ".join(p + "*" for p in B_ENV_ALLOW_PREFIXES),
        "arm_b_fs_deny": [str(p) for p in default_deny_paths()],
        "env_stripped_present": sorted(n for n in base_env if n in STRIP_ENV or n == args.secret_env or n.startswith(STRIP_PREFIX)),
        "tool_search_env": base_env.get("ENABLE_TOOL_SEARCH"), "restore_cmd": None if args.no_restore else restore_cmd,
        "timeout": args.timeout, "dry_run": args.dry_run, "no_restore": args.no_restore,
    }
    print_plan(args, tasks, cells, reps, expected)
    if any(a == "D" for a, _ in cells):
        checked = ("verified against the running server" if d_force_observed is not None
                   else "not checked in a dry run; a live run reads it from the server and aborts on a mismatch")
        print(f"\nArm D tier: {meta['arm_d_tier']} ({checked}). The runner cannot change a running server; for 'preview' the "
              f"server process must be started with {D_FORCE_PREVIEW_ENV}=1.")

    ctx = Ctx(out=out, truth=truth, server=args.server, secret=secret, secret_env=args.secret_env, rules_path=out / "sales-rules.md",
              skills_b=skills_b, skills_cd=skills_cd, expected_skills=expected, base_env=base_env,
              cli_dir=str(Path(args.hubspot_cli).expanduser().parent), timeout=args.timeout,
              no_restore=args.no_restore or args.dry_run, rerun_wait=0.0 if args.dry_run else args.rerun_wait,
              index_wait=args.index_wait)
    if args.dry_run:
        reader: Any = DryRunReader()
        restore_fn: Callable[[], dict[str, Any]] = dry_run_restore
        fakes = {t["id"]: dry_run_claude(t, expected) for t in tasks}
        claude_for = lambda task: fakes[task["id"]]  # noqa: E731
    else:
        from portal_read import PortalReader, ReadError
        token = os.environ.get(args.token_env)
        if not token:
            print(f"abort: set {args.token_env} (the portal token, used for read-only end-state checks)")
            return 2
        reader = PortalReader(token, settle_seconds=args.settle_seconds)
        restore_fn = make_restore_fn(restore_cmd, args.restore_cwd, args.token_env, base_env)
        try:
            if not args.no_restore:
                first = restore_with_retry(restore_fn)
                if not first["ok"]:
                    print(f"abort: the pre-flight restore failed twice (exit {first['exit_code']})")
                    return 2
            (out / "baseline.json").write_text(json.dumps(reader.baseline(), indent=1), encoding="utf-8")
        except ReadError as exc:
            print(f"abort: baseline read failed: {exc}")
            return 2
        claude_for = lambda task: run_claude  # noqa: E731

    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    sessions_path = out / "sessions.jsonl"

    def sink(record: dict[str, Any]) -> None:
        with sessions_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        summary = record["summary"] or {}
        detail = f" {record['failure_detail']}" if record["failure_detail"] else ""
        print(f"[{record['n']}] rep {record['rep']} {record['task_id']} {record['arm']}/{record['model']} a{record['attempt']} "
              f"{record['failure_class'] or 'ok'}{detail} turns={summary.get('num_turns')} "
              f"cost={summary.get('total_cost_usd')} ms={record['wall_ms']}", flush=True)

    counter = [0]
    for rep, task, cell in session_plan(tasks, cells, reps):
        try:
            if not run_with_rerun(task, cell, rep, ctx, counter, claude_fn=claude_for(task), reader=reader,
                                  restore_fn=restore_fn, sink=sink):
                print("stop: restore failed twice; the portal is not at seed state. Fix it, then re-run (exit 3).")
                return 3
        except HarnessLeak as exc:
            print(f"stop: arm configuration leak, the same leak would repeat in every later session of that arm ({exc}). Fix it, then re-run (exit 4).")
            return 4
    print("done ->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
