#!/usr/bin/env python3
"""Validation kit for the job-2 LLM judge: blind human packet, judge run, Cohen's kappa.

    python bench/judge_kit.py sample <run dir>... --truth <truth.json> --n 20 --seed 7 --out <dir>
    python bench/judge_kit.py judge  <dir> --truth <truth.json>
    python bench/judge_kit.py kappa  <dir>

`sample` picks job2 sessions without upstream or harness failure, assigns blind ids J01.., and writes
  packet.md                 facts sheet once, then each answer text (emails -> <email>, owner names -> "the rep")
  grader_A.csv, grader_B.csv  item, has_invented (yes/no), invented_count, notes
  key.json                  blind id -> run dir, session n (never shown to graders)
  sample.json               seed, counts, rubric version
Give graders packet.md and their own csv only. `judge` writes judge.csv in the same format (its notes quote the
judge's claims: keep it from the graders too). `kappa` compares A, B, the judge and the A=B consensus.

Pass rule (PLAN): kappa of the judge against the human consensus >= 0.8. Kappa is undefined, and reported as such with
the reason, when both raters used a single class or there are no paired items. `kappa` also checks the three label
files against key.json: a blank, missing, unknown or duplicated item makes the verdict INVALID (it names the files),
and a kappa of 0.8 or more over fewer than MIN_CONSENSUS consensus items is INSUFFICIENT, not PASS.

The packet carries truth-file values (names, phones, deal terms) by design. `sample` refuses an --out that is inside
this repo but outside bench/runs/ (gitignored), and refuses to write a packet in which a capitalised word follows
"the rep" (an owner surname that the name tokens did not cover). Pass the owner's display name with --mask-name or
JUDGE_MASK_NAMES; without one, only the name tokens in the truth file's owner emails are masked.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import judge as judge_mod  # noqa: E402
from grade import load_jsonl, superseded_flags  # noqa: E402

CSV_FIELDS = ["item", "has_invented", "invented_count", "notes"]
PASS_KAPPA = 0.8
MIN_CONSENSUS = 10
REPO = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO / "bench" / "runs"
_REP_SURNAME_RE = re.compile(r"\bthe rep[ \t]+[A-Z][a-z]{2,}")
TASK = "job2"
EXCLUDED_FAILURES = ("upstream", "harness")


# --- sampling ------------------------------------------------------------------------------------------------


def eligible_sessions(run_dir: Path) -> list[dict[str, Any]]:
    """Job2 sessions of one run dir that produced text and did not fail upstream or in the harness."""
    records = load_jsonl(run_dir / "sessions.jsonl")
    out = []
    for rec, superseded in zip(records, superseded_flags(records), strict=True):
        text = rec.get("result_text") or (rec.get("summary") or {}).get("final_text") or ""
        if rec.get("task_id") == TASK and not superseded and rec.get("failure_class") not in EXCLUDED_FAILURES \
                and text.strip():
            out.append({"run_dir": str(run_dir.resolve()), "n": rec["n"], "text": text})
    return sorted(out, key=lambda c: (c["run_dir"], c["n"]))


def pick_sample(candidates: list[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """n candidates at random (all when fewer), in a seed-determined order that doubles as the blind-id order."""
    pool = sorted(candidates, key=lambda c: (c["run_dir"], c["n"]))
    return random.Random(seed).sample(pool, min(n, len(pool)))  # noqa: S311 - a reproducible sample, not a secret


def blind_id(i: int) -> str:
    return f"J{i + 1:02d}"


def packet_markdown(items: list[tuple[str, str]], facts: str, names: list[str]) -> str:
    parts = [
        "# Job 2 invented-fact review",
        "",
        f"{len(items)} answers to the prompt \"Prep me for my call with Jane Doe at Acme tomorrow.\" For each item, "
        "decide whether the answer contains at least one invented fact and count them. Fill in your own grader file "
        "only: item, has_invented (yes/no), invented_count, notes. Grade each item on its own, and do not compare "
        "notes with the other grader until both files are done.",
        "",
        "## Rules",
        "",
        judge_mod.RUBRIC_CORE.strip(),
        "",
        "## Facts sheet",
        "",
        "```text",
        facts,
        "```",
    ]
    for bid, text in items:
        parts += ["", "---", "", f"## {bid}", "", "````text", judge_mod.clean_text(text, names).strip(), "````"]
    return "\n".join(parts) + "\n"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def out_dir_problem(out: Path) -> str | None:
    resolved = out.resolve()
    if resolved.is_relative_to(REPO) and not resolved.is_relative_to(RUNS_DIR):
        return f"{out} is inside the repo but outside bench/runs/ (gitignored); the packet holds answer-key values"
    return None


def warn_if_no_mask_name(args: argparse.Namespace) -> None:
    if not args.mask_name and not judge_mod.env_mask_names():
        print("warning: no --mask-name and no JUDGE_MASK_NAMES: only the name tokens in the truth file's owner emails are "
              "masked, so an owner surname or display name can survive", file=sys.stderr)


def packet_problems(items: list[tuple[str, str]], names: list[str]) -> list[str]:
    problems = []
    for bid, text in items:
        cleaned = judge_mod.clean_text(text, names)
        if judge_mod.EMAIL_RE.search(cleaned):
            problems.append(f"{bid}: an email address")
        if _REP_SURNAME_RE.search(cleaned):
            problems.append(f"{bid}: a capitalised word follows 'the rep' (an owner name the masks did not cover)")
    return problems


def cmd_sample(args: argparse.Namespace) -> int:
    if problem := out_dir_problem(args.out):
        print(f"sample: refusing --out: {problem}", file=sys.stderr)
        return 2
    warn_if_no_mask_name(args)
    truth = json.loads(args.truth.read_text(encoding="utf-8"))
    names = [*args.mask_name, *judge_mod.env_mask_names(), *judge_mod.mask_names_from_truth(truth)]
    candidates = [c for d in args.run_dirs for c in eligible_sessions(d)]
    if len(candidates) < args.n:
        print(f"only {len(candidates)} eligible job2 sessions, sampling all of them", file=sys.stderr)
    picked = pick_sample(candidates, args.n, args.seed)
    ids = [blind_id(i) for i in range(len(picked))]
    if problems := packet_problems([(b, c["text"]) for b, c in zip(ids, picked, strict=True)], names):
        print(f"sample: refusing to write the packet: {'; '.join(problems)}. Pass --mask-name with the owner's full "
              "display name.", file=sys.stderr)
        return 3

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "packet.md").write_text(
        packet_markdown([(b, c["text"]) for b, c in zip(ids, picked, strict=True)], judge_mod.facts_sheet(truth), names),
        encoding="utf-8")
    for grader in ("A", "B"):
        write_csv(args.out / f"grader_{grader}.csv", [{"item": b, "has_invented": "", "invented_count": "", "notes": ""} for b in ids])
    key = {b: {"run_dir": c["run_dir"], "n": c["n"]} for b, c in zip(ids, picked, strict=True)}
    (args.out / "key.json").write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")
    (args.out / "sample.json").write_text(json.dumps({
        "seed": args.seed, "n_requested": args.n, "n_sampled": len(picked),
        "run_dirs": sorted({str(d.resolve()) for d in args.run_dirs}), "rubric_version": judge_mod.JUDGE_RUBRIC_VERSION,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"sampled {len(picked)} of {len(candidates)} eligible sessions into {args.out}", file=sys.stderr)
    return 0


# --- judge ---------------------------------------------------------------------------------------------------


def judge_row(item: str, result: dict[str, Any], names: list[str]) -> dict[str, Any]:
    if "error" in result:
        return {"item": item, "has_invented": "", "invented_count": "", "notes": f"ERROR: {result['error']}"}
    notes = "; ".join(f"{i['claim']} [{i['why']}]" for i in result["invented"])
    return {"item": item, "has_invented": "yes" if result["has_invented"] else "no",
            "invented_count": result["invented_count"], "notes": judge_mod.clean_text(notes, names)}


def run_judge(directory: Path, truth: dict[str, Any], *, claude_fn: judge_mod.ClaudeFn | None = None,
              mask_names: list[str] | None = None) -> list[dict[str, Any]]:
    """Judge every sampled item; one call each, cached per run dir (the same cache grade.py --judge uses)."""
    key = json.loads((directory / "key.json").read_text(encoding="utf-8"))
    names = [*(mask_names or []), *judge_mod.env_mask_names()]
    all_names = [*names, *judge_mod.mask_names_from_truth(truth)]
    sessions: dict[str, dict[int, dict]] = {}
    judges: dict[str, Any] = {}
    rows = []
    for item, ref in sorted(key.items()):
        run_dir = ref["run_dir"]
        if run_dir not in sessions:
            sessions[run_dir] = {r["n"]: r for r in load_jsonl(Path(run_dir) / "sessions.jsonl")}
            judges[run_dir] = judge_mod.make_job2_judge(Path(run_dir) / "judge-cache.jsonl", claude_fn=claude_fn,
                                                        mask_names=names)
        rec = sessions[run_dir].get(ref["n"])
        if rec is None:
            rows.append(judge_row(item, {"error": "session_not_found"}, all_names))
            continue
        text = rec.get("result_text") or (rec.get("summary") or {}).get("final_text") or ""
        result = judges[run_dir](TASK, None, text, truth) or {"error": "judge_returned_nothing"}
        rows.append(judge_row(item, result, all_names))
    return rows


def cmd_judge(args: argparse.Namespace) -> int:
    warn_if_no_mask_name(args)
    truth = json.loads(args.truth.read_text(encoding="utf-8"))
    rows = run_judge(args.dir, truth, mask_names=args.mask_name)
    write_csv(args.dir / "judge.csv", rows)
    errors = sum(1 for r in rows if str(r["notes"]).startswith("ERROR:"))
    print(f"judged {len(rows) - errors} of {len(rows)} items into {args.dir / 'judge.csv'} ({errors} errors)", file=sys.stderr)
    return 0


# --- kappa ---------------------------------------------------------------------------------------------------

_YES, _NO = {"yes", "y", "true", "1"}, {"no", "n", "false", "0"}


def read_labels(path: Path) -> tuple[dict[str, bool | None], list[str], list[str]]:
    """item -> yes/no/None (blank), warnings about rows whose count contradicts their label, and repeated item ids
    (a repeated row overwrites the earlier one)."""
    labels: dict[str, bool | None] = {}
    warnings: list[str] = []
    repeated: list[str] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            item, raw = (row.get("item") or "").strip(), (row.get("has_invented") or "").strip().casefold()
            if not item:
                continue
            if item in labels:
                repeated.append(item)
            if raw in _YES:
                labels[item] = True
            elif raw in _NO:
                labels[item] = False
            elif not raw:
                labels[item] = None
            else:
                raise ValueError(f"{path.name} {item}: has_invented must be yes or no, got {raw!r}")
            count = (row.get("invented_count") or "").strip()
            if count.isdigit() and labels[item] is not None and (int(count) > 0) != labels[item]:
                warnings.append(f"{path.name} {item}: has_invented {raw} but invented_count {count}")
    return labels, warnings, repeated


def cohen_kappa(a: list[bool], b: list[bool]) -> dict[str, Any]:
    """Cohen's kappa for two raters on yes/no labels, with the 2x2 count table and raw agreement.
    kappa is None, with `undefined_reason`, when no pairs exist or both raters used one and the same class."""
    if len(a) != len(b):
        raise ValueError("raters must label the same items")
    n = len(a)
    table = {
        "yes/yes": sum(1 for x, y in zip(a, b, strict=True) if x and y),
        "yes/no": sum(1 for x, y in zip(a, b, strict=True) if x and not y),
        "no/yes": sum(1 for x, y in zip(a, b, strict=True) if not x and y),
        "no/no": sum(1 for x, y in zip(a, b, strict=True) if not x and not y),
    }
    out: dict[str, Any] = {"n": n, "table": table, "agreement": None, "kappa": None, "undefined_reason": None}
    if n == 0:
        out["undefined_reason"] = "no items labelled by both raters"
        return out
    # Exact fractions: a kappa of exactly 0.8 must not fall to 0.7999999999999999 and fail the pass rule.
    agreement = Fraction(table["yes/yes"] + table["no/no"], n)
    pa_yes, pb_yes = Fraction(table["yes/yes"] + table["yes/no"], n), Fraction(table["yes/yes"] + table["no/yes"], n)
    expected = pa_yes * pb_yes + (1 - pa_yes) * (1 - pb_yes)
    out["agreement"] = float(agreement)
    if expected == 1:
        out["undefined_reason"] = f"both raters used only one class ({'yes' if pa_yes == 1 else 'no'}): chance agreement is 1"
        return out
    out["kappa"] = float((agreement - expected) / (1 - expected))
    return out


def _paired(x: dict[str, bool | None], y: dict[str, bool | None]) -> tuple[list[bool], list[bool]]:
    items = [i for i in sorted(x) if i in y and x[i] is not None and y[i] is not None]
    return [x[i] for i in items], [y[i] for i in items]  # type: ignore[misc]


def label_problems(name: str, labels: dict[str, bool | None], repeated: list[str], ids: set[str]) -> list[str]:
    def listed(items: list[str]) -> str:
        return ", ".join(items[:5]) + (f" and {len(items) - 5} more" if len(items) > 5 else "")

    blank = sorted(i for i in ids if labels.get(i) is None)
    unknown = sorted(set(labels) - ids)
    out = []
    if blank:
        out.append(f"{name}: {len(ids) - len(blank)} of {len(ids)} sampled items labelled, blank or missing: {listed(blank)}")
    if unknown:
        out.append(f"{name}: items not in key.json: {listed(unknown)}")
    if repeated:
        out.append(f"{name}: repeated items: {listed(sorted(set(repeated)))}")
    return out


def kappa_report(directory: Path) -> dict[str, Any]:
    key_path = directory / "key.json"
    if not key_path.exists():
        raise ValueError(f"{key_path.name} missing: kappa checks the label files against the sampled ids")
    ids = set(json.loads(key_path.read_text(encoding="utf-8")))
    a, wa, ra = read_labels(directory / "grader_A.csv")
    b, wb, rb = read_labels(directory / "grader_B.csv")
    j, wj, rj = read_labels(directory / "judge.csv")
    problems = [p for name, labels, rep in (("grader_A.csv", a, ra), ("grader_B.csv", b, rb), ("judge.csv", j, rj))
                for p in label_problems(name, labels, rep, ids)]
    consensus = {i: a[i] for i in a if i in b and a[i] is not None and a[i] == b[i]}
    comparisons = {
        "A vs B": cohen_kappa(*_paired(a, b)),
        "A vs judge": cohen_kappa(*_paired(a, j)),
        "B vs judge": cohen_kappa(*_paired(b, j)),
        "judge vs consensus (A=B)": cohen_kappa(*_paired(j, consensus)),
    }
    main = comparisons["judge vs consensus (A=B)"]
    if problems:
        verdict = f"INVALID ({'; '.join(problems)}): the pass rule is not met"
    elif main["kappa"] is None:
        verdict = f"UNDEFINED ({main['undefined_reason']}): the pass rule is not met"
    elif main["kappa"] < PASS_KAPPA:
        verdict = "FAIL"
    elif len(consensus) < MIN_CONSENSUS:
        verdict = f"INSUFFICIENT (only {len(consensus)} consensus items, the floor is {MIN_CONSENSUS}): the pass rule is not met"
    else:
        verdict = "PASS"
    return {"comparisons": comparisons, "consensus_items": len(consensus), "items": len(ids),
            "pass_rule": f"judge vs consensus kappa >= {PASS_KAPPA}", "pass_verdict": verdict,
            "problems": problems, "warnings": [*wa, *wb, *wj]}


def format_kappa(report: dict[str, Any]) -> str:
    lines = [f"items sampled: {report['items']}, A=B consensus on {report['consensus_items']}", ""]
    for name, c in report["comparisons"].items():
        kappa = f"{c['kappa']:.3f}" if c["kappa"] is not None else f"undefined ({c['undefined_reason']})"
        agree = f"{c['agreement']:.3f}" if c["agreement"] is not None else "-"
        t = c["table"]
        lines.append(f"{name}: n={c['n']} agreement={agree} kappa={kappa}")
        lines.append(f"    first/second: yes/yes={t['yes/yes']} yes/no={t['yes/no']} no/yes={t['no/yes']} no/no={t['no/no']}")
    lines += ["", f"{report['pass_rule']}: {report['pass_verdict']}"]
    lines += [f"warning: {w}" for w in report["warnings"]]
    return "\n".join(lines)


def cmd_kappa(args: argparse.Namespace) -> int:
    try:
        report = kappa_report(args.dir)
    except (OSError, ValueError) as exc:
        print(f"kappa: {exc}", file=sys.stderr)
        return 2
    (args.dir / "kappa.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(format_kappa(report))
    return 0


# --- CLI -----------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="pick job2 sessions, write the blind packet, grader files and key")
    s.add_argument("run_dirs", nargs="+", type=Path)
    s.add_argument("--truth", type=Path, required=True)
    s.add_argument("--n", type=int, default=20)
    s.add_argument("--seed", type=int, required=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--mask-name", action="append", default=[], metavar="NAME", help="owner display name to mask (repeatable)")
    s.set_defaults(fn=cmd_sample)

    j = sub.add_parser("judge", help="run the judge on the sampled items and write judge.csv")
    j.add_argument("dir", type=Path)
    j.add_argument("--truth", type=Path, required=True)
    j.add_argument("--mask-name", action="append", default=[], metavar="NAME")
    j.set_defaults(fn=cmd_judge)

    k = sub.add_parser("kappa", help="Cohen's kappa for A, B, the judge and the consensus")
    k.add_argument("dir", type=Path)
    k.set_defaults(fn=cmd_kappa)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
