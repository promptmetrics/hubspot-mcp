#!/usr/bin/env python3
"""Two-portal demo kit: check, prepare, restore and compare. See demo/README.md.

    uv run python demo/kit.py setup --left-portal <id> --right-portal <id>
    uv run python demo/kit.py check --day today
    uv run python demo/kit.py prepare --day 2026-11-10 --yes
    uv run python demo/kit.py restore --yes
    uv run python demo/kit.py compare

State lives in ~/hubspot-demo (override with --home). Tokens are read from the environment variables named in
portals.json (HUBSPOT_TOKEN_<portal id>) and never written to a file. The seed scripts in hubspot-forecast/demo/seed
do every HubSpot write; this module only calls them, and reads the portals through bench/portal_read.PortalReader,
which cannot write. Nothing here prints a token or an email address: console output and the report mask both.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bench"))
import grade  # noqa: E402
import intent_run  # noqa: E402
from portal_read import TABLE_PROPS, PortalReader  # noqa: E402
from report import PRICES  # noqa: E402

from hubspot_mcp.intent.rules import SalesRules, render_sales_rules_md  # noqa: E402

HOME_NAME = "hubspot-demo"
DEFAULT_SEED_DIR = Path("/Users/izzy/Documents/hubspot-forecast/demo/seed")
DEFAULT_SKILLS_DIR = Path("/Users/izzy/Documents/hubspot-plugin/skills")
SEED_DIR_ENV = "HUBSPOT_DEMO_SEED_DIR"
SKILLS_DIR_ENV = "HUBSPOT_DEMO_SKILLS_DIR"
SIDES = ("left", "right")
DEFAULT_LEFT_SERVER = "hubspot-bench"
RIGHT_SERVER = "pronova"
MODEL_ID = intent_run.MODEL_IDS["sonnet"]
PRICE_KEY = "sonnet-5.5"
FORM_TOOLS = frozenset({"pronova_apply_changes", "pronova_undo_changes"})
TOKEN_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
SERVER_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
PORTAL_RE = re.compile(r"^\d{1,20}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# act number, title, answer-key job, kind
# The read acts that carry an answer key (1 triage, 2 forecast) run before the write acts: the keys describe the seeded
# state, and Acts 3 and 4 move D01's amount and close date and 31 other close dates.
ACTS: list[tuple[int, str, str, str]] = [
    (1, "Triage", "job1", "read"),
    (2, "Forecast", "job4", "read"),
    (3, "Log the meeting", "job3", "write"),
    (4, "Bulk fix of past-due close dates", "job5", "write"),
    (5, "Rule edit, then triage again", "job1", "read"),
]
WRITE_ACTS = {3: "job3", 4: "job5"}
SEED_EMAIL_ENVS = ("SEED_DEMO_USER_EMAIL", "SEED_OWNER_EMAIL_BASE")
CODE_WORDS = {
    "OVERDUE": ("overdue", "close date passed", "close dates passed", "past due", "past-due", "date passed"),
    "NO_NEXT": ("no_next", "no next", "next step missing", "no scheduled"),
    "SILENT": ("silent", "no contact", "not contacted", "no recent contact", "gone quiet"),
    "STALLED": ("stalled", "stuck in"),
}


class KitError(RuntimeError):
    """A condition the operator must fix; main prints it and exits non-zero."""


class TranscriptError(KitError):
    """A transcript record lacks a field the report needs."""


# --- small helpers -----------------------------------------------------------------------------------------------


def mask_emails(text: str) -> str:
    return EMAIL_RE.sub("<email>", text or "")


def mask_tree(obj: Any, _keys: dict[str, str] | None = None) -> Any:
    """Copy of ``obj`` with every email in a string replaced by <email>; an email used as a dict key becomes
    <email-N> (numbered in order of first sight) so two contacts never collapse into one entry."""
    keys = _keys if _keys is not None else {}
    if isinstance(obj, str):
        return mask_emails(obj)
    if isinstance(obj, list):
        return [mask_tree(v, keys) for v in obj]
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and EMAIL_RE.search(k):
                key = keys.setdefault(k, f"<email-{len(keys) + 1}>")
            else:
                key = k
            out[key] = mask_tree(v, keys)
        return out
    return obj


def redact(text: str, env: Mapping[str, str], names: list[str]) -> str:
    for name in names:
        value = (env.get(name) or "").strip()
        if len(value) >= 6:
            text = text.replace(value, "<redacted>")
    return mask_emails(text)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise KitError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise KitError(f"{path} is not valid JSON: {exc}") from exc


def parse_day(raw: str, today: Callable[[], date] = date.today) -> str:
    if raw == "today":
        return today().isoformat()
    if not DAY_RE.fullmatch(raw):
        raise KitError(f"--day must be YYYY-MM-DD or 'today', got {raw!r}")
    try:
        date.fromisoformat(raw)
    except ValueError as exc:
        raise KitError(f"--day {raw!r} is not a calendar date") from exc
    return raw


def home_dir(args_home: str | None) -> Path:
    return Path(args_home).expanduser() if args_home else Path.home() / HOME_NAME


def seed_dir_from(raw: str | None, env: Mapping[str, str]) -> Path:
    return Path(raw or env.get(SEED_DIR_ENV) or DEFAULT_SEED_DIR).expanduser()


def skills_dir_from(raw: str | None, env: Mapping[str, str]) -> Path:
    return Path(raw or env.get(SKILLS_DIR_ENV) or DEFAULT_SKILLS_DIR).expanduser()


# --- configuration -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Side:
    name: str
    portal_id: str
    token_env: str

    @property
    def label(self) -> str:
        return self.name


@dataclass(frozen=True)
class Config:
    left: Side
    right: Side
    left_server: str = DEFAULT_LEFT_SERVER
    right_server: str = RIGHT_SERVER

    def side(self, name: str) -> Side:
        return self.left if name == "left" else self.right

    def sides(self) -> tuple[Side, Side]:
        return self.left, self.right

    def server(self, name: str) -> str:
        return self.left_server if name == "left" else self.right_server

    def to_json(self) -> dict[str, Any]:
        def side(s: Side) -> dict[str, str]:
            return {"label": s.label, "portal_id": s.portal_id, "token_env": s.token_env}
        return {"version": 1, "left": side(self.left), "right": side(self.right),
                "left_server": self.left_server, "right_server": self.right_server}


def build_config(left_portal: str, right_portal: str, left_server: str = DEFAULT_LEFT_SERVER) -> Config:
    for flag, value in (("--left-portal", left_portal), ("--right-portal", right_portal)):
        if not PORTAL_RE.fullmatch(value):
            raise KitError(f"{flag} must be the numeric portal id, got {value!r}")
    if left_portal == right_portal:
        raise KitError("the left and right portal must differ: two sides on one portal break the comparison")
    if not SERVER_RE.fullmatch(left_server):
        raise KitError(f"--left-server must match [A-Za-z0-9_-]+, got {left_server!r}")
    return Config(left=Side("left", left_portal, f"HUBSPOT_TOKEN_{left_portal}"),
                  right=Side("right", right_portal, f"HUBSPOT_TOKEN_{right_portal}"), left_server=left_server)


def load_config(home: Path) -> Config:
    path = home / "portals.json"
    if not path.is_file():
        raise KitError(f"{path} not found; run `kit.py setup --left-portal <id> --right-portal <id>` first")
    raw = read_json(path)
    try:
        sides = {}
        for name in SIDES:
            entry = raw[name]
            side = Side(name, str(entry["portal_id"]), str(entry["token_env"]))
            if not PORTAL_RE.fullmatch(side.portal_id) or not TOKEN_ENV_RE.fullmatch(side.token_env):
                raise KitError(f"{path}: {name} needs a numeric portal_id and an environment variable NAME as token_env")
            sides[name] = side
        left_server = str(raw.get("left_server") or DEFAULT_LEFT_SERVER)
        right_server = str(raw.get("right_server") or RIGHT_SERVER)
    except (KeyError, TypeError) as exc:
        raise KitError(f"{path} lacks {exc}") from exc
    if not SERVER_RE.fullmatch(left_server) or not SERVER_RE.fullmatch(right_server):
        raise KitError(f"{path}: server names must match [A-Za-z0-9_-]+")
    if sides["left"].portal_id == sides["right"].portal_id:
        raise KitError(f"{path}: both sides name the same portal")
    return Config(sides["left"], sides["right"], left_server, right_server)


def load_state(home: Path) -> dict[str, Any]:
    path = home / "state.json"
    if not path.is_file():
        raise KitError(f"{path} not found; run `kit.py prepare --day <day> --yes` first")
    state = read_json(path)
    for key in ("day", "prepared_at", "seed_dir", "truth"):
        if key not in state:
            raise KitError(f"{path} lacks {key!r}; run `kit.py prepare` again")
    return state


def missing_tokens(cfg: Config, env: Mapping[str, str]) -> list[str]:
    return [s.token_env for s in cfg.sides() if not (env.get(s.token_env) or "").strip()]


# --- running the seed scripts ------------------------------------------------------------------------------------


@dataclass
class RunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def output(self) -> str:
        return (self.stdout or "") + (self.stderr or "")


Runner = Callable[[list[str], Mapping[str, str]], RunResult]


def subprocess_runner(argv: list[str], env: Mapping[str, str]) -> RunResult:
    proc = subprocess.run(  # noqa: S603 - argv is built here from validated ids and fixed script names
        argv, env=dict(env), capture_output=True, text=True, timeout=1800, stdin=subprocess.DEVNULL)
    return RunResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def seed_script(seed_dir: Path, name: str) -> str:
    path = seed_dir / name
    if not path.is_file():
        raise KitError(f"{path} not found; set --seed-dir or {SEED_DIR_ENV}")
    return str(path)


def side_args(side: Side) -> list[str]:
    return ["--portal", side.label, "--expect-portal", side.portal_id, "--token-env", side.token_env]


def preflight_argv(seed_dir: Path, side: Side, day: str) -> list[str]:
    return [sys.executable, seed_script(seed_dir, "preflight.py"), *side_args(side), "--demo-day", day, "--json"]


def reset_argv(seed_dir: Path, side: Side) -> list[str]:
    return [sys.executable, seed_script(seed_dir, "reset.py"), *side_args(side), "--apply", "--yes"]


def seed_argv(seed_dir: Path, side: Side, day: str, *, apply: bool) -> list[str]:
    tail = ["--apply", "--yes"] if apply else ["--plan"]
    return [sys.executable, seed_script(seed_dir, "seed.py"), *side_args(side), "--demo-day", day, *tail]


def restore_argv(seed_dir: Path, side: Side, day: str) -> list[str]:
    return [sys.executable, seed_script(seed_dir, "restore.py"), *side_args(side), "--demo-day", day, "--apply", "--yes"]


def tail(text: str, lines: int = 12) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


# --- check -------------------------------------------------------------------------------------------------------

PREFLIGHT_EXIT = {0: "pass", 1: "a check failed", 2: "bad flags or no token", 4: "demo day or plan refused",
                  5: "token belongs to another portal"}


def missing_seed_emails(env: Mapping[str, str]) -> list[str]:
    """Names of the owner e-mail variables seed.py needs. Without them it plans with placeholders and only fails, after the
    portal is reset, when it creates the owners."""
    missing = []
    if not (env.get(SEED_EMAIL_ENVS[0]) or "").strip():
        missing.append(SEED_EMAIL_ENVS[0])
    if "@" not in (env.get(SEED_EMAIL_ENVS[1]) or ""):
        missing.append(SEED_EMAIL_ENVS[1])
    return missing


def preflight_checks(result: RunResult) -> list[dict[str, Any]] | None:
    """The checks of one preflight run, or None when its output is not the JSON report."""
    try:
        checks = json.loads(result.stdout).get("checks", [])
    except (ValueError, TypeError, AttributeError):
        return None
    return [c for c in checks if isinstance(c, dict)] if isinstance(checks, list) else None


def checks_with(checks: list[dict[str, Any]] | None, status: str) -> list[str]:
    """'name: detail' of each check with this status; preflight.py writes it in lower case."""
    return [f"{c.get('name')}: {c.get('detail')}" for c in checks or [] if str(c.get("status")).lower() == status]


def check(home: Path, day: str, *, seed_dir: Path, env: Mapping[str, str], runner: Runner) -> int:
    cfg = load_config(home)
    names = [s.token_env for s in cfg.sides()]
    rows: list[tuple[str, str, int, str, str]] = []
    warnings: list[str] = []
    worst = 0
    for side in cfg.sides():
        result = runner(preflight_argv(seed_dir, side, day), env)
        checks = preflight_checks(result)
        if checks is None:
            failed = tail(result.stderr or result.stdout, 2).replace("\n", " ")
        else:
            failed = "; ".join(checks_with(checks, "fail"))
            warnings += [f"{side.name} {w}" for w in checks_with(checks, "warn")]
        meaning = PREFLIGHT_EXIT.get(result.returncode, "unexpected exit code")
        rows.append((side.name, side.portal_id, result.returncode, meaning, redact(failed, env, names)))
        worst = max(worst, result.returncode)
    print(f"Preflight for demo day {day} (read-only)")
    print(f"{'side':<6} {'portal':<12} {'exit':<5} {'result':<34} failed checks")
    for name, portal, code, meaning, failed in rows:
        print(f"{name:<6} {portal:<12} {code:<5} {meaning:<34} {failed or '-'}")
    for warning in warnings:
        print(f"warning: {redact(warning, env, names)}")
    missing = missing_seed_emails(env)
    if missing:
        print(f"seed owners: set {', '.join(missing)} in the environment (SEED_OWNER_EMAIL_BASE needs an @); "
              "without them the seed stops after it has reset a portal.")
    ok = all(code == 0 for _, _, code, _, _ in rows) and not missing
    if ok:
        print(f"READY ({len(warnings)} warning(s)): both portals pass." if warnings else "READY: both portals pass.")
    else:
        print("NOT READY: fix the failures above before `prepare`.")
    return 0 if ok else (worst or 2)


# --- prepare -----------------------------------------------------------------------------------------------------


def truth_path(seed_dir: Path, side: Side, day: str) -> Path:
    return seed_dir / "out" / f"truth-{side.label}-{day}.json"


def snapshot_path(seed_dir: Path, side: Side, day: str) -> Path:
    return seed_dir / "out" / f"snapshot-{side.label}-{day}.json"


def rules_source(seed_dir: Path, side: Side, day: str) -> Path:
    """seed.py names the rules file by label and day; the labels the benchmark docs name keep an older, day-less name."""
    for name in (f"rules-{side.label}-{day}.yaml", f"rules-{side.label}.yaml"):
        if (seed_dir / "out" / name).is_file():
            return seed_dir / "out" / name
    raise KitError(f"no rules file for {side.label} {day} in {seed_dir / 'out'}")


def render_rules_md(rules_yaml: Path) -> str:
    """The benchmark's sales-rules.md, from the rules YAML, by the server's own renderer."""
    try:
        raw = yaml.safe_load(rules_yaml.read_text(encoding="utf-8")) or {}
        return render_sales_rules_md(SalesRules.model_validate(raw))
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise KitError(f"cannot render {rules_yaml}: {mask_emails(str(exc))[:200]}") from exc


def prepare_plan_lines(cfg: Config, day: str, seed_dir: Path, home: Path) -> list[str]:
    lines = [f"prepare for demo day {day} would:"]
    for side in cfg.sides():
        lines.append(f"  1. seed.py --plan, then preflight.py on {side.name} (portal {side.portal_id}, token variable "
                     f"{side.token_env}): validates the day and the portal before anything is touched; both portals "
                     "must pass")
    for side in cfg.sides():
        lines.append(f"  2. {side.name}: reset.py --apply --yes, then seed.py --apply --yes --demo-day {day} "
                     f"(archives every demo record on portal {side.portal_id}, then seeds it again)")
    lines.append("  3. require both seeds to have written the same number of deals, companies, contacts and goals")
    lines.append(f"  4. write {home}/left and {home}/right (mcp.json, launch.sh; right also .claude/skills), "
                 f"{home}/sales-rules.md, {home}/rules/{cfg.right.portal_id}.yaml, {home}/script.md, {home}/state.json")
    lines.append(f"seed scripts: {seed_dir}")
    return lines


def prepare(home: Path, day: str, *, yes: bool, seed_dir: Path, skills_dir: Path, env: Mapping[str, str],
            runner: Runner, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
    cfg = load_config(home)
    names = [s.token_env for s in cfg.sides()]
    if not yes:
        print("\n".join(prepare_plan_lines(cfg, day, seed_dir, home)))
        print("REFUSING: this resets and re-seeds both portals. Re-run with --yes to do it.")
        return 2
    if missing := missing_tokens(cfg, env):
        print(f"REFUSING: set {', '.join(missing)} in the environment first (names only; values are never read from disk).")
        return 2
    if missing := missing_seed_emails(env):
        print(f"REFUSING: set {', '.join(missing)} in the environment first (SEED_OWNER_EMAIL_BASE needs an @). Without "
              "them the seed stops after it has reset a portal.")
        return 2
    if not skills_dir.is_dir():
        print(f"REFUSING: skills folder {skills_dir} not found")
        return 2

    def run(label: str, argv: list[str]) -> bool:
        result = runner(argv, env)
        if result.returncode != 0:
            print(f"FAILED: {label} exited {result.returncode}\n{redact(tail(result.output), env, names)}")
            return False
        print(f"ok: {label}")
        return True

    for side in cfg.sides():
        if not run(f"seed.py --plan ({side.name})", seed_argv(seed_dir, side, day, apply=False)):
            print("Nothing was changed on either portal.")
            return 1
    blocked: list[str] = []
    for side in cfg.sides():
        result = runner(preflight_argv(seed_dir, side, day), env)
        checks = preflight_checks(result)
        failed = checks_with(checks, "fail")
        # reset.py clears leftovers, so a failed leftovers check alone does not stop prepare.
        if result.returncode == 0 or (result.returncode == 1 and failed and all(f.startswith("leftovers:") for f in failed)):
            print(f"ok: preflight.py ({side.name}, portal {side.portal_id})")
            continue
        reason = "; ".join(failed) or tail(result.output, 2).replace("\n", " ")
        blocked.append(f"{side.name} (portal {side.portal_id}): {PREFLIGHT_EXIT.get(result.returncode, 'unexpected exit')}"
                       f" - {redact(reason, env, names)}")
    if blocked:
        print("REFUSING: the preflight failed, so nothing was reset. Fix:\n  " + "\n  ".join(blocked))
        return 1
    # From the first reset on, an earlier state.json would describe a portal that no longer holds that day.
    (home / "state.json").unlink(missing_ok=True)
    started = now()
    for side in cfg.sides():
        if not run(f"reset.py ({side.name}, portal {side.portal_id})", reset_argv(seed_dir, side)):
            print(f"Stopped on {side.name}; state.json was removed. "
                  + ("The right portal was not touched." if side is cfg.left
                     else "The left portal is already reset and seeded for this day.") + " Re-run prepare after fixing the cause.")
            return 1
        if not run(f"seed.py ({side.name}, portal {side.portal_id})", seed_argv(seed_dir, side, day, apply=True)):
            print(f"Stopped on {side.name}; its portal may be half seeded and state.json was removed. "
                  "Re-run prepare after fixing the cause.")
            return 1
    try:
        check_identical_seeds(cfg, day, seed_dir)
        write_workspace(home, cfg, day, seed_dir=seed_dir, skills_dir=skills_dir, prepared_at=started)
    except KitError as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"Prepared {day}. Windows: {home}/left/launch.sh and {home}/right/launch.sh. Script: {home}/script.md")
    print(f"Before Act 1, confirm the left window's HubSpot login is portal {cfg.left.portal_id} (the script's first step): "
          "nothing else ties the OAuth login to the portal that was just seeded.")
    return 0


def seed_manifest(seed_dir: Path, side: Side, day: str) -> dict[str, Any]:
    """The newest manifest seed.py wrote for this label and day (named <label>-<UTC stamp>.json)."""
    name = re.compile(rf"{re.escape(side.label)}-\d{{4}}-\d{{2}}-\d{{2}}-\d{{6}}\.json")
    found = sorted(p for p in (seed_dir / "out").glob(f"{side.label}-*.json") if name.fullmatch(p.name))
    for path in reversed(found):
        manifest = read_json(path)
        if manifest.get("demo_day") == day and str(manifest.get("portal_id")) == side.portal_id:
            return manifest
    raise KitError(f"no seed manifest for {side.label} {day} (portal {side.portal_id}) in {seed_dir / 'out'}")


def manifest_shape(manifest: dict[str, Any]) -> dict[str, Any]:
    return {"deals": len(manifest.get("deals") or {}), "companies": len(manifest.get("companies") or {}),
            "contacts": len(manifest.get("contacts") or {}),
            "goals": sum(1 for v in (manifest.get("goals") or {}).values() if v),
            "team": bool(manifest.get("team")), "owners": sorted(manifest.get("owners") or {})}


def check_identical_seeds(cfg: Config, day: str, seed_dir: Path) -> None:
    """Both portals must hold the same records. The answer keys cannot show it: they come from the day alone."""
    shapes = {s.name: manifest_shape(seed_manifest(seed_dir, s, day)) for s in cfg.sides()}
    if shapes["left"] != shapes["right"]:
        diff = [f"{k}: left {shapes['left'][k]}, right {shapes['right'][k]}" for k in shapes["left"]
                if shapes["left"][k] != shapes["right"][k]]
        raise KitError("the portals were not seeded identically (" + "; ".join(diff) + "); fix the portal that differs, "
                       "then run prepare again")


def mcp_json(cfg: Config, side: str, home: Path) -> dict[str, Any]:
    if side == "left":
        entry = next(iter(intent_run.mcp_config("A", "", None)["mcpServers"].values()))
        return {"mcpServers": {cfg.left_server: entry}}
    return {"mcpServers": {cfg.right_server: {
        "command": "uv",
        "args": ["--directory", str(REPO), "run", "hubspot-mcp", "--portal", cfg.right.portal_id,
                 "--mode", "token", "--surface", "intent"],
        "env": {"HUBSPOT_MCP_RULES_DIR": str(home / "rules")},
    }}}


def launch_sh(cfg: Config, side: str, day: str) -> str:
    server = cfg.server(side)
    allowed = f'"mcp__{server}"' + (' "Skill"' if side == "right" else "")
    unset = [cfg.left.token_env, cfg.right.token_env] if side == "left" else [cfg.left.token_env]
    guard = (f': "${{{cfg.right.token_env}:?set {cfg.right.token_env} before launching the right side}}"\n'
             if side == "right" else "")
    env_prefix = "env " + " ".join(f"-u {name}" for name in unset) + " "
    return (
        "#!/bin/sh\n"
        f"# Generated by demo/kit.py prepare for demo day {day} ({side} side). Holds no token: the right side's server "
        "inherits\n# its token variable from this shell; the left side signs in to HubSpot's MCP through OAuth.\n"
        "set -eu\n"
        'cd "$(dirname "$0")"\n'
        f"{guard}"
        f"exec {env_prefix}claude --strict-mcp-config --mcp-config mcp.json --setting-sources project "
        f"--model {MODEL_ID} --append-system-prompt-file ../sales-rules.md --allowedTools {allowed}\n"
    )


def act_prompts(truth: dict[str, Any], silent_days_now: int) -> dict[int, str]:
    job1 = truth["job1"]["prompt"]
    new_days = max(1, silent_days_now // 2)
    return {
        1: job1,
        2: truth["job4"]["prompt"],
        3: truth["job3"]["prompt"],
        4: truth["job5"]["prompt"],
        5: f"The SILENT rule is now {new_days} days. {job1}",
    }


def _count(value: Any) -> Any:
    return len(value) if isinstance(value, list) else value


def expectations(truth: dict[str, Any]) -> dict[int, list[str]]:
    owner = truth["meta"].get("demo_owner", "demo")
    j1, j3, j4, j5 = truth["job1"], truth["job3"], truth["job4"], truth["job5"]
    o1 = j1["owners"][owner]
    codes = ", ".join(f"{k} {_count(v)}" for k, v in o1.get("by_code", {}).items())
    exp3 = j3["expected_end_state"]["deal"]
    fig = j4["owners"][owner]
    totals = ", ".join(f"{label} {fig[key]['total']:,.0f}" for label, key in
                       (("closed won", "closed_won"), ("commit", "hubspot_commit"), ("best case", "best_case"),
                        ("pipeline", "pipeline"), ("weighted", "hubspot_weighted"), ("supported commit", "supported_commit")))
    return {
        1: [f"Key: {o1['flagged_count']} deals flagged ({codes})."],
        2: [f"Key ({j4['period']['label']}, {j4['company_currency']}, seeded state): {totals}."],
        3: [f"Key: {j3['target_deal']['dealname']} closes {exp3['closedate']}, amount {exp3['amount']} {exp3['currency']}; "
            "1 meeting, 1 note, 1 task and 1 new contact."],
        4: [f"Key: {j5['count']} deals move to {j5['new_close_date']}, one task each."],
        5: ["No answer key: the rule changed. Compare the flagged list with Act 1."],
    }


def build_script(day: str, cfg: Config, truth: dict[str, Any], rules_file: Path, silent_days_now: int) -> str:
    prompts = act_prompts(truth, silent_days_now)
    exp = expectations(truth)
    new_days = max(1, silent_days_now // 2)
    lines = [
        f"# Demo script for {day}",
        "",
        f"Left: HubSpot's official MCP (`{cfg.left_server}`, portal {cfg.left.portal_id}), no skills. "
        f"Right: the Pronova intent server (portal {cfg.right.portal_id}) with the HubSpot-style skills. "
        f"Both portals hold identical data for {day}; both windows run {MODEL_ID} with the same `sales-rules.md`.",
        "",
        "Start the windows in two terminals: `left/launch.sh` and `right/launch.sh` "
        f"(the right one needs `{cfg.right.token_env}` exported first).",
        "",
        "## Before Act 1: confirm the left login",
        "",
        f"The left window reaches HubSpot through OAuth, and nothing else ties that login to portal {cfg.left.portal_id}. "
        "In the left window, before Act 1, send this and check the answer:",
        "",
        "```text",
        "Call get_organization_details and tell me the portal id.",
        "```",
        "",
        f"The answer must read {cfg.left.portal_id}. If it does not, sign out of the left server and sign in to the "
        "right account before you go on: Acts 3 and 4 write to whatever portal that login points at, and `restore` "
        "does not undo them there.",
        "",
        "Rules of the run:",
        "",
        "- Paste each prompt exactly as written.",
        "- Run the acts in order. Acts 1 and 2 are graded against the seeded state, so they come before the write acts.",
        "- **Run the write acts (3 and 4) one side at a time.** Each portal holds its own copy of the data, but a side "
        "that waits on a confirmation form while the other writes breaks the comparison. Finish the form and any undo "
        "on one side, then send the prompt on the other.",
        "- Read acts (1, 2, 5) can run in both windows at once.",
        "- Do not undo Acts 3 and 4: `kit.py restore --yes` reads each portal's end state first, then puts the seed back.",
        "- When Act 5 is done, run `kit.py restore --yes`, then `kit.py compare`.",
    ]
    for num, title, job, kind in ACTS:
        lines += ["", f"## Act {num}: {title}", "", f"Answer key: `{job}` ({kind} act).", "", "Prompt:", "",
                  "```text", prompts[num], "```", ""]
        lines += [f"- {text}" for text in exp[num]]
        if num == 5:
            lines += [
                f"- Right side, before sending: open `{rules_file}` and change `silent_days: {silent_days_now}` to "
                f"`silent_days: {new_days}`. The Pronova server reloads the file on every call.",
                "- Left side: the rules file was read at launch, so the sentence at the start of the prompt carries the change.",
            ]
    return "\n".join(lines) + "\n"


_PROMPT_RE = re.compile(r"^## Act (\d+)\b[^\n]*\n.*?^```text\n(.*?)^```", re.S | re.M)


def parse_script(text: str) -> dict[int, str]:
    prompts = {int(m.group(1)): m.group(2).rstrip("\n") for m in _PROMPT_RE.finditer(text)}
    if set(prompts) != {n for n, *_ in ACTS}:
        raise KitError("script.md does not hold a prompt block for each of Acts 1 to 5; run `kit.py prepare` again")
    return prompts


def write_workspace(home: Path, cfg: Config, day: str, *, seed_dir: Path, skills_dir: Path, prepared_at: datetime) -> None:
    truths = {s.name: read_json(truth_path(seed_dir, s, day)) for s in cfg.sides()}
    for name, truth in truths.items():
        if truth.get("meta", {}).get("as_of") != day:
            raise KitError(f"{truth_path(seed_dir, cfg.side(name), day)} is not the key for {day}")
    if any(truths["left"][job]["prompt"] != truths["right"][job]["prompt"] for job in ("job1", "job3", "job4", "job5")):
        raise KitError("the two answer keys carry different prompts; the portals were not seeded identically")
    rules_src = rules_source(seed_dir, cfg.right, day)
    rules_dest = home / "rules" / f"{cfg.right.portal_id}.yaml"
    rules_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(rules_src, rules_dest)
    rules_dest.chmod(0o600)
    (home / "sales-rules.md").write_text(render_rules_md(rules_dest), encoding="utf-8")
    silent_days = int((yaml.safe_load(rules_dest.read_text(encoding="utf-8")) or {}).get("silent_days", 14))

    for side in SIDES:
        folder = home / side
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / "mcp.json", mcp_json(cfg, side, home))
        launcher = folder / "launch.sh"
        launcher.write_text(launch_sh(cfg, side, day), encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        skills_target = folder / ".claude" / "skills"
        if skills_target.exists():
            shutil.rmtree(skills_target)
    for skill in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        shutil.copytree(skill, home / "right" / ".claude" / "skills" / skill.name)

    (home / "script.md").write_text(build_script(day, cfg, truths["right"], rules_dest, silent_days), encoding="utf-8")
    write_json(home / "state.json", {
        "version": 1, "day": day, "prepared_at": prepared_at.astimezone(UTC).isoformat(), "model": MODEL_ID,
        "portals": {s.name: {"portal_id": s.portal_id, "label": s.label, "token_env": s.token_env} for s in cfg.sides()},
        "servers": {"left": cfg.left_server, "right": cfg.right_server},
        "seed_dir": str(seed_dir),
        "truth": {s.name: str(truth_path(seed_dir, s, day)) for s in cfg.sides()},
        "snapshot": {s.name: str(snapshot_path(seed_dir, s, day)) for s in cfg.sides()},
        "rules": str(rules_dest), "restored_at": None,
    })


# --- restore -----------------------------------------------------------------------------------------------------


class SnapshotReader(PortalReader):
    """A PortalReader whose baseline is the seed snapshot, so ``end_state`` compares the portal with what seed.py wrote.

    Only the properties the snapshot holds are compared; properties the seed never wrote (a deal description, say)
    would otherwise show as changes from nothing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tracked: dict[str, list[str]] = {}

    def _props(self, table: str, raw: dict[str, Any]) -> dict[str, Any]:
        full = super()._props(table, raw)
        wanted = self._tracked.get(table)
        return full if wanted is None else {k: full.get(k) for k in wanted}

    def use_snapshot(self, snapshot: dict[str, Any]) -> None:
        key_prop = {"deals": "dealname", "contacts": "email", "companies": "name"}
        records = {table: snapshot[table] for table in key_prop}
        for table, recs in records.items():
            first = next(iter(recs.values()), None)
            if first is None:
                raise KitError(f"the snapshot holds no {table}")
            self._tracked[table] = [p for p in first["properties"] if p in TABLE_PROPS[table]]
        data: dict[str, dict[str, Any]] = {table: {} for table in key_prop}
        for table, recs in records.items():
            for rec in recs.values():
                props = self._props(table, rec["properties"])
                key = str(props.get(key_prop[table]) or rec["id"])
                if key in data[table]:
                    key = f"{key} #{rec['id']}"
                data[table][key] = {"id": str(rec["id"]), "props": props}
        self.load_baseline(data)


