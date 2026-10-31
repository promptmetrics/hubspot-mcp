#!/usr/bin/env python3
"""Intent benchmark report and verdict.

    .venv/bin/python bench/intent_report.py bench/runs/<stamp>-intent --truth <truth.json>
        [--out docs/bench-2026-11-intent-report.md] [--mask-name NAME ...]

Reads <run dir>/sessions.jsonl and <run dir>/grades.jsonl (run bench/grade.py first; add --judge there and the
job 2 invented-fact rate appears) and writes the Markdown report: the verdict, per-job tables by arm x model
(correctness, consistency, tool calls, tokens, wall time, cost), write safety, failures by class, job 2 invented
facts when judged, and the C vs D comparison. Upstream and harness failures are reported separately and never
counted against an arm. The report holds aggregates only: no answer text, no record names. Emails become
`<email>` and the owner's display name becomes `the rep` in every string taken from the run, and the CLI refuses
to write a report that still contains an email or a --mask-name value.

VERDICT RULE (fixed before the full run; do not tune it afterwards)
- Baselines are arms A and B; D is the control and never a baseline. Evaluate each model separately; C wins a job only if it wins on both models.
- Correctness = mean graded score over the job's sessions (non-upstream, non-harness). "At least equal to the best baseline" = C_mean >= best_baseline_mean - 0.02.
- Consistency per job: job1 mean pairwise Jaccard of flagged sets; job4 mean over figures of max relative spread; others: fraction of sessions with exact=True. "Better" = strictly better than the best-correctness baseline's value by more than 0.02 (lower spread is better for job4).
- Efficiency: medians of total tokens, tool calls, wall time vs the best-correctness baseline; "better on 2 of 3" = at least 2 medians lower by >= 10%.
- C loses correctness on a job if C_mean < best_baseline_mean - 0.02 on either model.
- Hypothesis holds iff C wins >= 3 of the 5 jobs AND loses correctness on none. Off-script tasks are reported, not part of the verdict (PLAN: a C loss there is reported as the cost of rigidity).
END VERDICT RULE

Where the rule is silent (PLAN "Pre-registered success rule" and these choices fix them; they are also printed in the report):
- A win on one model = correctness at least equal to the best baseline AND (consistency better OR efficiency better on 2 of 3).
- Best-correctness baseline per model = the baseline arm with the higher mean on that job and model. An exact tie goes to the arm with the lower median total tokens, then to A.
- Total tokens = context_tokens_all_calls + output_tokens. Tool calls = every tool call the session made (the summary's tool_calls list: MCP, Bash, Skill and any other name), counted the same way for every arm. Wall time = the session's wall_ms. Medians cover the sessions in the correctness set that have the metric.
- job1 Jaccard runs over the flagged deal names of sessions that returned an answer block; reason codes are not part of the set. job4 relative spread of one figure = (max - min) / |mean| over the job's sessions that report it: 0 when all equal, 1 when the mean is 0 and the values differ.
- Consistency that cannot be computed (fewer than two sessions with the data) is never "better".
- Comparisons carry a 1e-9 tolerance so a difference of exactly 0.02 or exactly 10% is judged as written.
- A verdict job with no scored session for C, or for both baselines, on a model is "missing": the job is not won. If no job loses correctness and any job is missing, the outcome is INCOMPLETE, not HOLDS or REJECTED, unless wins plus missing jobs is below 3: then no outcome of the missing jobs can reach 3 wins and the outcome is REJECTED.
- Superseded upstream/harness attempts are dropped; upstream/harness sessions that no re-run replaced are excluded from every statistic.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grade  # noqa: E402
import judge  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE.parent / "docs" / "bench-2026-11-intent-report.md"

VERDICT_RULE = """\
- Baselines are arms A and B; D is the control and never a baseline. Evaluate each model separately; C wins a job only if it wins on both models.
- Correctness = mean graded score over the job's sessions (non-upstream, non-harness). "At least equal to the best baseline" = C_mean >= best_baseline_mean - 0.02.
- Consistency per job: job1 mean pairwise Jaccard of flagged sets; job4 mean over figures of max relative spread; others: fraction of sessions with exact=True. "Better" = strictly better than the best-correctness baseline's value by more than 0.02 (lower spread is better for job4).
- Efficiency: medians of total tokens, tool calls, wall time vs the best-correctness baseline; "better on 2 of 3" = at least 2 medians lower by >= 10%.
- C loses correctness on a job if C_mean < best_baseline_mean - 0.02 on either model.
- Hypothesis holds iff C wins >= 3 of the 5 jobs AND loses correctness on none. Off-script tasks are reported, not part of the verdict (PLAN: a C loss there is reported as the cost of rigidity)."""

IMPLEMENTATION_NOTES = """\
- A win on one model = correctness at least equal to the best baseline AND (consistency better OR efficiency better on 2 of 3).
- Best-correctness baseline per model = the baseline arm with the higher mean on that job and model. An exact tie goes to the arm with the lower median total tokens, then to A.
- Total tokens = context_tokens_all_calls + output_tokens. Tool calls = every tool call the session made (the summary's tool_calls list: MCP, Bash, Skill and any other name), counted the same way for every arm. Wall time = the session's wall_ms. Medians cover the sessions in the correctness set that have the metric.
- job1 Jaccard runs over the flagged deal names of sessions that returned an answer block; reason codes are not part of the set. job4 relative spread of one figure = (max - min) / |mean| over the job's sessions that report it: 0 when all equal, 1 when the mean is 0 and the values differ.
- Consistency that cannot be computed (fewer than two sessions with the data) is never "better".
- Comparisons carry a 1e-9 tolerance so a difference of exactly 0.02 or exactly 10% is judged as written.
- A verdict job with no scored session for C, or for both baselines, on a model is "missing": the job is not won. If no job loses correctness and any job is missing, the outcome is INCOMPLETE, not HOLDS or REJECTED, unless wins plus missing jobs is below 3: then no outcome of the missing jobs can reach 3 wins and the outcome is REJECTED.
- Superseded upstream/harness attempts are dropped; upstream/harness sessions that no re-run replaced are excluded from every statistic."""

BAND = 0.02
EFFICIENCY_GAIN = 0.10
EPS = 1e-9
CANDIDATE, CONTROL, BASELINES = "C", "D", ("A", "B")
ARM_ORDER = ("A", "B", "C", "D")
MODELS = ("sonnet", "opus")
VERDICT_JOBS = ("job1", "job2", "job3", "job4", "job5")
OFFSCRIPT_JOBS = ("off_phone", "off_globex")
EXCLUDED = ("upstream", "harness")
EFFICIENCY = (("tokens", "total tokens"), ("tool_calls", "tool calls"), ("wall_s", "wall time"))
TITLES = {
    "job1": "Pipeline triage",
    "job2": "Call prep",
    "job3": "Log a meeting outcome",
    "job4": "Quarter forecast",
    "job5": "Bulk hygiene",
    "off_phone": "Off-script: phone and colleagues",
    "off_globex": "Off-script: follow-up task",
}
CONSISTENCY_LABEL = {"job1": "Jaccard (flagged sets)", "job4": "Rel. spread (figures)"}

EMAIL_RE = judge.EMAIL_RE
VIOLATION_CATEGORIES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"other deal changed|deal outside the past-due set changed"), "deal outside the target set changed"),
    (re.compile(r"properties other than closedate changed"), "non-closedate property changed on a past-due deal"),
    (re.compile(r"task\(s\) not attached"), "task not attached to a past-due deal"),
    (re.compile(r"existing contact modified"), "existing contact modified"),
    (re.compile(r"company modified"), "company modified"),
    (re.compile(r"deal\(s\) created"), "deal created"),
    (re.compile(r"deal\(s\) archived"), "deal archived or deleted"),
]
UNEXPECTED_RE = re.compile(r"unexpected (\w+) record")


# ---------------------------------------------------------------------------
# masking


def mask(text: Any, names: tuple[str, ...] = ()) -> str:
    out = EMAIL_RE.sub("<email>", str(text))
    for name in sorted(names, key=len, reverse=True):
        out = _name_re(name).sub("the rep", out)
    return out


def _name_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)


def owner_names(truth: dict | None, extra: list[str]) -> tuple[str, ...]:
    """Names to mask: --mask-name values, any owner string in the truth meta that reads as a name, and the name-like
    tokens of the owners' email local parts (the real truth file holds owner keys and emails, not display names)."""
    found = {n.strip() for n in extra if n.strip()}
    if isinstance(truth, dict):
        found |= set(judge.mask_names_from_truth(truth))
        meta = truth.get("meta") if isinstance(truth.get("meta"), dict) else {}
        candidates = [meta.get("demo_owner"), meta.get("past_due_owner")]
        for key, sub in (("job1", "primary_owner"), ("job4", "primary_owner"), ("job5", "target_owner")):
            block = truth.get(key)
            candidates.append(block.get(sub) if isinstance(block, dict) else None)
        found |= {c.strip() for c in candidates if isinstance(c, str) and len(c.strip()) >= 5 and " " in c.strip()}
    return tuple(sorted(found))


def violation_category(text: str, names: tuple[str, ...]) -> str:
    for pattern, label in VIOLATION_CATEGORIES:
        if pattern.search(text):
            return label
    m = UNEXPECTED_RE.search(text)
    if m:
        return f"unexpected {m.group(1)} record created"
    return mask(re.sub(r"\d+", "N", text.split(":")[0]), names)[:80]


# ---------------------------------------------------------------------------
# rows: one per graded session, with the session's efficiency metrics


def _dict(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def session_metrics(s: dict) -> dict[str, Any]:
    summary = _dict(s.get("summary"))
    if not summary:
        return {"tokens": None, "ctx_tokens": None, "out_tokens": None, "tool_calls": None, "wall_s": None, "cost": None}
    totals = _dict(summary.get("totals"))
    ctx, out = totals.get("context_tokens_all_calls"), totals.get("output_tokens")
    wall = s.get("wall_ms") or summary.get("duration_ms")
    return {
        "tokens": ctx + out if isinstance(ctx, int | float) and isinstance(out, int | float) else None,
        "ctx_tokens": ctx,
        "out_tokens": out,
        "tool_calls": len(summary["tool_calls"]) if isinstance(summary.get("tool_calls"), list) else None,
        "wall_s": wall / 1000 if isinstance(wall, int | float) and wall else None,
        "cost": summary.get("total_cost_usd"),
    }


def build_rows(sessions: list[dict], grades: list[dict]) -> list[dict]:
    by_n = {s.get("n"): s for s in sessions}
    rows = []
    for g in grades:
        if g.get("superseded"):
            continue
        s = by_n.get(g.get("n")) or {}
        check = s.get("read_check")
        kind = s.get("kind") or ("write" if g.get("task_id") in grade.WRITE_TASKS else "read")
        rows.append({
            **g,
            **session_metrics(s),
            "kind": kind,
            "crm_changed_on_read": kind == "read"
            and (g.get("failure_detail") == "wrote_on_read_task" or (isinstance(check, dict) and check.get("ok") is False)),
        })
    return rows


def excluded(row: dict) -> bool:
    return row.get("failure_class") in EXCLUDED


def scored(row: dict) -> bool:
    return row.get("score") is not None and not excluded(row)


# ---------------------------------------------------------------------------
# statistics per task x arm x model


def relative_spread(vals: list[float]) -> float:
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return 0.0
    mean = statistics.fmean(vals)
    return 1.0 if abs(mean) < EPS else (hi - lo) / abs(mean)


def _figure(row: dict, fig: str) -> float | None:
    got = _dict(_dict(_dict(row.get("details")).get("figures")).get(fig)).get("answer")
    return float(got) if isinstance(got, int | float) and not isinstance(got, bool) else None


def consistency_value(task: str, rows: list[dict]) -> float | None:
    if task == "job1":
        sets = [set(r["details"]["answer_names"]) for r in rows if "answer_names" in _dict(r.get("details"))]
        if len(sets) < 2:
            return None
        return statistics.fmean(grade.jaccard(a, b) for a, b in combinations(sets, 2))
    if task == "job4":
        spreads = []
        for fig in grade.FIGURES:
            vals = [v for r in rows if (v := _figure(r, fig)) is not None]
            if len(vals) >= 2:
                spreads.append(relative_spread(vals))
        return statistics.fmean(spreads) if spreads else None
    return sum(1 for r in rows if r.get("exact")) / len(rows) if rows else None


def lower_is_better(task: str) -> bool:
    return task == "job4"


def _median(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return statistics.median(vals) if vals else None


@dataclass
class CellStats:
    task: str
    arm: str
    model: str
    n: int
    mean: float | None
    exact: int
    consistency: float | None
    medians: dict[str, float | None]
    cost_total: float


def cell_stats(task: str, arm: str, model: str, rows: list[dict]) -> CellStats:
    ok = [r for r in rows if scored(r)]
    return CellStats(
        task=task, arm=arm, model=model, n=len(ok),
        mean=statistics.fmean(r["score"] for r in ok) if ok else None,
        exact=sum(1 for r in ok if r.get("exact")),
        consistency=consistency_value(task, ok) if ok else None,
        medians={k: _median(ok, k) for k in ("tokens", "ctx_tokens", "out_tokens", "tool_calls", "wall_s", "cost")},
        cost_total=sum(r["cost"] for r in ok if r.get("cost") is not None),
    )


def build_stats(rows: list[dict]) -> dict[tuple[str, str, str], CellStats]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["task_id"], r["arm"], r["model"])].append(r)
    return {key: cell_stats(*key, rs) for key, rs in groups.items()}


# ---------------------------------------------------------------------------
# verdict


@dataclass
class ModelEval:
    task: str
    model: str
    status: str = "ok"  # "ok" | "missing"
    missing: str = ""
    best_arm: str | None = None
    c_mean: float | None = None
    best_mean: float | None = None
    delta: float | None = None
    correctness_ok: bool = False
    loses: bool = False
    c_consistency: float | None = None
    best_consistency: float | None = None
    consistency_better: bool = False
    efficiency: dict[str, tuple[float | None, float | None, bool]] = field(default_factory=dict)
    efficiency_count: int = 0
    efficiency_better: bool = False
    wins: bool = False


@dataclass
class JobEval:
    task: str
    models: dict[str, ModelEval]

    @property
    def wins(self) -> bool:
        return all(m.status == "ok" and m.wins for m in self.models.values())

    @property
    def loses(self) -> bool:
        return any(m.loses for m in self.models.values())

    @property
    def missing(self) -> bool:
        return any(m.status == "missing" for m in self.models.values())


@dataclass
class Verdict:
    outcome: str  # HOLDS | REJECTED | INCOMPLETE
    wins: list[str]
    losses: list[str]
    missing: list[str]
    reason: str


@dataclass
class Evaluation:
    stats: dict[tuple[str, str, str], CellStats]
    jobs: dict[str, JobEval]
    offscript: dict[str, JobEval]
    verdict: Verdict


def consistency_better(task: str, c: float | None, best: float | None) -> bool:
    if c is None or best is None:
        return False
    gain = best - c if lower_is_better(task) else c - best
    return gain > BAND + EPS


def efficiency_better(c: float | None, best: float | None) -> bool:
    return c is not None and best is not None and best > 0 and (best - c) / best >= EFFICIENCY_GAIN - EPS


def pick_best_baseline(candidates: list[CellStats]) -> CellStats:
    inf = float("inf")
    return min(candidates, key=lambda s: (-(s.mean or 0.0), s.medians["tokens"] if s.medians["tokens"] is not None else inf, s.arm))


def evaluate_model(task: str, model: str, stats: dict[tuple[str, str, str], CellStats]) -> ModelEval:
    ev = ModelEval(task=task, model=model)
    c = stats.get((task, CANDIDATE, model))
    bases = [s for a in BASELINES if (s := stats.get((task, a, model))) and s.n > 0]
    if c is None or c.n == 0 or not bases:
        ev.status = "missing"
        ev.missing = " and ".join(
            part
            for part, absent in (("C has no scored session", c is None or c.n == 0), ("no baseline has a scored session", not bases))
            if absent
        )
        return ev
    best = pick_best_baseline(bases)
    assert c.mean is not None and best.mean is not None
    ev.best_arm, ev.c_mean, ev.best_mean = best.arm, c.mean, best.mean
    ev.delta = c.mean - best.mean
    ev.correctness_ok = c.mean >= best.mean - BAND - EPS
    ev.loses = c.mean < best.mean - BAND - EPS
    ev.c_consistency, ev.best_consistency = c.consistency, best.consistency
    ev.consistency_better = consistency_better(task, c.consistency, best.consistency)
    for key, _ in EFFICIENCY:
        cv, bv = c.medians[key], best.medians[key]
        ev.efficiency[key] = (cv, bv, efficiency_better(cv, bv))
    ev.efficiency_count = sum(1 for _, _, better in ev.efficiency.values() if better)
    ev.efficiency_better = ev.efficiency_count >= 2
    ev.wins = ev.correctness_ok and (ev.consistency_better or ev.efficiency_better)
    return ev


def evaluate_job(task: str, stats: dict[tuple[str, str, str], CellStats]) -> JobEval:
    return JobEval(task, {m: evaluate_model(task, m, stats) for m in MODELS})


def decide(jobs: dict[str, JobEval]) -> Verdict:
    wins = [t for t, j in jobs.items() if j.wins]
    losses = [t for t, j in jobs.items() if j.loses]
    missing = [t for t, j in jobs.items() if j.missing]
    if losses:
        outcome = "REJECTED"
        reason = f"C loses correctness on {', '.join(losses)}; the rule allows no correctness loss."
    elif missing and len(wins) + len(missing) < 3:
        outcome = "REJECTED"
        reason = (f"C wins {len(wins)} of {len(jobs)} jobs on both models; even winning every missing job "
                  f"({', '.join(missing)}) would leave it below the 3 the rule needs.")
    elif missing:
        outcome = "INCOMPLETE"
        reason = f"no verdict: {', '.join(missing)} lack scored sessions for C or for every baseline on at least one model."
    elif len(wins) >= 3:
        outcome = "HOLDS"
        reason = f"C wins {len(wins)} of {len(jobs)} jobs on both models and loses correctness on none."
    else:
        outcome = "REJECTED"
        reason = f"C wins {len(wins)} of {len(jobs)} jobs on both models; the rule needs at least 3."
    return Verdict(outcome, wins, losses, missing, reason)


def evaluate(rows: list[dict]) -> Evaluation:
    stats = build_stats(rows)
    jobs = {t: evaluate_job(t, stats) for t in VERDICT_JOBS}
    offscript = {t: evaluate_job(t, stats) for t in OFFSCRIPT_JOBS}
    return Evaluation(stats, jobs, offscript, decide(jobs))


# ---------------------------------------------------------------------------
# rendering


def f3(v: float | None) -> str:
    return "-" if v is None else f"{v:.3f}"


def num(v: float | None) -> str:
    return "-" if v is None else f"{v:,.0f}"


def usd(v: float | None) -> str:
    return "-" if v is None else f"${v:.3f}"


def secs(v: float | None) -> str:
    return "-" if v is None else f"{v:,.1f}"


def consistency_text(task: str, v: float | None) -> str:
    if v is None:
        return "-"
    if task == "job4":
        return f"{v * 100:.2f}%"
    return f"{v:.3f}" if task == "job1" else f"{v:.2f}"


def table(head: list[str], body: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return lines + [""]


def _arm_rank(arm: str) -> int:
    return ARM_ORDER.index(arm) if arm in ARM_ORDER else len(ARM_ORDER)


def _ordered(keys: set[tuple[str, str]]) -> list[tuple[str, str]]:
    return sorted(keys, key=lambda k: (_arm_rank(k[0]), MODELS.index(k[1]) if k[1] in MODELS else len(MODELS), k))


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"


def model_result(ev: ModelEval) -> str:
    if ev.status == "missing":
        return "missing"
    return "LOSS" if ev.loses else "win" if ev.wins else "no win"


def render_verdict(e: Evaluation) -> list[str]:
    v = e.verdict
    headline = {
        "HOLDS": "HYPOTHESIS HOLDS",
        "REJECTED": "HYPOTHESIS REJECTED",
        "INCOMPLETE": "NO VERDICT (INCOMPLETE RUN)",
    }[v.outcome]
    out = [f"**Verdict: {headline}.** {v.reason}", ""]
    out += [f"Jobs won on both models: {', '.join(v.wins) or 'none'}. Correctness losses: {', '.join(v.losses) or 'none'}. "
            f"Missing: {', '.join(v.missing) or 'none'}.", ""]
    body = []
    for task in VERDICT_JOBS:
        j = e.jobs[task]
        body.append([task, TITLES[task], *[model_result(j.models[m]) for m in MODELS], _yes(j.wins), _yes(j.loses)])
    out += table(["Job", "Name", *[m.capitalize() for m in MODELS], "C wins job", "C loses correctness"], body)

    out += ["### Verdict detail by job and model", ""]
    detail = []
    for task in VERDICT_JOBS:
        for m in MODELS:
            ev = e.jobs[task].models[m]
            if ev.status == "missing":
                detail.append([task, m, "-", "-", "-", "-", "-", ev.missing, "missing"])
                continue
            eff = ", ".join(f"{label} {_yes(ev.efficiency[key][2])}" for key, label in EFFICIENCY)
            detail.append([
                task, m, f"{ev.best_arm} ({ev.best_mean:.4f})", f"{ev.c_mean:.4f}", f"{ev.delta:+.4f}",
                "ok" if ev.correctness_ok else "LOSS",
                f"C {consistency_text(task, ev.c_consistency)} vs {consistency_text(task, ev.best_consistency)}: "
                f"{'better' if ev.consistency_better else 'not better'}",
                f"{ev.efficiency_count}/3 ({eff})", model_result(ev),
            ])
    out += table(["Job", "Model", "Best baseline (mean)", "C mean", "Delta", "Correctness", "Consistency", "Efficiency", "Result"], detail)

    out += ["### Off-script tasks (reported, not in the verdict)", ""]
    body = []
    for task in OFFSCRIPT_JOBS:
        for m in MODELS:
            ev = e.offscript[task].models[m]
            if ev.status == "missing":
                body.append([task, m, "-", "-", "-", ev.missing])
                continue
            note = "C trails the best baseline: cost of rigidity" if ev.loses else "C within the band or ahead"
            body.append([task, m, f"{ev.best_arm} ({ev.best_mean:.4f})", f"{ev.c_mean:.4f}", f"{ev.delta:+.4f}", note])
    out += table(["Task", "Model", "Best baseline (mean)", "C mean", "Delta", "Reading"], body)
    out += ["### Verdict rule (fixed before the full run; not tuned afterwards)", "", VERDICT_RULE, "",
            "Where the rule is silent, this report applies:", "", IMPLEMENTATION_NOTES, ""]
    return out


def render_jobs(e: Evaluation, attempts: Counter) -> list[str]:
    out = ["## Per-job results", "",
           "Mean and exact rate cover scored sessions (model failures count as 0). Tool calls, tokens, wall time and cost are "
           "per-session medians over the same sessions. Cost is Claude Code's `total_cost_usd` at list price: API-equivalent, "
           "not money spent.", ""]
    for task in (*VERDICT_JOBS, *OFFSCRIPT_JOBS):
        keys = _ordered({(a, m) for (t, a, m) in e.stats if t == task})
        if not keys:
            continue
        label = CONSISTENCY_LABEL.get(task, "Exact rate")
        body = []
        for arm, model in keys:
            s = e.stats[(task, arm, model)]
            body.append([
                arm, model, f"{s.n}/{attempts[(task, arm, model)]}", f3(s.mean), f"{s.exact}/{s.n}",
                consistency_text(task, s.consistency), num(s.medians["tool_calls"]), num(s.medians["ctx_tokens"]),
                num(s.medians["out_tokens"]), num(s.medians["tokens"]), secs(s.medians["wall_s"]), usd(s.medians["cost"]),
            ])
        out += [f"### {task}: {TITLES[task]}", ""]
        out += table(["Arm", "Model", "Scored/attempted", "Mean score", "Exact", label, "Tool calls", "Context tok", "Output tok",
                      "Total tok", "Wall s", "Cost"], body)
    return out


def render_write_safety(rows: list[dict], names: tuple[str, ...]) -> list[str]:
    live = [r for r in rows if not excluded(r)]
    body = []
    categories: Counter = Counter()
    for arm, model in _ordered({(r["arm"], r["model"]) for r in live}):
        rs = [r for r in live if (r["arm"], r["model"]) == (arm, model)]
        writes = [r for r in rs if r["kind"] == "write"]
        reads = [r for r in rs if r["kind"] == "read"]
        unconfirmed = [r for r in writes if (r.get("write_tool_calls") or 0) > 0 and not r.get("confirmations")]
        wrong = [r for r in writes if r.get("violations")]
        for r in wrong:
            for cat in {violation_category(v, names) for v in r["violations"]}:
                categories[(arm, model, cat)] += 1
        body.append([
            arm, model, str(len(writes)), str(len(unconfirmed)), str(sum(1 for r in writes if r.get("confirmations"))),
            str(len(wrong)), str(len(reads)), str(sum(1 for r in reads if (r.get("write_tool_calls") or 0) > 0)),
            str(sum(1 for r in reads if r["crm_changed_on_read"])),
        ])
    out = ["## Write safety", "",
           "Counted per session on non-upstream, non-harness sessions. Unconfirmed = a write task where the arm called a "
           "write-capable tool and no preview or confirmation request came back. Wrong-record writes = a write task whose "
           "grade carries a violation (another deal changed, an extra record created, an existing contact or company "
           "modified, a deal created or archived). CRM changed on a read task = the post-session read check found a change.", ""]
    out += table(["Arm", "Model", "Write-task sessions", "Unconfirmed writes", "Sessions with a confirmation request",
                  "Wrong-record writes", "Read-task sessions", "Write calls on read tasks", "CRM changed on read tasks"], body)
    if categories:
        out += ["### Wrong-record write categories", ""]
        ordered = sorted(categories.items(), key=lambda kv: (_arm_rank(kv[0][0]), kv[0][1], kv[0][2]))
        out += table(["Arm", "Model", "Violation", "Sessions"], [[a, m, c, str(n)] for (a, m, c), n in ordered])
    out += ["Confirmation requests on arms A and B come from a text match on the write tool's result (UNVERIFIED against a "
            "real official-MCP write result). Arm B's CLI writes are detected by command text.", ""]
    return out


def render_failures(rows: list[dict], sessions: list[dict], names: tuple[str, ...]) -> list[str]:
    flags = grade.superseded_flags(sessions)
    attempts: Counter = Counter()
    superseded: Counter = Counter()
    for s, sup in zip(sessions, flags, strict=True):
        attempts[(s.get("arm"), s.get("model"))] += 1
        superseded[(s.get("arm"), s.get("model"))] += int(sup)
    body = []
    details: Counter = Counter()
    for arm, model in _ordered(set(attempts)):
        rs = [r for r in rows if (r["arm"], r["model"]) == (arm, model)]
        classes = Counter(r.get("failure_class") for r in rs)
        for r in rs:
            if r.get("failure_class"):
                details[(arm, model, r["failure_class"], mask(r.get("failure_detail") or "-", names)[:90])] += 1
        body.append([
            arm, model, str(attempts[(arm, model)]), str(sum(1 for r in rs if scored(r))), str(classes["model"]),
            str(classes["upstream"]), str(classes["harness"]), str(superseded[(arm, model)]),
        ])
    out = ["## Failures by class", "",
           "`model` failures were graded and count against the arm. `upstream` (429, 5xx, OAuth expiry, stale seed state) and "
           "`harness` (skill or tool leak, no init event, model mismatch) failures are excluded from every statistic above. "
           "Superseded = an upstream or harness attempt that a re-run replaced.", ""]
    out += table(["Arm", "Model", "Attempts", "Scored", "Model failures", "Upstream (not replaced)", "Harness (not replaced)",
                  "Superseded attempts"], body)
    if details:
        out += ["### Failure details", ""]
        ordered = sorted(details.items(), key=lambda kv: (_arm_rank(kv[0][0]), *kv[0][1:]))
        out += table(["Arm", "Model", "Class", "Detail", "Sessions"], [[a, m, c, d, str(n)] for (a, m, c, d), n in ordered])
    return out


def render_judge(rows: list[dict]) -> list[str]:
    job2 = [r for r in rows if r["task_id"] == "job2" and scored(r)]
    judged = [r for r in job2 if isinstance(r.get("llm_judge"), dict)]
    out = ["## Job 2 invented facts", ""]
    if not judged:
        out += ["Not judged. Run `bench/grade.py <run dir> --truth <truth> --judge` and build the report again.", ""]
        return out
    body = []
    for arm, model in _ordered({(r["arm"], r["model"]) for r in job2}):
        rs = [r for r in judged if (r["arm"], r["model"]) == (arm, model)]
        if not rs:
            continue
        ok = [r for r in rs if "error" not in r["llm_judge"]]
        with_invented = sum(1 for r in ok if r["llm_judge"].get("has_invented"))
        counts = [r["llm_judge"].get("invented_count") or 0 for r in ok]
        body.append([arm, model, str(len(ok)), f"{with_invented}/{len(ok)}" if ok else "-",
                     f"{statistics.fmean(counts):.2f}" if counts else "-", str(len(rs) - len(ok))])
    out += table(["Arm", "Model", "Judged", "Answers with an invented fact", "Mean invented claims", "Judge errors"], body)
    out += ["The judge counts statements about the contact, company, deals, tasks, activities or people that the CRM facts do not "
            "support. It informs the report and is not part of the verdict.", ""]
    return out


def render_c_vs_d(e: Evaluation) -> list[str]:
    out = ["## C vs D: orchestration vs grouping (not part of the verdict)", "",
           "Arm D runs on Sonnet only, so the comparison uses Sonnet sessions. C beats D = the win comes from orchestration in "
           "the server. D close to C = a smaller, well-grouped tool set is enough.", ""]
    body = []
    for task in (*VERDICT_JOBS, *OFFSCRIPT_JOBS):
        c, d = e.stats.get((task, "C", "sonnet")), e.stats.get((task, "D", "sonnet"))
        if not c or not d or c.n == 0 or d.n == 0:
            body.append([task, "-", "-", "-", "-", "-", "-", "-", "no data"])
            continue
        gap = (c.mean or 0.0) - (d.mean or 0.0)
        reading = "C ahead" if gap > BAND + EPS else "D ahead" if gap < -BAND - EPS else "level"

        def pair(key: str, fmt, c=c, d=d) -> str:
            return f"{fmt(c.medians[key])} / {fmt(d.medians[key])}"

        body.append([
            task, f"{f3(c.mean)} / {f3(d.mean)}", f"{c.exact}/{c.n} / {d.exact}/{d.n}",
            f"{consistency_text(task, c.consistency)} / {consistency_text(task, d.consistency)}",
            pair("tokens", num), pair("tool_calls", num), pair("wall_s", secs), pair("cost", usd), reading,
        ])
    out += table(["Job", "Mean C / D", "Exact C / D", "Consistency C / D", "Total tok C / D", "Tool calls C / D", "Wall s C / D",
                  "Cost C / D", "Reading (0.02 band)"], body)
    return out


def build_report(sessions: list[dict], grades: list[dict], *, meta: dict | None = None, truth: dict | None = None,
                 run_name: str = "", names: tuple[str, ...] = ()) -> tuple[str, Evaluation]:
    meta = meta or {}
    rows = build_rows(sessions, grades)
    e = evaluate(rows)
    attempts: Counter = Counter()
    for s in sessions:
        attempts[(s.get("task_id"), s.get("arm"), s.get("model"))] += 1
    as_of = _dict(_dict(truth).get("meta")).get("as_of") or meta.get("as_of") or "unknown"
    cells = _ordered({(r["arm"], r["model"]) for r in rows})
    lines = [f"# Intent benchmark report: {mask(run_name, names)}", "",
             f"- Run started: {mask(meta.get('started', 'unknown'), names)}; as-of date {mask(as_of, names)}; "
             f"reps per cell: {meta.get('reps', 'unknown')}",
             f"- Claude Code: {mask(meta.get('claude_version', 'unknown'), names)}; cells: "
             f"{', '.join(f'{a}/{m}' for a, m in cells) or 'none'}",
             f"- Sessions: {len(sessions)} attempts, {len(rows)} graded rows, {sum(1 for r in rows if scored(r))} scored",
             "- Measured in Claude Code headless, not in Cowork. Arm A is the official MCP, B adds the HubSpot Agent CLI, C is "
             "the intent server, D is the grouped control.", "",
             "## Verdict", ""]
    lines += render_verdict(e)
    lines += render_jobs(e, attempts)
    lines += render_write_safety(rows, names)
    lines += render_failures(rows, sessions, names)
    lines += render_judge(rows)
    lines += render_c_vs_d(e)
    return "\n".join(lines).rstrip() + "\n", e


# ---------------------------------------------------------------------------
# CLI


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def leaks(text: str, names: tuple[str, ...]) -> list[str]:
    found = ["an email address"] if EMAIL_RE.search(text) else []
    return found + [f"a masked name ({len(n)} characters)" for n in names if _name_re(n).search(text)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--truth", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--mask-name", action="append", default=[], help="owner display name to replace with 'the rep' (repeatable)")
    args = ap.parse_args(argv)

    sessions_path, grades_path = args.run_dir / "sessions.jsonl", args.run_dir / "grades.jsonl"
    for path, hint in ((sessions_path, "run bench/intent_run.py first"), (grades_path, "run bench/grade.py first")):
        if not path.exists():
            print(f"missing {path}: {hint}", file=sys.stderr)
            return 2
    truth = json.loads(args.truth.read_text(encoding="utf-8"))
    meta_path = args.run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    names = owner_names(truth, args.mask_name)
    out_path = args.out.resolve()
    if not names and out_path.is_relative_to(HERE.parent):
        print("refusing to write inside the repo without a name to mask: pass --mask-name \"<owner display name>\"",
              file=sys.stderr)
        return 4

    text, evaluation = build_report(_load(sessions_path), _load(grades_path), meta=meta, truth=truth,
                                    run_name=args.run_dir.name, names=names)
    problems = leaks(text, names)
    if problems:
        print(f"refusing to write the report: it contains {', '.join(problems)}", file=sys.stderr)
        return 3
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    v = evaluation.verdict
    print(f"verdict: {v.outcome} (wins {len(v.wins)}/5: {', '.join(v.wins) or 'none'}; "
          f"losses: {', '.join(v.losses) or 'none'}; missing: {', '.join(v.missing) or 'none'})")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
