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

# USD per million tokens, Anthropic list prices read from
# https://platform.claude.com/docs/en/about-claude/pricing on 2026-10-02.
# Claude Code writes the 1-hour cache, so cache writes are priced at the 1h rate.
PRICES = {
    "opus-5.5": {"input": 4.0, "cache_write_1h": 8.0, "cache_read": 0.20, "output": 20.0},
    "sonnet-5.5": {"input": 2.0, "cache_write_1h": 4.0, "cache_read": 0.20, "output": 10.0},
    "sonnet-5": {"input": 2.0, "cache_write_1h": 4.0, "cache_read": 0.20, "output": 10.0},
    "haiku-4.5": {"input": 1.0, "cache_write_1h": 2.0, "cache_read": 0.10, "output": 5.0},
}
PRICE_MODEL: str | None = None  # --price-model: reprice every session's tokens at this model's list prices


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
    if key == "total_cost_usd" and PRICE_MODEL:
        return reprice(sm["totals"], PRICE_MODEL)
    if key == "cost_observed":
        return sm.get("total_cost_usd")
    return sm.get(key)


def reprice(totals: dict[str, Any], model: str) -> float:
    p = PRICES[model]
    return (
        totals["input_tokens"] * p["input"]
        + totals["cache_creation_input_tokens"] * p["cache_write_1h"]
        + totals["cache_read_input_tokens"] * p["cache_read"]
        + totals["output_tokens"] * p["output"]
    ) / 1_000_000