ReaderFactory = Callable[[str], Any]


def default_reader(token: str) -> Any:
    return SnapshotReader(token)


def drop_seeded(end_state: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """The snapshot is taken with a clock-skew margin, so a seeded engagement can look newer than it. Remove seeded ids
    from the created lists and recount."""
    seeded = {kind: {str(i) for i in ids} for kind, ids in (snapshot.get("seeded_engagement_ids") or {}).items()}
    created = end_state.get("created") or {}
    for kind, rows in list(created.items()):
        if kind in seeded:
            created[kind] = [r for r in rows if str(r.get("id")) not in seeded[kind]]
    counts = end_state.get("counts")
    if isinstance(counts, dict):
        for kind in created:
            if kind in counts:
                counts[kind] = len(created[kind])
    return end_state


def capture_end_state(side: Side, day: str, *, home: Path, seed_dir: Path, env: Mapping[str, str],
                      reader_factory: ReaderFactory) -> Path:
    snapshot = read_json(snapshot_path(seed_dir, side, day))
    token = (env.get(side.token_env) or "").strip()
    reader = reader_factory(token)
    reader.use_snapshot(snapshot)
    truth = read_json(truth_path(seed_dir, side, day))
    targets = [truth["job3"]["target_deal"]["dealname"], *(d["dealname"] for d in truth["job5"]["deals"])]
    state = drop_seeded(reader.end_state(snapshot["snapshot_at"], targets), snapshot)
    out = home / "runs" / day / f"endstate-{side.name}.json"
    write_json(out, mask_tree({
        "side": side.name, "label": side.label, "portal_id": side.portal_id, "day": day,
        "since": snapshot["snapshot_at"], "captured_at": datetime.now(UTC).isoformat(), "end_state": state}))
    return out


def saved_end_state(home: Path, side: Side, day: str, prepared: datetime) -> tuple[Path, datetime] | None:
    """The end-state file of this run, with its capture time, or None when it is missing or older than the prepare."""
    path = home / "runs" / day / f"endstate-{side.name}.json"
    if not path.is_file():
        return None
    captured = parse_ts(read_json(path).get("captured_at"), str(path))
    return (path, captured) if captured >= prepared else None


def restore(home: Path, *, yes: bool, skip_capture: bool, env: Mapping[str, str], runner: Runner,
            reader_factory: ReaderFactory = default_reader, recapture: bool = False,
            now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
    cfg = load_config(home)
    state = load_state(home)
    day, seed_dir = state["day"], Path(state["seed_dir"])
    names = [s.token_env for s in cfg.sides()]
    if not yes:
        print(f"restore for demo day {day} would: read each portal's end state against the seed snapshot "
              f"(read-only; saved under {home}/runs/{day}/), then run restore.py on both portals.")
        print("REFUSING: restore.py writes to both portals. Re-run with --yes.")
        return 2
    if missing := missing_tokens(cfg, env):
        print(f"REFUSING: set {', '.join(missing)} in the environment first.")
        return 2
    prepared = parse_ts(state["prepared_at"], "state.json prepared_at")
    if not skip_capture:
        for side in cfg.sides():
            kept = None if recapture else saved_end_state(home, side, day, prepared)
            if kept is not None:
                # A second capture would read the already restored portal and replace the evidence of the write acts.
                print(f"keeping the end state of {side.name} captured at {kept[1].isoformat()} (--recapture reads it again)")
                continue
            try:
                path = capture_end_state(side, day, home=home, seed_dir=seed_dir, env=env, reader_factory=reader_factory)
            except KitError as exc:
                print(f"FAILED: end state of {side.name}: {exc}\nNothing was restored. Fix it, or pass --skip-capture to restore "
                      "without keeping the evidence.")
                return 1
            except Exception as exc:  # noqa: BLE001 - any read failure must stop before restore.py removes the evidence
                print(f"FAILED: end state of {side.name}: {type(exc).__name__}: {redact(str(exc), env, names)[:200]}\n"
                      "Nothing was restored.")
                return 1
            print(f"captured: {path}")
    failed = []
    for side in cfg.sides():
        result = runner(restore_argv(seed_dir, side, day), env)
        if result.returncode != 0:
            failed.append(side.name)
            print(f"FAILED: restore.py ({side.name}) exited {result.returncode}\n{redact(tail(result.output), env, names)}")
        else:
            print(f"ok: restore.py ({side.name}, portal {side.portal_id})")
    if failed:
        print(f"Restore failed on: {', '.join(failed)}. Fix the cause and run `kit.py restore --yes` again: the end states "
              "already saved are kept.")
        return 1
    state["restored_at"] = now().astimezone(UTC).isoformat()
    write_json(home / "state.json", state)
    print("Both portals are back at seed state.")
    return 0


# --- transcripts -------------------------------------------------------------------------------------------------


def parse_ts(raw: Any, where: str) -> datetime:
    if not isinstance(raw, str):
        raise TranscriptError(f"{where}: record has no timestamp")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TranscriptError(f"{where}: bad timestamp {raw!r}") from exc


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def norm_prompt(text: str) -> str:
    """A typed prompt can carry the terminal's prompt marker; drop it, collapse whitespace, ignore case."""
    text = text.replace("\xa0", " ").strip().lstrip("❯>").strip()
    return " ".join(text.split()).casefold()


def is_prompt(rec: dict[str, Any]) -> bool:
    if rec.get("type") != "user" or rec.get("isMeta"):
        return False
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
        return False
    text = text_of(content).strip()
    return bool(text) and not text.startswith(("<", "Caveat:", "[Request interrupted"))


@dataclass
class Tool:
    id: str
    name: str
    start: datetime
    seconds: float | None = None
    is_error: bool = False


@dataclass
class Turn:
    prompt: str
    start: datetime
    end: datetime
    calls: list[dict[str, int]] = field(default_factory=list)
    tools: list[Tool] = field(default_factory=list)
    final_text: str = ""
    models: set[str] = field(default_factory=set)


USAGE_KEYS = {"input": "input_tokens", "cache_write": "cache_creation_input_tokens",
              "cache_read": "cache_read_input_tokens", "output": "output_tokens"}


def parse_transcript(path: Path) -> list[Turn]:
    """Turns of one Claude Code transcript. A turn starts at a prompt the person typed and runs to the next one."""
    turns: list[Turn] = []
    current: Turn | None = None
    seen: set[str] = set()
    by_id: dict[str, Tool] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        where = f"{path.name} line {n}"
        try:
            rec = json.loads(line)
        except ValueError as exc:
            raise TranscriptError(f"{where}: not JSON ({exc})") from exc
        if rec.get("type") not in ("user", "assistant") or rec.get("isSidechain"):
            continue
        when = parse_ts(rec.get("timestamp"), where)
        msg = rec.get("message")
        if not isinstance(msg, dict):
            raise TranscriptError(f"{where}: {rec.get('type')} record has no message")
        if rec["type"] == "user":
            if is_prompt(rec):
                current = Turn(prompt=text_of(msg.get("content")).strip(), start=when, end=when)
                turns.append(current)
                continue
            if current is None:
                continue
            blocks = msg.get("content") if isinstance(msg.get("content"), list) else []
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    # Only a tool result is work of the turn. The local-command records of /exit, /clear and /model are
                    # user records too and can be stamped long after the last answer.
                    current.end = max(current.end, when)
                    use_id = block.get("tool_use_id")
                    if not use_id:
                        raise TranscriptError(f"{where}: tool_result without tool_use_id")
                    tool = by_id.get(use_id)
                    if tool is not None:
                        tool.seconds = (when - tool.start).total_seconds()
                        tool.is_error = bool(block.get("is_error"))
            continue
        if current is None:
            continue
        current.end = max(current.end, when)
        if msg.get("model") == "<synthetic>":
            continue
        mid = msg.get("id")
        if not mid:
            raise TranscriptError(f"{where}: assistant record has no message.id")
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            raise TranscriptError(f"{where}: assistant record has no message.usage")
        for key in USAGE_KEYS.values():
            if not isinstance(usage.get(key), int):
                raise TranscriptError(f"{where}: usage lacks {key}")
        # One API response is split over several records (one per content block); count its usage once.
        if mid not in seen:
            seen.add(mid)
            current.calls.append({k: usage[v] for k, v in USAGE_KEYS.items()})
            if msg.get("model"):
                current.models.add(msg["model"])
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                if not block.get("id") or not block.get("name"):
                    raise TranscriptError(f"{where}: tool_use without id or name")
                if block["id"] not in by_id:
                    by_id[block["id"]] = Tool(block["id"], block["name"], when)
                    current.tools.append(by_id[block["id"]])
            elif block.get("type") == "text" and (block.get("text") or "").strip():
                current.final_text = block["text"]
    return turns


def first_timestamp(path: Path) -> datetime | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if '"timestamp"' not in line:
            continue
        try:
            raw = json.loads(line).get("timestamp")
        except ValueError:
            continue
        if isinstance(raw, str):
            return parse_ts(raw, path.name)
    return None


def project_dir_name(folder: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(folder.resolve()))


def side_transcripts(projects_dir: Path, folder: Path, since: datetime) -> list[Path]:
    """Transcripts of the session folder that started at or after ``since``, oldest first."""
    found = []
    for path in (projects_dir / project_dir_name(folder)).glob("*.jsonl"):
        started = first_timestamp(path)
        if started is not None and started >= since:
            found.append((started, path))
    return [p for _, p in sorted(found)]


def assign_acts(turns: list[Turn], prompts: dict[int, str],
                retakes: dict[int, int] | None = None) -> tuple[dict[int, list[Turn]], int]:
    """Each turn goes to the act whose prompt it starts with; a turn that matches none (a "yes" to a form) joins the
    act before it. A prompt sent again starts a new take that replaces the earlier one; ``retakes`` counts the repeats.
    Returns the acts and the number of turns before the first matched prompt."""
    order = sorted(prompts, key=lambda a: -len(norm_prompt(prompts[a])))
    acts: dict[int, list[Turn]] = defaultdict(list)
    current: int | None = None
    unmapped = 0
    for turn in turns:
        text = norm_prompt(turn.prompt)
        act = next((a for a in order if text.startswith(norm_prompt(prompts[a]))), None)
        if act is not None:
            current = act
            if act in acts:
                acts[act] = []
                if retakes is not None:
                    retakes[act] = retakes.get(act, 0) + 1
        if current is None:
            unmapped += 1
            continue
        acts[current].append(turn)
    return dict(acts), unmapped


@dataclass
class ActStats:
    turns: int = 0
    wall_s: float = 0.0
    form_wait_s: float = 0.0
    model_calls: int = 0
    hubspot_calls: int = 0
    input: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0
    ctx_first: int | None = None
    ctx_last: int | None = None
    tool_errors: int = 0
    answer_text: str = ""
    models: set[str] = field(default_factory=set)

    @property
    def working_s(self) -> float:
        return max(0.0, self.wall_s - self.form_wait_s)

    @property
    def cost(self) -> float:
        price = PRICES[PRICE_KEY]
        return (self.input * price["input"] + self.cache_write * price["cache_write_1h"]
                + self.cache_read * price["cache_read"] + self.output * price["output"]) / 1_000_000


def act_stats(turns: list[Turn]) -> ActStats:
    stats = ActStats(turns=len(turns))
    for index, turn in enumerate(turns):
        stats.wall_s += (turn.end - turn.start).total_seconds()
        for call in turn.calls:
            stats.model_calls += 1
            stats.input += call["input"]
            stats.cache_write += call["cache_write"]
            stats.cache_read += call["cache_read"]
            stats.output += call["output"]
            ctx = call["input"] + call["cache_write"] + call["cache_read"]
            stats.ctx_first = ctx if stats.ctx_first is None else stats.ctx_first
            stats.ctx_last = ctx
        for tool in turn.tools:
            if tool.name.startswith("mcp__"):
                stats.hubspot_calls += 1
            if tool.is_error:
                stats.tool_errors += 1
            if tool.name.rsplit("__", 1)[-1] in FORM_TOOLS and tool.seconds is not None:
                stats.form_wait_s += tool.seconds
        stats.models |= turn.models
        if index == 0:
            stats.answer_text = turn.final_text
    return stats


# --- grading against the day's answer key ------------------------------------------------------------------------


def codes_in(text: str) -> set[str]:
    low = text.casefold()
    return {code for code, words in CODE_WORDS.items() if code.casefold() in low or any(w in low for w in words)}


def grade_triage(text: str, truth: dict[str, Any]) -> dict[str, Any]:
    """Which flagged deals the answer names, and whether each carries its codes. A deal's codes are those on its own
    line, or on the header of the section it sits under (a bulk group is listed under its close-date header only)."""
    owner = truth["meta"].get("demo_owner", "demo")
    flagged = truth["job1"]["owners"][owner]["flagged"]
    section: set[str] = set()
    placed: list[tuple[str, set[str]]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("━", "#")):
            section = codes_in(stripped)
        placed.append((grade.norm_name(line), codes_in(line) | section))
    found, code_hits, code_total, missing = 0, 0, 0, []
    for item in flagged:
        name = grade.norm_name(item["dealname"])
        hits = [c for line, c in placed if name in line]
        want = {c.upper() for c in item.get("codes", [])}
        code_total += len(want)
        if not hits:
            missing.append(item["dealname"])
            continue
        found += 1
        code_hits += len(want & set().union(*hits))
    total = len(flagged)
    return {"found": found, "total": total, "codes_matched": code_hits, "codes_total": code_total, "missing": missing[:5],
            "score": found / total if total else 1.0}


def numbers_in(text: str) -> list[float]:
    """Every number in the text, in every plausible reading of its separators (1,234.5 / 1.234,5 / 1234) and with k and m
    suffixes applied, so a figure matches however the answer formats it."""
    out: list[float] = []
    for m in re.finditer(r"\d[\d.,  ]*\d|\d", text):
        token = m.group(0).replace(" ", "").replace(" ", "")
        reads = {token.replace(",", ""), token.replace(".", "").replace(",", "."), re.sub(r"[.,]", "", token)}
        mult = 1.0
        suffix = re.match(r"\s?([kKmM])(?![A-Za-z])", text[m.end():m.end() + 3])
        if suffix:
            mult = 1e3 if suffix.group(1).lower() == "k" else 1e6
        for read in reads:
            try:
                out.append(float(read) * mult)
            except ValueError:
                continue
    return out


def grade_forecast(text: str, truth: dict[str, Any]) -> dict[str, Any]:
    owner = truth["meta"].get("demo_owner", "demo")
    block = truth["job4"]["owners"][owner]
    tol = grade.tolerance_from_truth(truth)
    values = numbers_in(text)
    figures: dict[str, bool | None] = {}
    for key, tkey in grade.FIGURES.items():
        target = float(block[tkey]["total"])
        figures[key] = None if target == 0 else any(abs(v - target) <= tol * abs(target) + 1e-9 for v in values)
    graded = [v for v in figures.values() if v is not None]
    norm = grade.norm_name(text)
    removed = [d["dealname"] for d in block.get("supported_commit_removed", [])]
    return {"figures": figures, "passed": sum(graded), "graded": len(graded), "tolerance": tol,
            "at_risk_named": sum(1 for n in removed if grade.norm_name(n) in norm), "at_risk_total": len(removed)}


def split_end_state(end_state: dict[str, Any], truth: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """One capture holds both write acts. job3's view keeps every deal outside the past-due set and every record that is
    not attached to a past-due deal; job5's keeps the past-due deals and the records attached to them. A change outside
    both sets stays in both views so neither grader misses it."""
    past_due = {grade.norm_name(d["dealname"]) for d in truth["job5"]["deals"]}
    target = grade.norm_name(truth["job3"]["target_deal"]["dealname"])
    es = end_state

    def pick(mapping: Any, keep: Callable[[str], bool]) -> dict[str, Any]:
        return {k: v for k, v in (mapping or {}).items() if keep(grade.norm_name(k))}

    def on_past_due(row: dict[str, Any]) -> bool:
        deals = (row.get("associations") or {}).get("deals") or []
        return bool({grade.norm_name(n) for n in deals} & past_due)

    common = {k: es.get(k) for k in ("tz", "stable", "rounds", "read_at", "contacts_changed", "companies_changed",
                                     "deals_missing", "deals_created")}
    created = es.get("created") or {}
    job3 = {**common,
            "deals_target": pick(es.get("deals_target"), lambda n: n not in past_due),
            "deals_changed": pick(es.get("deals_changed"), lambda n: n not in past_due),
            "created": {k: [r for r in rows if not on_past_due(r)] for k, rows in created.items()}}
    job5 = {**common,
            "deals_target": pick(es.get("deals_target"), lambda n: n in past_due),
            "deals_changed": pick(es.get("deals_changed"), lambda n: n != target),
            "created": {k: ([r for r in rows if on_past_due(r)] if k != "contacts" else []) for k, rows in created.items()}}
    return {"job3": job3, "job5": job5}


def grade_write(job: str, end_state: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    view = split_end_state(end_state, truth)[job]
    result = grade.grade_job3(view, {}, truth) if job == "job3" else grade.grade_job5(view, {}, truth)
    failed = [k for k, v in result["checks"].items() if v is False or (isinstance(v, float) and v < 1.0)]
    return {"score": result["score"], "exact": result["exact"], "failed": failed, "violations": result["violations"],
            "details": {k: v for k, v in result["details"].items() if k != "claim_mismatch"}}


# --- the report --------------------------------------------------------------------------------------------------


def fmt_s(value: float) -> str:
    return f"{value:.1f}s"


def fmt_n(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def quality_row(act: int, stats: ActStats | None, truth: dict[str, Any], end_state: dict[str, Any] | None) -> tuple[str, str]:
    if stats is None:
        return "not run", "no matching prompt in the transcript"
    if act == 5:
        return "not graded", "no answer key for a changed rule"
    if act in WRITE_ACTS:
        if end_state is None:
            return "no end state", "run `kit.py restore` before `compare`, or the capture was skipped"
        g = grade_write(WRITE_ACTS[act], end_state, truth)
        detail = f"failed checks: {', '.join(g['failed']) or 'none'}; violations: {len(g['violations'])}"
        if g["violations"]:
            detail += " (" + "; ".join(g["violations"][:3]) + ")"
        return f"{g['score']:.2f}{' exact' if g['exact'] else ''}", detail
    if act == 1:
        t = grade_triage(stats.answer_text, truth)
        detail = f"deals named {t['found']}/{t['total']}, codes {t['codes_matched']}/{t['codes_total']}"
        if t["missing"]:
            detail += "; missing e.g. " + ", ".join(t["missing"][:3])
        return f"{t['score']:.2f}", detail
    f = grade_forecast(stats.answer_text, truth)
    wrong = [k for k, v in f["figures"].items() if v is False]
    detail = (f"figures {f['passed']}/{f['graded']} within {f['tolerance'] * 100:g}%"
              f"{'; off: ' + ', '.join(wrong) if wrong else ''}; at-risk deals named {f['at_risk_named']}/{f['at_risk_total']}")
    return f"{f['passed']}/{f['graded']}", detail


def build_report(day: str, cfg: Config, stats: dict[str, dict[int, ActStats]], truths: dict[str, dict[str, Any]],
                 end_states: dict[str, dict[str, Any] | None], notes: list[str]) -> str:
    price = PRICES[PRICE_KEY]
    lines = [
        f"# Demo comparison for {day}",
        "",
        f"Left: HubSpot's official MCP (`{cfg.left_server}`, portal {cfg.left.portal_id}), no skills. "
        f"Right: Pronova intent server (portal {cfg.right.portal_id}) with skills. Same model ({MODEL_ID}), same sales rules, "
        f"identical seeded data. Cost uses {PRICE_KEY} list prices per million tokens: input ${price['input']}, "
        f"cache write ${price['cache_write_1h']}, cache read ${price['cache_read']}, output ${price['output']}.",
        "",
        "## Per act",
        "",
        "| Act | Side | Turns | Wall | Form wait | Working | Model calls | HubSpot calls | Input | Cache write | Cache read "
        "| Output | Context first | Context last | Cost | Tool errors |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for num, title, _, _ in ACTS:
        for side in SIDES:
            s = stats[side].get(num)
            if s is None:
                lines.append(f"| {num} {title} | {side} | not run |" + " - |" * 13)
                continue
            lines.append(
                f"| {num} {title} | {side} | {s.turns} | {fmt_s(s.wall_s)} | {fmt_s(s.form_wait_s)} | {fmt_s(s.working_s)} "
                f"| {s.model_calls} | {s.hubspot_calls} | {fmt_n(s.input)} | {fmt_n(s.cache_write)} | {fmt_n(s.cache_read)} "
                f"| {fmt_n(s.output)} | {fmt_n(s.ctx_first)} | {fmt_n(s.ctx_last)} | ${s.cost:.4f} | {s.tool_errors} |")
    lines += ["", "## Totals", "", "| Side | Wall | Form wait | Working | Model calls | HubSpot calls | Output tokens | Cost | Tool errors |",
              "|---|---|---|---|---|---|---|---|---|"]
    for side in SIDES:
        rows = list(stats[side].values())
        lines.append(
            f"| {side} | {fmt_s(sum(r.wall_s for r in rows))} | {fmt_s(sum(r.form_wait_s for r in rows))} "
            f"| {fmt_s(sum(r.working_s for r in rows))} | {sum(r.model_calls for r in rows)} "
            f"| {sum(r.hubspot_calls for r in rows)} | {fmt_n(sum(r.output for r in rows))} "
            f"| ${sum(r.cost for r in rows):.4f} | {sum(r.tool_errors for r in rows)} |")
    lines += ["", "## Quality against the answer key", "", "| Act | Side | Result | Detail |", "|---|---|---|---|"]
    for num, title, _, _ in ACTS:
        for side in SIDES:
            result, detail = quality_row(num, stats[side].get(num), truths[side], end_states.get(side))
            lines.append(f"| {num} {title} | {side} | {result} | {detail} |")
    lines += ["", "## How to read this", "",
              "- **Wall** is the time from the prompt to the last record of the act, follow-ups such as a \"yes\" included; "
              "time between acts is not counted.",
              "- **Form wait** is the latency of `pronova_apply_changes` and `pronova_undo_changes`: the time the "
              "confirmation form waited for the person. **Working** is wall minus form wait. The left side has no form.",
              "- Triage and forecast are graded from the answer text: a deal counts when its name appears, its codes when "
              "the code word or a plain-language synonym sits on its line or under its section header, and a figure when "
              "any number in the text matches the key within the key's tolerance.",
              "- The write acts are graded from one end-state capture per portal, taken before restore. Records are "
              "attributed to Act 3 when they are attached to a past-due deal, otherwise to Act 2."]
    lines += [f"- {n}" for n in notes]
    return mask_emails("\n".join(lines) + "\n")


def compare(home: Path, day_arg: str | None, *, projects_dir: Path) -> int:
    cfg = load_config(home)
    state = load_state(home)
    day = state["day"]
    if day_arg and day_arg != day:
        raise KitError(f"state.json is for {day}, not {day_arg}; run `kit.py prepare --day {day_arg} --yes` first")
    prepared = parse_ts(state["prepared_at"], "state.json prepared_at")
    script = home / "script.md"
    if not script.is_file():
        raise KitError(f"{script} not found; run `kit.py prepare` first")
    prompts = parse_script(script.read_text(encoding="utf-8"))
    truths = {s: read_json(Path(state["truth"][s])) for s in SIDES}
    stats: dict[str, dict[int, ActStats]] = {}
    end_states: dict[str, dict[str, Any] | None] = {}
    notes: list[str] = []
    for side in SIDES:
        paths = side_transcripts(projects_dir, home / side, prepared)
        if not paths:
            raise KitError(f"no {side} transcript in {projects_dir / project_dir_name(home / side)} started after "
                           f"{prepared.isoformat()}")
        turns = [t for p in paths for t in parse_transcript(p)]
        retakes: dict[int, int] = {}
        acts, unmapped = assign_acts(turns, prompts, retakes)
        notes += [f"{side}: Act {n} was sent {c + 1} times; only the last take is reported." for n, c in sorted(retakes.items())]
        stats[side] = {n: act_stats(ts) for n, ts in acts.items()}
        notes.append(f"{side}: {len(paths)} transcript file(s), {len(turns)} turn(s)"
                     f"{f', {unmapped} before the first act prompt ignored' if unmapped else ''}.")
        models = set().union(*(s.models for s in stats[side].values())) if stats[side] else set()
        if models and not all("sonnet-5" in m for m in models):
            notes.append(f"{side} ran {', '.join(sorted(models))}; costs still use {PRICE_KEY} prices.")
        es_path = home / "runs" / day / f"endstate-{side}.json"
        end_states[side] = None
        if es_path.is_file():
            saved = read_json(es_path)
            if parse_ts(saved.get("captured_at"), str(es_path)) >= prepared:
                end_states[side] = saved["end_state"]
            else:
                notes.append(f"{side}: the saved end state was captured before this prepare and is ignored; run "
                             "`kit.py restore --yes --recapture` before `compare`.")
        id_re = re.compile(rf"(?<!\d){re.escape(cfg.left.portal_id)}(?!\d)")
        if side == "left" and not any(id_re.search(p.read_text(encoding="utf-8")) for p in paths):
            notes.append(f"WARNING: no left transcript record names portal {cfg.left.portal_id}. Confirm the left window's "
                         "HubSpot login points at that portal; the OAuth login is not tied to it by the kit.")
    text = build_report(day, cfg, stats, truths, end_states, notes)
    out = home / f"report-{day}.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"written: {out}")
    return 0


# --- command line ------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", help=f"demo folder (default ~/{HOME_NAME})")
    parser = argparse.ArgumentParser(description="Two-portal demo kit (see demo/README.md).")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", parents=[common], help="write portals.json")
    setup.add_argument("--left-portal", required=True, help="portal id of the left side (the official HubSpot MCP)")
    setup.add_argument("--right-portal", required=True, help="portal id of the right side (Pronova)")
    setup.add_argument("--left-server", default=DEFAULT_LEFT_SERVER,
                       help=f"name the official MCP is registered under in Claude Code (default {DEFAULT_LEFT_SERVER})")

    for name, helptext in (("check", "read-only preflight on both portals"),
                           ("prepare", "reset and seed both portals, write the demo folders")):
        cmd = sub.add_parser(name, parents=[common], help=helptext)
        cmd.add_argument("--day", required=True, help="demo day YYYY-MM-DD, or 'today'")
        cmd.add_argument("--seed-dir", help=f"hubspot-forecast demo/seed folder (default ${SEED_DIR_ENV} or {DEFAULT_SEED_DIR})")
        if name == "prepare":
            cmd.add_argument("--yes", action="store_true", help="confirm: resets and re-seeds both portals")
            cmd.add_argument("--skills-dir", help=f"skills folder for the right side (default {DEFAULT_SKILLS_DIR})")

    rest = sub.add_parser("restore", parents=[common], help="save both end states, then put both portals back")
    rest.add_argument("--yes", action="store_true", help="confirm: restore.py writes to both portals")
    rest.add_argument("--skip-capture", action="store_true", help="restore without saving the end states")
    rest.add_argument("--recapture", action="store_true",
                      help="read the end states again even when this prepare already saved them")

    comp = sub.add_parser("compare", parents=[common], help="report from the two sessions' transcripts")
    comp.add_argument("--day", help="demo day; must match state.json")
    comp.add_argument("--projects-dir", help="Claude Code projects folder (default ~/.claude/projects)")
    return parser


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None, runner: Runner = subprocess_runner,
         reader_factory: ReaderFactory = default_reader) -> int:
    args = build_parser().parse_args(argv)
    env = os.environ if env is None else env
    home = home_dir(args.home)
    try:
        if args.command == "setup":
            cfg = build_config(args.left_portal, args.right_portal, args.left_server)
            write_json(home / "portals.json", cfg.to_json())
            print(f"wrote {home / 'portals.json'}\nexport {cfg.left.token_env} and {cfg.right.token_env} before check, "
                  "prepare and restore (names only are stored).")
            return 0
        if args.command == "check":
            return check(home, parse_day(args.day), seed_dir=seed_dir_from(args.seed_dir, env), env=env, runner=runner)
        if args.command == "prepare":
            return prepare(home, parse_day(args.day), yes=args.yes, seed_dir=seed_dir_from(args.seed_dir, env),
                           skills_dir=skills_dir_from(args.skills_dir, env), env=env, runner=runner)
        if args.command == "restore":
            return restore(home, yes=args.yes, skip_capture=args.skip_capture, env=env, runner=runner,
                           reader_factory=reader_factory, recapture=args.recapture)
        projects = Path(args.projects_dir).expanduser() if args.projects_dir else Path.home() / ".claude" / "projects"
        return compare(home, parse_day(args.day) if args.day else None, projects_dir=projects)
    except KitError as exc:
        print(f"error: {mask_emails(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
