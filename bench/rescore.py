#!/usr/bin/env python3
"""Re-judge a finished run against the current bench/tasks.jsonl checks.

    .venv/bin/python bench/rescore.py bench/runs/<timestamp> [--test-contact NAME --test-deal NAME]

Verdicts derive from each session's stored summary and portal state, so a check
can be corrected after the fact without re-running sessions. Rewrites sessions.jsonl
in place (a .bak copy is kept) and prints what changed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import HERE, check, load_tasks  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--test-contact", default=os.getenv("BENCH_TEST_CONTACT", "Test Contact"))
    ap.add_argument("--test-deal", default=os.getenv("BENCH_TEST_DEAL", "Test Deal"))
    args = ap.parse_args()
    subs = {"TEST_CONTACT": args.test_contact, "TEST_CONTACT_FIRST": args.test_contact.split()[0], "TEST_DEAL": args.test_deal}
    tasks = {t["id"]: t for t in load_tasks(HERE / "tasks.jsonl", subs)}
    path = Path(args.run_dir) / "sessions.jsonl"
    shutil.copy(path, path.with_suffix(".jsonl.bak"))
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    changed = 0
    for s in rows:
        task = tasks.get(s["task_id"])
        if not task:
            continue
        before = s["verdict"]["passed"]
        s["verdict"] = check(task, s["summary"], s.get("state", {}), s["arm"])
        s["kind"] = task["kind"]
        if s["verdict"]["passed"] != before:
            changed += 1
            print(f"rep {s['rep']} {s['task_id']} {s['arm']}: {before} -> {s['verdict']['passed']} {s['verdict']['checks']}")
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"rescored {len(rows)} sessions, {changed} verdicts changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
