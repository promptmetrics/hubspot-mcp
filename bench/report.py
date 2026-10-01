#!/usr/bin/env python3
"""Aggregate a bench run into a Markdown report.

    .venv/bin/python bench/report.py bench/runs/<timestamp>

Reads sessions.jsonl and tools_list.json, writes report.md beside them and
prints it. Record names are placeholders by construction: the prompts carry
{TEST_CONTACT}/{TEST_DEAL} substitutions that never reach this file, and the
final answers are not reproduced here.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

ARMS = ("control", "full", "routed")


def med(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def fmt(v: Any, digits: int = 0) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.{digits}f}" if digits else f"{v:,.0f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def _rate(vals: list[Any]) -> str:
    scored = [v for v in vals if v is not None]
    return f"{sum(scored)}/{len(scored)}" if scored else "n/a"


def usd(v: float | None) -> str:
    return "-" if v is None else f"${v:.4f}"


def load(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    sessions = [json.loads(line) for line in (run_dir / "sessions.jsonl").read_text().splitlines() if line.strip()]
    tools = json.loads((run_dir / "tools_list.json").read_text()) if (run_dir / "tools_list.json").exists() else {}
    meta = json.loads((run_dir / "meta.json").read_text()) if (run_dir / "meta.json").exists() else {}
    return sessions, tools, meta


def metric(s: dict[str, Any], key: str) -> Any:
    sm = s["summary"]
    if key == "ctx_all":
        return sm["totals"]["context_tokens_all_calls"]
    if key == "ctx_first":
        return sm["first_call_context_tokens"]
    if key == "cache_read":
        return sm["totals"]["cache_read_input_tokens"]
    if key == "cache_create":
        return sm["totals"]["cache_creation_input_tokens"]
    if key == "output":
        return sm["totals"]["output_tokens"]
    if key == "tools":
        return len(sm["hubspot_tool_names"])
    if key == "tool_errors":
        return sm["hubspot_tool_errors"]
    if key == "routing_calls":
        from run import ROUTING_TOOLS

        return sum(1 for n in sm["hubspot_tool_names"] if n in ROUTING_TOOLS)
    if key == "passed":
        v = s["verdict"]["passed"]
        return None if v is None else (1 if v else 0)
    return sm.get(key)


def build(run_dir: Path) -> str:
    sessions, tools, meta = load(run_dir)
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_task_arm: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for s in sessions:
        by_arm[s["arm"]].append(s)
        by_task_arm[(s["task_id"], s["arm"])].append(s)
    arms = [a for a in ARMS if a in by_arm]
    tasks = sorted({s["task_id"] for s in sessions})

    lines = [f"# Bench report: {run_dir.name}", ""]
    lines += [
        f"- Sessions: {len(sessions)} ({len(tasks)} tasks x {meta.get('reps', '?')} reps x {len(arms)} arms)",
        f"- Model: {meta.get('model') or 'Claude Code default'} (observed: {', '.join(sorted({str((s['summary'].get('init') or {}).get('model')) for s in sessions}))}); Claude Code {meta.get('claude_version', '?')}",
        f"- Server: {meta.get('server', '?')}",
        "",
        "## Context the MCP adds (tools/list)",
        "",
        "| Surface | Tools | Bytes | ~tokens (chars/4) | First-call context tokens, median (exact, from sessions) |",
        "|---|---|---|---|---|",
    ]
    ctrl_first = med([metric(s, "ctx_first") for s in by_arm.get("control", [])])
    for arm in ("control", "full", "routed"):
        if arm not in by_arm:
            continue
        st = tools.get(arm, {})
        first = med([metric(s, "ctx_first") for s in by_arm[arm]])
        delta = f" (+{first - ctrl_first:,.0f} vs control)" if first is not None and ctrl_first is not None and arm != "control" else ""
        lines.append(
            f"| {arm} | {fmt(st.get('tools', 0 if arm == 'control' else None))} | {fmt(st.get('bytes'))} | "
            f"{fmt(st.get('approx_tokens_chars_div_4'))} | {fmt(first)}{delta} |"
        )
    if "full" in tools and "routed" in tools and tools["routed"]["bytes"]:
        lines.append(f"\nFull/routed tools/list ratio: {tools['full']['bytes'] / tools['routed']['bytes']:.1f}x by bytes.")

    lines += ["", "## Per arm, medians over all sessions", ""]
    cols = [
        ("ctx_all", "Context tokens, all calls"), ("cache_read", "Cache read"), ("cache_create", "Cache create"),
        ("output", "Output tokens"), ("total_cost_usd", "Cost"), ("duration_ms", "Duration ms"),
        ("num_turns", "Turns"), ("tools", "HubSpot tool calls"), ("routing_calls", "Routing calls"),
        ("tool_errors", "Tool errors"), ("passed", "Completion rate"),
    ]
    lines.append("| Arm | " + " | ".join(c[1] for c in cols) + " |")
    lines.append("|---|" + "---|" * len(cols))
    for arm in arms:
        cells = []
        for key, _ in cols:
            vals = [metric(s, key) for s in by_arm[arm]]
            if key == "total_cost_usd":
                cells.append(usd(med(vals)))
            elif key == "passed":
                scored = [v for v in vals if v is not None]
                cells.append(f"{sum(scored)}/{len(scored)}" if scored else "n/a")
            else:
                cells.append(fmt(med(vals)))
        lines.append(f"| {arm} | " + " | ".join(cells) + " |")

    lines += ["", "## Per task", ""]
    lines.append("| Task | Arm | Passed | Ctx first | Ctx all | Output | Cost | Duration ms | Turns | Tool calls | Server-side tools (rep 1) |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for task in tasks:
        for arm in arms:
            ss = by_task_arm.get((task, arm), [])
            if not ss:
                continue
            first = ss[0]["verdict"].get("server_side_tools", [])
            lines.append(
                f"| {task} | {arm} | {_rate([metric(s, 'passed') for s in ss])} | "
                f"{fmt(med([metric(s, 'ctx_first') for s in ss]))} | {fmt(med([metric(s, 'ctx_all') for s in ss]))} | "
                f"{fmt(med([metric(s, 'output') for s in ss]))} | {usd(med([metric(s, 'total_cost_usd') for s in ss]))} | "
                f"{fmt(med([metric(s, 'duration_ms') for s in ss]))} | {fmt(med([metric(s, 'num_turns') for s in ss]))} | "
                f"{fmt(med([metric(s, 'tools') for s in ss]))} | {', '.join(first)[:120]} |"
            )

    lines += ["", "## Routing decisions (routed arm)", ""]
    routed = by_arm.get("routed", [])
    decisions = []
    for s in routed:
        for c in s["summary"]["tool_calls"]:
            if str(c.get("name", "")).endswith("find_capabilities"):
                try:
                    d = json.loads(c.get("result_text") or "{}")
                except ValueError:
                    continue
                decisions.append((s["task_id"], d.get("router"), d.get("primary_charter"), d.get("routing_ms"), d.get("routing_cost_usd"), len(d.get("tools", []))))
    if decisions:
        lines.append("| Task | Router | Primary charter | Routing ms | Routing cost | Tools returned |")
        lines.append("|---|---|---|---|---|---|")
        for row in decisions:
            lines.append("| " + " | ".join(fmt(v) if not isinstance(v, float) else (usd(v) if v < 1 else fmt(v)) for v in row) + " |")
        jev = [d for d in decisions if d[1] == "jev"]
        lines.append(f"\nJev answered {len(jev)}/{len(decisions)} routes; median routing {fmt(med([d[3] for d in jev]))} ms.")
    else:
        lines.append("No find_capabilities calls recorded.")

    skill_rows = [s for s in sessions if s.get("kind", "").startswith("skill")]
    if skill_rows:
        lines += ["", "## Skills", ""]
        lines.append("| Task | Arm | Skill picked (reps) | Skill loaded (reps) | Passed | Ctx first | Ctx all | Cost | Duration ms | Turns |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for task in sorted({s["task_id"] for s in skill_rows}):
            for arm in arms:
                ss = [s for s in skill_rows if s["task_id"] == task and s["arm"] == arm]
                if not ss:
                    continue
                picked = sum(1 for s in ss if s["verdict"]["checks"].get("skill_picked"))
                loaded = sum(1 for s in ss if s["verdict"]["checks"].get("skill_loaded"))
                lines.append(
                    f"| {task} | {arm} | {picked}/{len(ss)} | {loaded}/{len(ss)} | {_rate([metric(s, 'passed') for s in ss])} | "
                    f"{fmt(med([metric(s, 'ctx_first') for s in ss]))} | {fmt(med([metric(s, 'ctx_all') for s in ss]))} | "
                    f"{usd(med([metric(s, 'total_cost_usd') for s in ss]))} | {fmt(med([metric(s, 'duration_ms') for s in ss]))} | {fmt(med([metric(s, 'num_turns') for s in ss]))} |"
                )
        lines.append("\nSkill picked = the lookup's primary skill matched the expected one; loaded = the model then loaded that skill.")

    lines += ["", "## Write gate outcomes", ""]
    rows = []
    for s in sessions:
        for o in s["verdict"].get("write_outcomes", []):
            rows.append((s["task_id"], s["arm"], s["rep"], o["tool"], o["status"]))
    if rows:
        lines.append("| Task | Arm | Rep | Tool | Outcome |")
        lines.append("|---|---|---|---|---|")
        for r in sorted(rows):
            lines.append("| " + " | ".join(str(x) for x in r) + " |")
        lines.append("\n`declined` = the gate asked for an inline confirmation and the headless host declined it; "
                     "`applied` = AUTO tier, undone by the runner afterwards.")
    else:
        lines.append("No write envelopes seen.")

    lines += ["", "## Failures and errors", ""]
    bad = [s for s in sessions if s["verdict"]["passed"] is False or s["returncode"] != 0]
    if not bad:
        lines.append("None.")
    for s in bad:
        failed = [k for k, v in s["verdict"]["checks"].items() if not v]
        lines.append(f"- rep {s['rep']} {s['task_id']} {s['arm']}: failed {failed}; rc={s['returncode']}; result={s['summary'].get('result_subtype')}")

    return "\n".join(lines) + "\n"


def main() -> int:
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else sorted((Path(__file__).parent / "runs").glob("*"))[-1]
    report = build(run_dir)
    (run_dir / "report.md").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
