#!/usr/bin/env python3
"""Paired headless sessions: control vs /mcp (86 tools) vs /mcp/routed (3 tools).

Each task runs through Claude Code in print mode, once per arm, interleaved, for
N reps. The stream-json transcript gives exact API usage per call, the tool-call
sequence with timing, and the final cost and duration. After each session the
runner checks completion, rejects any pending previews and undoes AUTO writes so
the next rep starts clean.

Usage (server already running locally, see bench/README.md):

    .venv/bin/python bench/run.py --server http://127.0.0.1:8000 --reps 3 \
        --test-contact "Ada Example" --test-deal "Example Corp renewal"
    .venv/bin/python bench/run.py --dry-run          # 2 tasks x 1 rep x 3 arms

Transcripts land in bench/runs/<timestamp>/ (gitignored: they carry portal data).
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import Surface, measure_tools_list  # noqa: E402

HERE = Path(__file__).resolve().parent
ARMS = ("control", "full", "routed")
MCP_PREFIX = "mcp__hubspot__"
# Meta-tools that route or load text rather than touch HubSpot, on either surface.
ROUTING_TOOLS = frozenset({"find_capabilities", "load_charter", "load_skill", "hubspot_find_skills", "hubspot_load_skill", "hubspot_route"})

# The same operator framing in every arm, so the arms differ only in the MCP
# surface. Confirmation-tier writes must stay previews: that is the behaviour
# being compared, and it keeps the real portal unchanged between reps.
SUFFIX = (
    "\n\nWork on your own; do not ask me questions. Use the HubSpot tools available to you. "
    "If a write returns a preview that needs my confirmation, do not approve it: report the "
    "preview and stop. Finish with a short answer."
)


def load_tasks(path: Path, subs: dict[str, str]) -> list[dict[str, Any]]:
    tasks = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        task = json.loads(line)
        for key, value in subs.items():
            task["prompt"] = task["prompt"].replace("{" + key + "}", value)
            task["check"] = json.loads(json.dumps(task["check"]).replace("{" + key + "}", value))
        tasks.append(task)
    return tasks


def mcp_config(arm: str, server: str, secret: str | None) -> dict[str, Any]:
    if arm == "control":
        return {"mcpServers": {}}
    entry: dict[str, Any] = {"type": "http", "url": server.rstrip("/") + ("/mcp" if arm == "full" else "/mcp/routed")}
    if secret:
        entry["headers"] = {"Authorization": f"Bearer {secret}"}
    return {"mcpServers": {"hubspot": entry}}


def run_claude(prompt: str, cfg: dict[str, Any], model: str | None, cwd: Path, timeout: int) -> dict[str, Any]:
    claude = shutil.which("claude")
    if not claude:
        raise RuntimeError("claude CLI not found on PATH")
    # The config carries the bearer secret; a file in the per-session temp dir
    # keeps it out of `ps` output, unlike a JSON string on the command line.
    cfg_path = cwd / "mcp-config.json"
    cfg_path.write_text(json.dumps(cfg))
    cmd = [
        claude, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--tools", "",
        "--no-session-persistence",
        "--setting-sources", "",
        "--strict-mcp-config",
        "--mcp-config", str(cfg_path),
        "--permission-mode", "bypassPermissions",
    ]
    if model:
        cmd += ["--model", model]
    started = time.perf_counter()
    proc = subprocess.run(  # noqa: S603 — argv list, no shell; the prompt is our own task text
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


def _ts(ev: dict[str, Any]) -> float | None:
    raw = ev.get("timestamp")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return raw / 1000 if raw > 1e12 else float(raw)
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    api_calls: list[dict[str, Any]] = []
    tool_calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    result: dict[str, Any] = {}
    init: dict[str, Any] = {}
    for ev in events:
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            init = {"tools": ev.get("tools"), "mcp_servers": ev.get("mcp_servers"), "model": ev.get("model")}
        elif t == "assistant":
            msg = ev.get("message", {})
            usage = msg.get("usage") or {}
            api_calls.append(
                {
                    "input_tokens": usage.get("input_tokens", 0),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "ts": _ts(ev),
                }
            )
            for block in msg.get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tid = block.get("id") or f"anon-{len(order)}"
                    tool_calls[tid] = {
                        "id": tid,
                        "name": block.get("name"),
                        "input": block.get("input"),
                        "input_chars": len(json.dumps(block.get("input") or {})),
                        "call_ts": _ts(ev),
                    }
                    order.append(tid)
        elif t == "user":
            msg = ev.get("message", {})
            for block in msg.get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id")
                    if tid in tool_calls:
                        text = _result_text(block)
                        tool_calls[tid].update(
                            {
                                "is_error": bool(block.get("is_error")),
                                "result_chars": len(text),
                                "result_text": text[:20000],
                                "result_ts": _ts(ev),
                            }
                        )
        elif t == "result":
            result = ev

    calls = [tool_calls[t] for t in order]
    for c in calls:
        if c.get("call_ts") is not None and c.get("result_ts") is not None:
            c["latency_ms"] = int((c["result_ts"] - c["call_ts"]) * 1000)
    hubspot_calls = [c for c in calls if str(c.get("name", "")).startswith(MCP_PREFIX)]

    first = api_calls[0] if api_calls else {}
    totals = {
        k: sum(c.get(k, 0) for c in api_calls)
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    }
    totals["context_tokens_all_calls"] = (
        totals["input_tokens"] + totals["cache_creation_input_tokens"] + totals["cache_read_input_tokens"]
    )
    return {
        "init": init,
        "api_calls": len(api_calls),
        "first_call_context_tokens": first.get("input_tokens", 0)
        + first.get("cache_creation_input_tokens", 0)
        + first.get("cache_read_input_tokens", 0),
        "totals": totals,
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        "duration_api_ms": result.get("duration_api_ms"),
        "total_cost_usd": result.get("total_cost_usd"),
        "result_subtype": result.get("subtype"),
        "result_is_error": result.get("is_error"),
        "final_text": (result.get("result") or "")[:8000],
        "model_usage": result.get("modelUsage"),
        "tool_calls": calls,
        "hubspot_tool_names": [c["name"][len(MCP_PREFIX):] for c in hubspot_calls],
        "hubspot_tool_errors": sum(1 for c in hubspot_calls if c.get("is_error")),
    }


def _server_side_names(summary: dict[str, Any]) -> list[str]:
    """HubSpot tools actually invoked, seen through call_hubspot in the routed arm."""
    names = []
    for c in summary["tool_calls"]:
        name = str(c.get("name", ""))
        if not name.startswith(MCP_PREFIX):
            continue
        short = name[len(MCP_PREFIX):]
        if short == "call_hubspot" and isinstance(c.get("input"), dict):
            names.append(str(c["input"].get("tool")))
        else:
            names.append(short)
    return names


def _write_outcomes(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Every write envelope the model saw: ``{tool, action_id, status}``.

    ``status`` is ``preview`` (gate asked), ``applied`` (AUTO tier), or
    ``validation_failed`` / ``error`` when the tool refused before the gate.
    """
    out: list[dict[str, Any]] = []
    for c in summary["tool_calls"]:
        name = str(c.get("name", "")).split("__")[-1]
        inp = c.get("input") or {}
        tool = inp.get("tool") if name == "call_hubspot" and isinstance(inp, dict) else name
        text = c.get("result_text") or ""
        try:
            data = json.loads(text)
        except ValueError:
            if "validation_failed" in text or c.get("is_error"):
                out.append({"tool": tool, "action_id": None, "status": "error", "text": text[:200]})
            continue
        for d in _walk(data):
            if not isinstance(d, dict):
                continue
            if d.get("action_id") and d.get("status") in ("preview", "applied"):
                out.append({"tool": d.get("tool") or tool, "action_id": str(d["action_id"]), "status": d["status"]})
            elif d.get("rejected") and d.get("for"):
                # The gate asked the host for an inline confirmation (MRTR) and a
                # headless client declined it, so the server rejected the preview.
                # The write reached the gate; nothing was applied.
                out.append({"tool": d["for"], "action_id": str(d["rejected"]), "status": "declined"})
            elif d.get("error") == "validation_failed":
                out.append({"tool": d.get("tool") or tool, "action_id": None, "status": "validation_failed",
                            "errors": d.get("validation_errors")})
    return out


