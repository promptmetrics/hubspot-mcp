"""Report for a multi-turn bench run (bench/multiturn.py).

Table A: per arm, per turn, medians over the multi-turn sessions: lookup and load calls, HubSpot
calls, API calls, context tokens with the cache read / write split, cost and seconds.
Table B: amortisation, one three-turn session against the sum of the three matching single-turn
sessions. Table C: checks per turn and every re-lookup.

Usage: .venv/bin/python bench/multiturn_report.py bench/runs/<stamp>-multiturn
"""

from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report import PRICES  # noqa: E402


def med(values: list[float]) -> float:
    return st.median(values) if values else 0.0


def fmt(v: float, kind: str = "n") -> str:
    if kind == "$":
        return f"${v:.3f}"
    if kind == "s":
        return f"{v / 1000:.0f}"
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:g}"


def load(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    rows = [json.loads(line) for line in (run_dir / "sessions.jsonl").read_text().splitlines() if line.strip()]
    meta = json.loads((run_dir / "meta.json").read_text()) if (run_dir / "meta.json").exists() else {}
    tools = json.loads((run_dir / "tools_list.json").read_text()) if (run_dir / "tools_list.json").exists() else {}
    return rows, meta, tools


def observed_model(run_dir: Path) -> str | None:
    for path in sorted(run_dir.glob("events-*.jsonl"))[:3]:
        for line in path.read_text().splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            model = (ev.get("message") or {}).get("model")
            if model:
                return str(model)
    return None


def build(run_dir: Path) -> str:
    rows, meta, tools = load(run_dir)
    arms = meta.get("arms") or sorted({r["arm"] for r in rows})
    multi = [r for r in rows if r["mode"] == "multi"]
    single = [r for r in rows if r["mode"] == "single"]
    model = observed_model(run_dir)
    turns_each = len(multi[0]["turns"]) if multi else 0
    out: list[str] = [f"# Multi-turn bench report: {run_dir.name}", ""]
    out.append(f"- Sessions: {len(multi)} multi-turn ({turns_each} turns each) and {len(single)} single-turn; arms {', '.join(arms)}; scenarios {', '.join(meta.get('scenarios', []))}")
    out.append(f"- Model: {meta.get('model') or 'CLI default'} (observed: {model}); {meta.get('claude_version', '')}")
    out.append("- Cost basis: Anthropic list API prices for the model that ran, as Claude Code reports them per session (`total_cost_usd`, cumulative over a resumed session; per-turn cost is the difference). The sessions ran on a Claude subscription, so the dollar figures are API-equivalent, not money spent."
               + (f" Known price tables: {', '.join(PRICES)}." if PRICES else ""))
    if tools:
        out.append("- tools/list: " + "; ".join(f"{a}: {t.get('tools')} tools, {t.get('bytes'):,} bytes" + (f", router {t['router']}" if t.get("router") else "") for a, t in tools.items()))
    out.append("")

    out += ["## A. Per turn, medians over multi-turn sessions", ""]
    out.append("| Arm | Turn | Sessions | Lookup calls | Load calls | HubSpot calls | API calls | Context tokens | Cache read | Cache write | Output tokens | Cost | Seconds | Checks passed |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for arm in arms:
        ss = [r for r in multi if r["arm"] == arm]
        if not ss:
            continue
        nturns = max(len(r["turns"]) for r in ss)
        for ti in range(nturns):
            ts = [r["turns"][ti] for r in ss if len(r["turns"]) > ti]

            def g(key: str, ts: list[dict[str, Any]] = ts) -> list[float]:
                return [t[key] for t in ts]

            out.append(
                f"| {arm} | {ti + 1} | {len(ts)} | {fmt(med(g('lookup_calls')))} | {fmt(med(g('load_calls')))} | {fmt(med(g('hubspot_calls')))} | "
                f"{fmt(med(g('api_calls')))} | {fmt(med(g('context_tokens_all_calls')))} | {fmt(med(g('cache_read')))} | {fmt(med(g('cache_write')))} | "
                f"{fmt(med(g('output_tokens')))} | {fmt(med(g('cost_delta')), '$')} | {fmt(med(g('duration_ms')), 's')} | "
                f"{sum(1 for t in ts if t['verdict']['passed'])}/{len(ts)} |"
            )
    out.append("")
    out.append("Lookup = find_capabilities or hubspot_find_skills; load = load_skill or hubspot_load_skill. A lookup or load count of 0 on turns 2 and 3 means the model reused what turn 1 put in its context.")
    out.append("")

    out += ["## B. One three-turn session against three single-turn sessions", ""]
    out.append("| Arm | Scenario | Multi: cost | Singles: cost (sum of medians) | Multi: seconds | Singles: seconds | Multi: API calls | Singles: API calls | Multi: context tokens | Singles: context tokens | Multi: cache write | Singles: cache write | Multi: lookups+loads | Singles: lookups+loads |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for arm in arms:
        for sc in meta.get("scenarios", sorted({r["scenario"] for r in rows})):
            ms = [r for r in multi if r["arm"] == arm and r["scenario"] == sc]
            sg = [r for r in single if r["arm"] == arm and r["scenario"] == sc]
            if not ms or not sg:
                continue
            by_turn: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for r in sg:
                by_turn[r["turns"][0]["prompt_id"]].append(r["turns"][0])

            def ssum(key: str, by_turn: dict[str, list[dict[str, Any]]] = by_turn) -> float:
                return sum(med([t[key] for t in ts]) for ts in by_turn.values())

            def msess(key: str, ms: list[dict[str, Any]] = ms) -> float:
                return med([r["session"][key] for r in ms])

            out.append(
                f"| {arm} | {sc} | {fmt(msess('cost'), '$')} | {fmt(ssum('cost_delta'), '$')} | {fmt(msess('duration_ms'), 's')} | {fmt(ssum('duration_ms'), 's')} | "
                f"{fmt(msess('api_calls'))} | {fmt(ssum('api_calls'))} | {fmt(msess('context_tokens_all_calls'))} | {fmt(ssum('context_tokens_all_calls'))} | "
                f"{fmt(msess('cache_write'))} | {fmt(ssum('cache_write'))} | {fmt(msess('lookup_calls') + msess('load_calls'))} | {fmt(ssum('lookup_calls') + ssum('load_calls'))} |"
            )
    out.append("")
    out.append("Singles use the explicit form of turns 2 and 3 (the contact named instead of a pronoun), so each stands alone. Medians per prompt, then summed.")
    out.append("")

    out += ["## C. Checks and re-lookups", ""]
    out.append("| Arm | Mode | Sessions | All turns passed | Turn checks passed | Re-lookups (turn 2+ lookup or load calls) | Tool errors |")
    out.append("|---|---|---|---|---|---|---|")
    relooks: list[str] = []
    for arm in arms:
        for mode, pool in (("multi", multi), ("single", single)):
            ss = [r for r in pool if r["arm"] == arm]
            if not ss:
                continue
            turns = [t for r in ss for t in r["turns"]]
            re = [(r["scenario"], r["rep"], t["turn"], t["lookup_calls"], t["load_calls"]) for r in ss for t in r["turns"] if t["turn"] > 1 and (t["lookup_calls"] or t["load_calls"])]
            relooks += [f"- {arm} {sc} rep {rep} turn {tn}: {lk} lookup, {ld} load" for sc, rep, tn, lk, ld in re]
            out.append(
                f"| {arm} | {mode} | {len(ss)} | {sum(1 for r in ss if r['session']['passed'])}/{len(ss)} | "
                f"{sum(1 for t in turns if t['verdict']['passed'])}/{len(turns)} | {len(re)} | {sum(t['tool_errors'] for t in turns)} |"
            )
    out.append("")
    out += relooks or ["No re-lookups: every multi-turn session called the lookup and the loader on turn 1 only."]
    out.append("")

    fails = [(r["arm"], r["mode"], r["scenario"], r["rep"], t["turn"], [k for k, v in t["verdict"]["checks"].items() if not v]) for r in rows for t in r["turns"] if not t["verdict"]["passed"]]
    if fails:
        out += ["## Failed turn checks", ""]
        out += [f"- {a} {m} {sc} rep {rep} turn {tn}: {', '.join(ks)}" for a, m, sc, rep, tn, ks in fails]
        out.append("")
    return "\n".join(out)


def main() -> int:
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else sorted((Path(__file__).resolve().parent / "runs").glob("*-multiturn"))[-1]
    text = build(run_dir)
    (run_dir / "report.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
