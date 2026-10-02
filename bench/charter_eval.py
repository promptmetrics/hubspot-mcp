#!/usr/bin/env python3
"""Charter routing accuracy: the keyword router against Jev on the server's own corpus.

    .venv/bin/python bench/charter_eval.py --jev-server http://127.0.0.1:8000

tests/routing_corpus.yaml holds 49 requests with the correct charter(s). The keyword
router runs in-process (agent_routing.route_request). Jev runs through the live
server's find_capabilities, which carries the gateway key, and its primary_charter
is scored. A pick counts when the primary charter is one of the expected routes.
Writes bench/runs/charter-eval-<ts>.json.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
from mcp_client import Surface  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jev-server", default="http://127.0.0.1:8000")
    ap.add_argument("--corpus", default=str(HERE.parent / "tests" / "routing_corpus.yaml"))
    args = ap.parse_args()
    corpus = [c for c in yaml.safe_load(Path(args.corpus).read_text()) if c.get("mode") != "llm"]

    from hubspot_mcp.agent_routing import route_request

    surface = Surface(args.jev_server.rstrip("/") + "/mcp/routed")
    surface.initialize()
    rows = []
    for c in corpus:
        want = set(c["expected_routes"])
        kw = route_request(c["request"])
        kw_primary = kw[0] if kw else None
        started = time.perf_counter()
        data, err = surface.call_tool("find_capabilities", {"task": c["request"]})
        ms = int((time.perf_counter() - started) * 1000)
        jev_primary = data.get("primary_charter") if isinstance(data, dict) else None
        router = data.get("router") if isinstance(data, dict) else "error"
        rows.append({
            "request": c["request"], "expected": sorted(want),
            "keyword": kw_primary, "keyword_ok": kw_primary in want,
            "jev": jev_primary, "jev_ok": jev_primary in want, "jev_router": router,
            "jev_ms": data.get("routing_ms") if isinstance(data, dict) else None, "wall_ms": ms,
            "jev_cost": data.get("routing_cost_usd") if isinstance(data, dict) else None,
            "jev_tools_returned": len(data.get("tools", [])) if isinstance(data, dict) else None,
        })
        print(f"{'ok' if rows[-1]['jev_ok'] else 'MISS':4} jev={jev_primary!s:16} {'ok' if rows[-1]['keyword_ok'] else 'MISS':4} kw={kw_primary!s:16} want={sorted(want)} {c['request'][:50]!r}")

    n = len(rows)
    jev_live = [r for r in rows if r["jev_router"] == "jev"]
    print(f"\ncharters, {n} prompts: keyword {sum(r['keyword_ok'] for r in rows)}/{n} | jev {sum(r['jev_ok'] for r in rows)}/{n}"
          f" (jev answered {len(jev_live)}/{n}; median {statistics.median(r['jev_ms'] or 0 for r in rows):.0f} ms, "
          f"${statistics.mean(r['jev_cost'] or 0 for r in rows):.5f}/route, median {statistics.median(r['jev_tools_returned'] or 0 for r in rows):.0f} tools returned)")
    print("keyword-only misses:", [r["request"][:40] for r in rows if r["jev_ok"] and not r["keyword_ok"]])
    print("jev-only misses:", [r["request"][:40] for r in rows if r["keyword_ok"] and not r["jev_ok"]])
    out = HERE / "runs" / f"charter-eval-{datetime.now(UTC).strftime('%Y-%m-%dT%H-%M-%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1))
    print("saved", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
