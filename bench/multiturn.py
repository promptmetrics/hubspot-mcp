"""Multi-turn bench: one Claude Code session, several user turns, one skill.

Measures whether the model re-calls the lookup (find_capabilities / hubspot_find_skills) and
the loader (load_skill / hubspot_load_skill) on later turns or reuses what is already in its
context, and what each turn costs. Each user turn is its own `claude -p` process: turn 1 with
`--session-id`, later turns with `--resume`, so per-turn usage comes straight from that
process's assistant events. `total_cost_usd` is cumulative over the session; the per-turn cost
is the difference.

Every scenario also runs as single-turn sessions (each prompt on its own) so a three-turn
session can be compared with three one-turn sessions for the same work.

Usage:
  .venv/bin/python bench/multiturn.py --smoke --test-contact "..." --test-deal "..."
  .venv/bin/python bench/multiturn.py --reps 3 --single-reps 2 \
      --arm-url routed-jev=http://127.0.0.1:8000/mcp/routed --arm-url full=http://127.0.0.1:8000/mcp \
      --test-contact "..." --test-deal "..."
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run as bench  # noqa: E402
from mcp_client import Surface, measure_tools_list  # noqa: E402

HERE = Path(__file__).resolve().parent
LOOKUP_TOOLS = {"find_capabilities", "hubspot_find_skills"}
LOAD_TOOLS = {"load_skill", "hubspot_load_skill"}


def load_scenarios(path: Path, subs: dict[str, str]) -> list[dict[str, Any]]:
    text = path.read_text()
    for key, value in subs.items():
        text = text.replace("{" + key + "}", value)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def run_turn(prompt: str, cfg: dict[str, Any], model: str | None, cwd: Path, timeout: int,
             session_id: str, first: bool) -> dict[str, Any]:
    claude = shutil.which("claude")
    if not claude:
        raise RuntimeError("claude CLI not found on PATH")
    cfg_path = cwd / "mcp-config.json"
    if first:
        cfg_path.write_text(json.dumps(cfg))
    cmd = [
        claude, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--tools", "",
        "--setting-sources", "",
        "--strict-mcp-config",
        "--mcp-config", str(cfg_path),
        "--permission-mode", "bypassPermissions",
        "--session-id" if first else "--resume", session_id,
    ]
    if model:
        cmd += ["--model", model]
    started = time.perf_counter()
    proc = subprocess.run(  # noqa: S603 — argv list, no shell; the prompt is our own scenario text
        cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout
    )
    wall_ms = int((time.perf_counter() - started) * 1000)
    events = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            events.append({"type": "nonjson", "raw": line[:500]})
    return {"events": events, "stderr": proc.stderr[-4000:], "returncode": proc.returncode, "wall_ms": wall_ms}


def turn_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    names = summary["hubspot_tool_names"]
    t = summary["totals"]
    return {
        "lookup_calls": sum(1 for n in names if n in LOOKUP_TOOLS),
        "load_calls": sum(1 for n in names if n in LOAD_TOOLS),
        "hubspot_calls": sum(1 for n in names if n not in bench.ROUTING_TOOLS),
        "api_calls": summary["api_calls"],
        "first_call_context_tokens": summary["first_call_context_tokens"],
        "context_tokens_all_calls": t["context_tokens_all_calls"],
        "cache_read": t["cache_read_input_tokens"],
        "cache_write": t["cache_creation_input_tokens"],
        "input_tokens": t["input_tokens"],
        "output_tokens": t["output_tokens"],
        "tool_errors": summary["hubspot_tool_errors"],
        "num_turns": summary["num_turns"],
        "duration_ms": summary["duration_ms"],
        "cost_cumulative": summary["total_cost_usd"],
    }


def forget_session(session_id: str) -> list[str]:
    """Remove the transcript Claude Code saved for this session (it carries portal data)."""
    removed = []
    for path in (Path.home() / ".claude" / "projects").glob(f"*/{session_id}*"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        removed.append(str(path))
        parent = path.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    return removed


def run_session(scenario: dict[str, Any], arm: str, args: argparse.Namespace, secret: str | None,
                mode: str, turn_ids: list[str]) -> dict[str, Any]:
    cfg = bench.mcp_config(arm, args.server, secret)
    cwd = Path(tempfile.mkdtemp(prefix="hs-bench-mt-"))
    session_id = str(uuid.uuid4())
    turns_out: list[dict[str, Any]] = []
    merged_calls: list[dict[str, Any]] = []
    prev_cost = 0.0
    raw_events: list[list[dict[str, Any]]] = []
    try:
        for i, turn in enumerate([t for t in scenario["turns"] if t["id"] in turn_ids]):
            first = i == 0
            prompt = turn["prompt"] if mode == "multi" else turn.get("single_prompt", turn["prompt"])
            if first:
                prompt += bench.SUFFIX
            try:
                raw = run_turn(prompt, cfg, args.model, cwd, args.timeout, session_id, first)
            except subprocess.TimeoutExpired:
                raw = {"events": [], "stderr": "timeout", "returncode": -1, "wall_ms": args.timeout * 1000}
            raw_events.append(raw["events"])
            summary = bench.summarize(raw["events"])
            task = {"id": f"{scenario['id']}-{turn['id']}", "kind": scenario["kind"], "check": turn["check"]}
            verdict = bench.check(task, summary, {"pending": [], "audit": []}, arm)
            metrics = turn_metrics(summary)
            metrics["cost_delta"] = round(metrics["cost_cumulative"] - prev_cost, 6)
            prev_cost = metrics["cost_cumulative"]
            merged_calls.extend(summary["tool_calls"])
            turns_out.append({
                "turn": i + 1, "prompt_id": turn["id"], "verdict": verdict, **metrics,
                "hubspot_tool_names": summary["hubspot_tool_names"],
                "routing": bench._routing_events(summary),
                "final_text": summary["final_text"], "returncode": raw["returncode"], "stderr": raw["stderr"],
            })
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
    merged = {"tool_calls": merged_calls, "final_text": ""}
    return {
        "session_id": session_id, "turns": turns_out, "merged_summary": merged, "events": raw_events,
        "session": {
            "cost": prev_cost,
            "duration_ms": sum(t["duration_ms"] for t in turns_out),
            "api_calls": sum(t["api_calls"] for t in turns_out),
            "lookup_calls": sum(t["lookup_calls"] for t in turns_out),
            "load_calls": sum(t["load_calls"] for t in turns_out),
            "hubspot_calls": sum(t["hubspot_calls"] for t in turns_out),
            "context_tokens_all_calls": sum(t["context_tokens_all_calls"] for t in turns_out),
            "cache_read": sum(t["cache_read"] for t in turns_out),
            "cache_write": sum(t["cache_write"] for t in turns_out),
            "passed": all(t["verdict"]["passed"] for t in turns_out) if turns_out else False,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--secret-env", default="HUBSPOT_MCP_SERVER_SECRET")
    ap.add_argument("--scenarios", default=str(HERE / "scenarios.jsonl"))
    ap.add_argument("--arm-url", action="append", default=[], metavar="NAME=URL")
    ap.add_argument("--reps", type=int, default=3, help="multi-turn sessions per scenario and arm")
    ap.add_argument("--single-reps", type=int, default=2, help="single-turn baseline sessions per prompt and arm")
    ap.add_argument("--model", default=None)
    ap.add_argument("--only", default=None, help="comma-separated scenario ids")
    ap.add_argument("--test-contact", default=os.getenv("BENCH_TEST_CONTACT", "Test Contact"))
    ap.add_argument("--test-deal", default=os.getenv("BENCH_TEST_DEAL", "Test Deal"))
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default=None)
    ap.add_argument("--smoke", action="store_true", help="s02 only, first arm only, two turns, one multi session")
    ap.add_argument("--no-cleanup", action="store_true")
    args = ap.parse_args()

    secret = os.getenv(args.secret_env) or None
    for spec in args.arm_url:
        name, _, url = spec.partition("=")
        bench.ARM_URLS[name] = url
    arms = list(bench.ARM_URLS) or ["routed", "full"]
    subs = {"TEST_CONTACT": args.test_contact, "TEST_CONTACT_FIRST": args.test_contact.split()[0], "TEST_DEAL": args.test_deal}
    scenarios = load_scenarios(Path(args.scenarios), subs)
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s["id"] in wanted]
    reps, single_reps = args.reps, args.single_reps
    if args.smoke:
        scenarios = [s for s in scenarios if s["id"] == "s02"] or scenarios[:1]
        arms, reps, single_reps = arms[:1], 1, 0
        for s in scenarios:
            s["turns"] = s["turns"][:2]

    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    out = Path(args.out or HERE / "runs" / f"{stamp}-multiturn")
    out.mkdir(parents=True, exist_ok=True)

    static: dict[str, Any] = {}
    for arm in arms:
        s = Surface(bench.arm_url(arm, args.server), secret)
        s.initialize()
        tools = s.list_tools()
        static[arm] = measure_tools_list(tools)
        expected = bench._expected_router(arm)
        if expected and "find_capabilities" in {t.get("name") for t in tools}:
            probe, _ = s.call_tool("find_capabilities", {"task": "list our active workflows"})
            router = probe.get("router") if isinstance(probe, dict) else None
            static[arm]["router"] = router
            if router != expected:
                print(f"abort: arm {arm} routes with {router!r}, expected {expected!r}")
                return 2
    (out / "tools_list.json").write_text(json.dumps(static, indent=2))
    print("tools/list:", json.dumps(static))
    (out / "meta.json").write_text(json.dumps({
        "started": stamp, "server": args.server, "arms": arms, "reps": reps, "single_reps": single_reps,
        "model": args.model, "scenarios": [s["id"] for s in scenarios],
        "claude_version": subprocess.run([shutil.which("claude") or "claude", "--version"], capture_output=True, text=True).stdout.strip(),  # noqa: S603
        "placeholders": {"TEST_CONTACT": "{TEST_CONTACT}", "TEST_DEAL": "{TEST_DEAL}"},
    }, indent=2))

    surfaces: dict[str, Surface] = {}

    def full_surface(arm: str) -> Surface:
        base = bench.full_url_for(arm, args.server)
        if base not in surfaces:
            surfaces[base] = Surface(base, secret)
            surfaces[base].initialize()
        return surfaces[base]

    jobs: list[tuple[dict[str, Any], str, int, str, list[str]]] = []
    for rep in range(1, reps + 1):
        for sc in scenarios:
            rotated = arms[rep % len(arms):] + arms[: rep % len(arms)]
            for arm in rotated:
                jobs.append((sc, arm, rep, "multi", [t["id"] for t in sc["turns"]]))
    for rep in range(1, single_reps + 1):
        for sc in scenarios:
            for turn in sc["turns"]:
                rotated = arms[rep % len(arms):] + arms[: rep % len(arms)]
                for arm in rotated:
                    jobs.append((sc, arm, rep, "single", [turn["id"]]))

    sessions_path = out / "sessions.jsonl"
    for n, (sc, arm, rep, mode, turn_ids) in enumerate(jobs, 1):
        since = datetime.now(UTC).isoformat()
        label = f"[{n}/{len(jobs)}] {mode} rep {rep} {sc['id']} {'+'.join(turn_ids)} {arm}"
        print(label, "...", end=" ", flush=True)
        result = run_session(sc, arm, args, secret, mode, turn_ids)
        state: dict[str, Any] = {"pending": [], "audit": []}
        cleaned: dict[str, Any] = {}
        try:
            fs = full_surface(arm)
            state = bench.portal_state(fs, since)
            if not args.no_cleanup:
                cleaned = bench.cleanup(fs, result["merged_summary"], state)
        except RuntimeError as exc:
            state = {"pending": [], "audit": [], "error": str(exc)}
        forgotten = forget_session(result["session_id"])
        events = result.pop("events")
        result.pop("merged_summary")
        record = {"n": n, "scenario": sc["id"], "kind": sc["kind"], "skill": sc["skill"], "arm": arm, "rep": rep,
                  "mode": mode, "model": args.model, "since": since, "state": state, "cleanup": cleaned,
                  "forgotten": forgotten, **result}
        with sessions_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
        for i, evs in enumerate(events, 1):
            (out / f"events-{n:03d}-{sc['id']}-{arm}-{mode}-r{rep}-t{i}.jsonl").write_text("\n".join(json.dumps(e) for e in evs))
        s = result["session"]
        per_turn = " ".join(
            f"t{t['turn']}:look={t['lookup_calls']} load={t['load_calls']} hs={t['hubspot_calls']} api={t['api_calls']} "
            f"ctx={t['context_tokens_all_calls']} wr={t['cache_write']} ${t['cost_delta']:.3f} ok={t['verdict']['passed']}"
            for t in result["turns"]
        )
        print(f"ok={s['passed']} cost=${s['cost']:.3f} ms={s['duration_ms']} | {per_turn}", flush=True)
    print("done ->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