def _skill_events(summary: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(primary skills the lookups returned, skills the model loaded), either surface."""
    picked: list[str] = []
    loaded: list[str] = []
    for c in summary["tool_calls"]:
        name = str(c.get("name", "")).split("__")[-1]
        inp = c.get("input") or {}
        if name in ("hubspot_find_skills", "find_capabilities"):
            try:
                data = json.loads(c.get("result_text") or "")
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("primary_skill"):
                picked.append(str(data["primary_skill"]))
        elif name in ("hubspot_load_skill", "load_skill") and isinstance(inp, dict) and inp.get("name"):
            loaded.append(str(inp["name"]))
    return picked, loaded


def _action_ids(summary: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(preview action_ids, applied action_ids) parsed from tool results, in order."""
    outcomes = _write_outcomes(summary)
    previews = list(dict.fromkeys(o["action_id"] for o in outcomes if o["status"] == "preview" and o["action_id"]))
    applied = list(dict.fromkeys(o["action_id"] for o in outcomes if o["status"] == "applied" and o["action_id"]))
    return previews, applied


def _walk(data: Any):
    yield data
    if isinstance(data, dict):
        for v in data.values():
            yield from _walk(v)
    elif isinstance(data, list):
        for v in data:
            yield from _walk(v)


def check(task: dict[str, Any], summary: dict[str, Any], state: dict[str, Any], arm: str = "full") -> dict[str, Any]:
    spec = task["check"]
    names = _server_side_names(summary)
    text = (summary.get("final_text") or "").lower()
    results: dict[str, bool] = {}
    if arm == "control" and not spec.get("no_hubspot_tools"):
        # No MCP, so tool-dependent tasks cannot complete by construction. The
        # control arm exists for its token and time baseline, not a verdict.
        return {"passed": None, "checks": {}, "server_side_tools": names}
    if "tool_any" in spec:
        results["tool_any"] = any(n in names for n in spec["tool_any"])
    if "tool_all" in spec:
        results["tool_all"] = all(n in names for n in spec["tool_all"])
    if "text_any" in spec:
        results["text_any"] = any(s.lower() in text for s in spec["text_any"])
    if spec.get("no_hubspot_tools"):
        results["no_hubspot_tools"] = not any(n not in ROUTING_TOOLS for n in names)
    if "skill_picked" in spec or "skill_loaded" in spec:
        picked, loaded = _skill_events(summary)
        if "skill_picked" in spec:
            results["skill_picked"] = spec["skill_picked"] in picked
        if "skill_loaded" in spec:
            results["skill_loaded"] = spec["skill_loaded"] in loaded
    outcomes = _write_outcomes(summary)
    if "write_gate" in spec:
        # The write reached the safety gate for the expected tool: a preview the
        # model left pending, or an AUTO-tier apply. A validation refusal is not
        # completion, but it is recorded so the report can say why.
        wanted = spec["write_gate"] if isinstance(spec["write_gate"], list) else [spec["write_gate"]]
        results["write_gate"] = all(
            any(o["tool"] == w and o["status"] in ("preview", "applied", "declined") for o in outcomes) for w in wanted
        )
    if "write_gate_any" in spec:
        # Any one of these tools reaching the gate counts: the skill decides
        # how to record a call (call engagement or note), not the check.
        results["write_gate_any"] = any(
            o["tool"] in spec["write_gate_any"] and o["status"] in ("preview", "applied", "declined") for o in outcomes
        )
    if "not_applied" in spec:
        wanted = spec["not_applied"] if isinstance(spec["not_applied"], list) else [spec["not_applied"]]
        results["not_applied"] = not any(o["tool"] in wanted and o["status"] == "applied" for o in outcomes)
    results["no_error_result"] = not summary.get("result_is_error")
    return {
        "passed": all(results.values()),
        "checks": results,
        "server_side_tools": names,
        "write_outcomes": outcomes,
    }


def portal_state(surface: Surface, since_iso: str) -> dict[str, Any]:
    pending, _ = surface.call_tool("hubspot_list_pending_writes")
    audit, _ = surface.call_tool("hubspot_list_recent_audit", {"limit": 30})
    entries = audit.get("audit", []) if isinstance(audit, dict) else []
    recent = [a for a in entries if str(a.get("timestamp", "")) >= since_iso]
    return {"pending": (pending or {}).get("pending", []) if isinstance(pending, dict) else [], "audit": recent}


def cleanup(surface: Surface, summary: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    done: dict[str, list[str]] = {"rejected": [], "undone": [], "failed": []}
    for action_id in state.get("pending", []):
        try:
            surface.call_tool("hubspot_reject_write", {"action_id": action_id})
            done["rejected"].append(action_id)
        except RuntimeError as exc:
            done["failed"].append(f"reject {action_id}: {exc}")
    _, applied = _action_ids(summary)
    pending = set(state.get("pending", []))
    # Newest first: a property update on a record the same session created must
    # be undone before the create is undone, or the update's undo finds nothing.
    for action_id in [a for a in reversed(applied) if a not in pending]:
        try:
            _, is_error = surface.call_tool("hubspot_undo_write", {"action_id": action_id})
            (done["failed"] if is_error else done["undone"]).append(action_id)
        except RuntimeError as exc:
            done["failed"].append(f"undo {action_id}: {exc}")
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--secret-env", default="HUBSPOT_MCP_SERVER_SECRET")
    ap.add_argument("--tasks", default=str(HERE / "tasks.jsonl"))
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--model", default=None, help="Claude Code model alias; default is the CLI default")
    ap.add_argument("--only", default=None, help="comma-separated task ids")
    ap.add_argument("--test-contact", default=os.getenv("BENCH_TEST_CONTACT", "Test Contact"))
    ap.add_argument("--test-deal", default=os.getenv("BENCH_TEST_DEAL", "Test Deal"))
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry-run", action="store_true", help="2 tasks x 1 rep")
    ap.add_argument("--no-cleanup", action="store_true")
    args = ap.parse_args()

    secret = os.getenv(args.secret_env) or None
    arms = [a for a in args.arms.split(",") if a]
    subs = {
        "TEST_CONTACT": args.test_contact,
        "TEST_CONTACT_FIRST": args.test_contact.split()[0],
        "TEST_DEAL": args.test_deal,
    }
    tasks = load_tasks(Path(args.tasks), subs)
    if args.only:
        wanted = set(args.only.split(","))
        tasks = [t for t in tasks if t["id"] in wanted]
    reps = args.reps
    if args.dry_run:
        tasks, reps = tasks[:2], 1

    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    out = Path(args.out or HERE / "runs" / stamp)
    out.mkdir(parents=True, exist_ok=True)
    full = Surface(args.server.rstrip("/") + "/mcp", secret)
    full.initialize()

    static: dict[str, Any] = {}
    for arm in ("full", "routed"):
        s = Surface(args.server.rstrip("/") + ("/mcp" if arm == "full" else "/mcp/routed"), secret)
        s.initialize()
        static[arm] = measure_tools_list(s.list_tools())
    (out / "tools_list.json").write_text(json.dumps(static, indent=2))
    print("tools/list:", json.dumps(static))

    meta = {
        "started": stamp, "server": args.server, "arms": arms, "reps": reps, "model": args.model,
        "tasks": [t["id"] for t in tasks], "claude_version": subprocess.run(  # noqa: S603
            [shutil.which("claude") or "claude", "--version"], capture_output=True, text=True
        ).stdout.strip(),
        "placeholders": {"TEST_CONTACT": "{TEST_CONTACT}", "TEST_DEAL": "{TEST_DEAL}"},
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2))

    sessions_path = out / "sessions.jsonl"
    total = len(tasks) * reps * len(arms)
    n = 0
    for rep in range(1, reps + 1):
        for task in tasks:
            rotated = arms[rep % len(arms):] + arms[: rep % len(arms)]
            for arm in rotated:
                n += 1
                since = datetime.now(UTC).isoformat()
                cwd = Path(tempfile.mkdtemp(prefix="hs-bench-"))
                label = f"[{n}/{total}] rep {rep} {task['id']} {arm}"
                print(label, "...", end=" ", flush=True)
                try:
                    raw = run_claude(task["prompt"] + SUFFIX, mcp_config(arm, args.server, secret), args.model, cwd, args.timeout)
                except subprocess.TimeoutExpired:
                    raw = {"events": [], "stderr": "timeout", "returncode": -1, "wall_ms": args.timeout * 1000}
                finally:
                    shutil.rmtree(cwd, ignore_errors=True)
                summary = summarize(raw["events"])
                state = {"pending": [], "audit": []}
                cleaned: dict[str, Any] = {}
                if arm != "control":
                    try:
                        state = portal_state(full, since)
                        if not args.no_cleanup:
                            cleaned = cleanup(full, summary, state)
                    except RuntimeError as exc:
                        state = {"pending": [], "audit": [], "error": str(exc)}
                verdict = check(task, summary, state, arm)
                picked, loaded = _skill_events(summary)
                record = {
                    "n": n, "rep": rep, "task_id": task["id"], "kind": task["kind"], "arm": arm, "model": args.model,
                    "skills_picked": picked, "skills_loaded": loaded,
                    "since": since, "wall_ms": raw["wall_ms"], "returncode": raw["returncode"],
                    "summary": summary, "state": state, "cleanup": cleaned, "verdict": verdict, "stderr": raw["stderr"],
                }
                with sessions_path.open("a") as fh:
                    fh.write(json.dumps(record) + "\n")
                (out / f"events-{n:03d}-{task['id']}-{arm}-r{rep}.jsonl").write_text(
                    "\n".join(json.dumps(e) for e in raw["events"])
                )
                t = summary["totals"]
                print(
                    f"ok={verdict['passed']} turns={summary['num_turns']} ctx1={summary['first_call_context_tokens']} "
                    f"ctx_all={t['context_tokens_all_calls']} out={t['output_tokens']} cost={summary['total_cost_usd']} "
                    f"ms={summary['duration_ms']} tools={len(summary['hubspot_tool_names'])}"
                )
    print("done ->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
