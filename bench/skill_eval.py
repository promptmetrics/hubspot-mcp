#!/usr/bin/env python3
"""Skill routing eval: Jev against the keyword fallback on 40 labelled prompts.

    .venv/bin/python bench/skill_eval.py            # needs AI_GATEWAY_API_KEY for the jev rows
    .venv/bin/python bench/skill_eval.py --routers keyword

Cases: bench/skill_cases.jsonl, one {id, tag, task, skills[], tools[]} per line; skills[0] is
the expected primary skill, [] means none. Tags: clear, boundary, multi, none. The gate used
on the Pipedrive proof of concept: Jev beats keyword by at least 15 points on the boundary cases.
Runs in-process against the router, no server needed. Writes bench/runs/skill-eval-<ts>.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from hubspot_mcp.jev_router import (  # noqa: E402
    JevUnavailable,
    build_questions,
    decide_from_answers,
    jev_evaluate,
    keyword_decision,
)
from hubspot_mcp.skills import load_skills, skill_names  # noqa: E402

HERE = Path(__file__).resolve().parent


def descriptions() -> dict[str, str]:
    return {n: load_skills()[n].description for n in skill_names()}


SERVER: str | None = None  # set by --via-server: route through hubspot_find_skills on a running server


async def route_jev(task: str) -> dict:
    if SERVER:
        return await asyncio.to_thread(route_via_server, task)
    started = time.perf_counter()
    body = await jev_evaluate(task, build_questions({}, {}, descriptions()))
    ms = int((time.perf_counter() - started) * 1000)
    d = decide_from_answers(body, {}, {}, ms, descriptions())
    return {"primary": d.primary_skill, "skills": [s.name for s in d.skills], "ms": ms, "cost": d.cost_usd}


def route_via_server(task: str) -> dict:
    """Same routing, but the server holds the gateway key. Keyword fallbacks count as errors."""
    sys.path.insert(0, str(HERE))
    from mcp_client import Surface

    surface = Surface(SERVER.rstrip("/") + "/mcp")
    surface.initialize()
    data, err = surface.call_tool("hubspot_find_skills", {"request_text": task})
    if err or not isinstance(data, dict):
        raise JevUnavailable(str(data)[:200])
    if data.get("router") != "jev":
        raise JevUnavailable(data.get("note") or "server routed without Jev")
    return {"primary": data.get("primary_skill"), "skills": [s["name"] for s in data.get("skills", [])],
            "ms": data.get("routing_ms", 0), "cost": data.get("routing_cost_usd")}


def route_keyword(task: str) -> dict:
    started = time.perf_counter()
    d = keyword_decision(task, None, 0, "", with_skills=True)
    return {"primary": d.primary_skill, "skills": [s.name for s in d.skills], "ms": int((time.perf_counter() - started) * 1000), "cost": 0.0}


def pr(got: list[str], want: list[str]) -> tuple[float, float]:
    g, w = set(got), set(want)
    if not g and not w:
        return 1.0, 1.0
    tp = len(g & w)
    return (tp / len(g) if g else 0.0), (tp / len(w) if w else 1.0)


async def run(router: str, cases: list[dict], concurrency: int) -> dict:
    sem = asyncio.Semaphore(concurrency)
    rows = []

    async def one(c):
        async with sem:
            try:
                r = await route_jev(c["task"]) if router == "jev" else route_keyword(c["task"])
                err = None
            except JevUnavailable as exc:
                r, err = {"primary": None, "skills": [], "ms": 0, "cost": None}, str(exc)
            want = c["skills"][0] if c["skills"] else None
            p, rc = pr(r["skills"], c["skills"])
            rows.append({**c, "router": router, "got": r["primary"], "got_skills": r["skills"], "top1": r["primary"] == want,
                         "precision": p, "recall": rc, "ms": r["ms"], "cost": r["cost"], "error": err})

    await asyncio.gather(*(one(c) for c in cases))
    rows.sort(key=lambda r: r["id"])
    tags = sorted({r["tag"] for r in rows})
    summary = {
        "router": router,
        "n": len(rows),
        "top1": sum(r["top1"] for r in rows) / len(rows),
        **{f"top1_{t}": f"{sum(r['top1'] for r in rows if r['tag'] == t)}/{sum(1 for r in rows if r['tag'] == t)}" for t in tags},
        "skill_precision": statistics.mean(r["precision"] for r in rows),
        "skill_recall": statistics.mean(r["recall"] for r in rows),
        "latency_p50_ms": statistics.median(r["ms"] for r in rows),
        "latency_p95_ms": sorted(r["ms"] for r in rows)[int(0.95 * (len(rows) - 1))],
        "cost_per_call_usd": statistics.mean(r["cost"] or 0 for r in rows),
        "errors": sum(1 for r in rows if r["error"]),
    }
    return {"summary": summary, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--routers", default="jev,keyword")
    ap.add_argument("--cases", default=str(HERE / "skill_cases.jsonl"))
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--via-server", default=None, help="route Jev rows through hubspot_find_skills on this server URL")
    args = ap.parse_args()
    global SERVER
    SERVER = args.via_server
    cases = [json.loads(line) for line in Path(args.cases).read_text().splitlines() if line.strip()]
    results = {r: asyncio.run(run(r, cases, args.concurrency)) for r in args.routers.split(",")}

    print(f"\n{'router':8} {'top-1':>6} {'clear':>7} {'boundary':>9} {'multi':>6} {'none':>5} {'prec':>5} {'rec':>5} {'p50ms':>6} {'p95ms':>6} {'$/call':>8} err")
    for r, res in results.items():
        s = res["summary"]
        print(f"{r:8} {s['top1']:6.0%} {s.get('top1_clear','-'):>7} {s.get('top1_boundary','-'):>9} {s.get('top1_multi','-'):>6} {s.get('top1_none','-'):>5} "
              f"{s['skill_precision']:5.0%} {s['skill_recall']:5.0%} {s['latency_p50_ms']:6.0f} {s['latency_p95_ms']:6.0f} {s['cost_per_call_usd']:8.5f} {s['errors']}")
    for r, res in results.items():
        misses = [x for x in res["rows"] if not x["top1"]]
        print(f"\n{r} misses ({len(misses)}):")
        for x in misses:
            print(f"  {x['id']} [{x['tag']}] want={x['skills'][0] if x['skills'] else None} got={x['got']} {x['task'][:70]!r}")
    if {"jev", "keyword"} <= set(results):
        def boundary(res):
            rows = [x for x in res["rows"] if x["tag"] == "boundary"]
            return sum(x["top1"] for x in rows) / len(rows)
        gap = (boundary(results["jev"]) - boundary(results["keyword"])) * 100
        print(f"\nGate: Jev minus keyword on boundary cases = {gap:+.0f} points ({'PASS' if gap >= 15 else 'FAIL'}, needs +15).")
    out = HERE / "runs" / f"skill-eval-{datetime.now(UTC).strftime('%Y-%m-%dT%H-%M-%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1))
    print("saved", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