def build(run_dir: Path) -> str:
    sessions, tools, meta = load(run_dir)
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_task_arm: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for s in sessions:
        by_arm[s["arm"]].append(s)
        by_task_arm[(s["task_id"], s["arm"])].append(s)
    arms = [a for a in ARMS if a in by_arm] + sorted(a for a in by_arm if a not in ARMS)
    tasks = sorted({s["task_id"] for s in sessions})

    lines = [f"# Bench report: {run_dir.name}", ""]
    observed = ", ".join(sorted({str((s["summary"].get("init") or {}).get("model")) for s in sessions}))
    basis = (
        f"- Cost basis: Anthropic list API prices for the model that ran ({observed}), as Claude Code reports them "
        "(`total_cost_usd`, costBasis list). The sessions ran on a Claude subscription, so the dollar figures are "
        "API-equivalent, not money spent."
    )
    if PRICE_MODEL:
        p = PRICES[PRICE_MODEL]
        basis += (
            f" **Every cost in this report is repriced at {PRICE_MODEL} list prices** (input ${p['input']}, 1h cache write "
            f"${p['cache_write_1h']}, cache read ${p['cache_read']}, output ${p['output']} per MTok, pricing page read 2026-10-02). "
            "Same tokens, different price list; the model's behaviour is unchanged."
        )
    lines += [
        f"- Sessions: {len(sessions)} ({len(tasks)} tasks x {meta.get('reps', '?')} reps x {len(arms)} arms)",
        basis,
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

    lines += ["", "## Routing decisions (routed arms)", ""]
    routed = [s for a in arms for s in by_arm[a] if any(str(c.get("name", "")).endswith("find_capabilities") for c in s["summary"]["tool_calls"])]
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
        for row in decisions[:40]:
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

    routed_arms = [a for a in arms if any(s.get("routing") for s in by_arm[a])]
    if routed_arms:
        lines += ["", "## Routing, per arm", ""]
        lines.append("| Arm | Router | Charter right | Skill right | Re-routes per task | Tools returned | Lookup result chars | Routing ms | Routing cost | Arg rejections | Tool errors |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for arm in routed_arms:
            ss = by_arm[arm]
            routes = [r for s in ss for r in s.get("routing", []) if r.get("router")]
            routers = sorted({r["router"] for r in routes})
            ch = [s["verdict"]["checks"].get("charter_picked") for s in ss if "charter_picked" in s["verdict"]["checks"]]
            sk = [s["verdict"]["checks"].get("skill_picked") for s in ss if "skill_picked" in s["verdict"]["checks"]]
            reroutes = med([max(0, len([r for r in s.get("routing", []) if r.get("tool") == "find_capabilities"]) - 1) for s in ss])
            rejections = sum(1 for s in ss for c in s["summary"]["tool_calls"] if c.get("is_error") and "Invalid args" in (c.get("result_text") or ""))
            errors = sum(s["summary"]["hubspot_tool_errors"] for s in ss)
            lines.append(
                f"| {arm} | {', '.join(routers)} | {_rate([1 if v else 0 for v in ch])} | {_rate([1 if v else 0 for v in sk])} | {fmt(reroutes)} | "
                f"{fmt(med([r['tools_returned'] for r in routes]))} | {fmt(med([r['result_chars'] for r in routes]))} | "
                f"{fmt(med([r['routing_ms'] or 0 for r in routes]))} | {usd(med([r['routing_cost_usd'] or 0 for r in routes]))} | {rejections} | {errors} |"
            )
        lines.append("\nCharter right / skill right = the first lookup's primary pick matched the task label. Re-routes = extra find_capabilities calls after the first. Arg rejections = proxy schema errors the model had to correct.")

    if len(arms) == 2 and all(any(s.get("routing") for s in by_arm[a]) for a in arms):
        a, b = arms
        lines += ["", f"## Paired per task: {a} vs {b}", ""]
        lines.append(f"| Task | Passed {a} / {b} | Ctx all {a} / {b} | Cost {a} / {b} | Duration s {a} / {b} | Turns {a} / {b} | Tool calls {a} / {b} | Picks {a} / {b} |")
        lines.append("|---|---|---|---|---|---|---|---|")
        wins = {"ctx": [0, 0], "cost": [0, 0], "dur": [0, 0]}
        for task in tasks:
            sa = [s for s in by_arm[a] if s["task_id"] == task]
            sb = [s for s in by_arm[b] if s["task_id"] == task]
            if not sa or not sb:
                continue
            def m(ss, key): return med([metric(s, key) for s in ss])
            def picks(ss):
                ch = [s["verdict"]["checks"].get("charter_picked") for s in ss if "charter_picked" in s["verdict"]["checks"]]
                sk = [s["verdict"]["checks"].get("skill_picked") for s in ss if "skill_picked" in s["verdict"]["checks"]]
                parts = []
                if ch:
                    parts.append(f"charter {sum(1 for v in ch if v)}/{len(ch)}")
                if sk:
                    parts.append(f"skill {sum(1 for v in sk if v)}/{len(sk)}")
                return ", ".join(parts) or "-"
            for key, name in (("ctx_all", "ctx"), ("total_cost_usd", "cost"), ("duration_ms", "dur")):
                va, vb = m(sa, key), m(sb, key)
                if va is not None and vb is not None and va != vb:
                    wins[name][0 if va < vb else 1] += 1
            lines.append(
                f"| {task} | {_rate([metric(s, 'passed') for s in sa])} / {_rate([metric(s, 'passed') for s in sb])} | "
                f"{fmt(m(sa, 'ctx_all'))} / {fmt(m(sb, 'ctx_all'))} | {usd(m(sa, 'total_cost_usd'))} / {usd(m(sb, 'total_cost_usd'))} | "
                f"{fmt((m(sa, 'duration_ms') or 0) / 1000)} / {fmt((m(sb, 'duration_ms') or 0) / 1000)} | {fmt(m(sa, 'num_turns'))} / {fmt(m(sb, 'num_turns'))} | "
                f"{fmt(m(sa, 'tools'))} / {fmt(m(sb, 'tools'))} | {picks(sa)} / {picks(sb)} |"
            )
        lines.append(f"\nTasks where {a} was cheaper on context: {wins['ctx'][0]} vs {wins['ctx'][1]}; on cost: {wins['cost'][0]} vs {wins['cost'][1]}; on time: {wins['dur'][0]} vs {wins['dur'][1]}.")

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
    import argparse

    ap = argparse.ArgumentParser(description="Aggregate a bench run into report.md")
    ap.add_argument("run_dir", nargs="?", default=None)
    ap.add_argument("--price-model", choices=sorted(PRICES), default=None,
                    help="reprice every session's tokens at this model's list prices (report-<model>.md)")
    args = ap.parse_args()
    global PRICE_MODEL
    PRICE_MODEL = args.price_model
    run_dir = Path(args.run_dir) if args.run_dir else sorted((Path(__file__).parent / "runs").glob("*"))[-1]
    report = build(run_dir)
    (run_dir / (f"report-{PRICE_MODEL}.md" if PRICE_MODEL else "report.md")).write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
