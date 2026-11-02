"""Unit tests for bench/intent_report.py.

Every session and grade here is synthetic: placeholder deal names, no real emails, no truth-file values. The verdict
cases mirror the pre-registered rule: a clear pass, one correctness loss, a tie inside the 0.02 band, a win on
one model only (not a win), and upstream failures that must not touch any statistic. One test renders a real
smoke run when the gitignored run directory and the truth file are on this machine; it asserts structure only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
import grade  # noqa: E402
import intent_report as ir  # noqa: E402

REAL_TRUTH = Path(
    os.environ.get(
        "BENCH_TRUTH_PATH",
        "/Users/izzy/Documents/hubspot-forecast/demo/seed/out/truth-enterprise-2026-11-10.json",
    )
)
REAL_RUN = Path(__file__).resolve().parent.parent / "bench" / "runs" / "2026-10-30T19-53-37Z-intent"

BASE = {"score": 0.8, "tokens": 1000, "tools": 10, "wall": 100.0}
WIN = {"score": 0.9, "tokens": 500, "tools": 4, "wall": 40.0}  # 50% lower on all three efficiency medians


class Run:
    """Collects session and grade rows the way intent_run.py and grade.py write them."""

    def __init__(self) -> None:
        self.sessions: list[dict] = []
        self.grades: list[dict] = []
        self._n = 0

    def add(self, task, arm, model, scores, *, exact=None, tokens=1000, tools=10, wall=100.0, cost=0.1, failure=None,
            detail="", details=None, violations=None, write_calls=0, confirmations=0, read_ok=True, attempt=1, rep=None,
            llm_judge=None) -> None:
        for i, score in enumerate(scores):
            self._n += 1
            kind = "write" if task in grade.WRITE_TASKS else "read"
            # What summary.tool_calls holds in a real session: a Skill load and an unprefixed tool name are tool calls too,
            # while mcp_calls keeps only the mcp__ names (so counting mcp_calls would undercount arms C and D).
            names = (["Skill", "companies"][:tools]) + ["mcp__hubspot__search_crm_objects"] * max(0, tools - 2)
            summary = {"totals": {"context_tokens_all_calls": tokens - 100, "output_tokens": 100}, "total_cost_usd": cost,
                       "tool_calls": [{"name": name} for name in names]}
            self.sessions.append({
                "n": self._n, "rep": rep or i + 1, "task_id": task, "arm": arm, "model": model, "kind": kind,
                "attempt": attempt, "wall_ms": int(wall * 1000), "summary": summary, "mcp_calls": [{}] * max(0, tools - 2),
                "cli_calls": [], "failure_class": failure,
                "read_check": None if kind == "write" else {"ok": read_ok},
            })
            is_exact = (score is not None and score >= 1.0) if exact is None else (exact[i] if isinstance(exact, list) else exact)
            self.grades.append({
                "n": self._n, "rep": rep or i + 1, "task_id": task, "arm": arm, "model": model, "attempt": attempt,
                "superseded": False, "failure_class": failure, "failure_detail": detail, "score": score,
                "exact": bool(is_exact), "violations": list(violations or []), "confirmations": confirmations,
                "write_tool_calls": write_calls, "details": (details[i] if isinstance(details, list) else details) or {},
                "llm_judge": (llm_judge[i] if isinstance(llm_judge, list) else llm_judge),
            })

    def cell(self, task, arm, model, spec, n=3, **kw) -> None:
        self.add(task, arm, model, [spec["score"]] * n, tokens=spec["tokens"], tools=spec["tools"], wall=spec["wall"], **kw)

    def drop(self, task=None, arm=None) -> None:
        def keep(r: dict) -> bool:
            return not ((task is None or r["task_id"] == task) and (arm is None or r["arm"] == arm))

        self.sessions = [s for s in self.sessions if keep(s)]
        self.grades = [g for g in self.grades if keep(g)]


def world(c_spec: dict | None = None, *, jobs=ir.VERDICT_JOBS, d_score=0.95) -> Run:
    """A, B, C on both models for every job, plus D on Sonnet. D beats C on correctness to prove it is never a baseline.

    c_spec maps (task, model) to the spec C gets; every other C cell gets WIN. B is always worse than A."""
    run = Run()
    for task in jobs:
        for model in ir.MODELS:
            run.cell(task, "A", model, BASE)
            run.cell(task, "B", model, {**BASE, "score": 0.7})
            run.cell(task, "C", model, (c_spec or {}).get((task, model), WIN))
        run.cell(task, "D", "sonnet", {"score": d_score, "tokens": 100, "tools": 1, "wall": 5.0})
    return run


def evaluate(run: Run) -> ir.Evaluation:
    return ir.evaluate(ir.build_rows(run.sessions, run.grades))


def stats_of(run: Run) -> dict:
    return ir.build_stats(ir.build_rows(run.sessions, run.grades))


def report(run: Run, **kw) -> str:
    return ir.build_report(run.sessions, run.grades, **kw)[0]


def section_rows(text: str, heading: str) -> list[list[str]]:
    """Body rows (cells) of the first table under the heading that starts with `heading`."""
    rows, inside = [], False
    for line in text.splitlines():
        if line.startswith("#"):
            inside = line.lstrip("# ").startswith(heading)
        elif inside and line.startswith("|") and not line.startswith("|---"):
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows[1:]


# ---------------------------------------------------------------------------
# the verdict cases


class TestVerdict:
    def test_clear_pass(self):
        e = evaluate(world())
        assert e.verdict.outcome == "HOLDS"
        assert e.verdict.wins == list(ir.VERDICT_JOBS)
        assert e.verdict.losses == [] and e.verdict.missing == []
        for job in e.jobs.values():
            for ev in job.models.values():
                assert ev.best_arm == "A"
                assert ev.efficiency_count == 3 and ev.wins
        assert "HYPOTHESIS HOLDS" in report(world())

    def test_control_d_is_never_a_baseline(self):
        # D scores above C on Sonnet: still a clear pass, because only A and B are baselines.
        e = evaluate(world(d_score=0.99))
        assert e.verdict.outcome == "HOLDS"
        assert all(ev.best_arm in ir.BASELINES for job in e.jobs.values() for ev in job.models.values())

    def test_one_correctness_loss_rejects_despite_four_wins(self):
        spec = {("job3", "opus"): {**WIN, "score": 0.77}}  # 0.8 - 0.77 = 0.03 > 0.02, Opus only
        e = evaluate(world(spec))
        assert e.verdict.outcome == "REJECTED"
        assert e.verdict.losses == ["job3"]
        assert e.verdict.wins == ["job1", "job2", "job4", "job5"]
        job3 = e.jobs["job3"]
        assert job3.models["sonnet"].wins and job3.models["opus"].loses and not job3.wins
        assert "C loses correctness on job3" in report(world(spec))

    def test_tie_inside_the_band_is_not_a_loss(self):
        # 0.78 is exactly best - 0.02: "at least equal".
        e = evaluate(world({("job2", "sonnet"): {**WIN, "score": 0.78}}))
        ev = e.jobs["job2"].models["sonnet"]
        assert ev.correctness_ok and not ev.loses and ev.wins
        assert e.verdict.outcome == "HOLDS"
        lost = evaluate(world({("job2", "sonnet"): {**WIN, "score": 0.779}}))
        assert lost.jobs["job2"].models["sonnet"].loses and lost.verdict.outcome == "REJECTED"

    def test_equal_correctness_without_an_edge_is_no_win(self):
        # C identical to the best baseline on every metric: nobody loses, nobody wins, the rule needs 3 wins.
        tie = {(t, m): BASE for t in ir.VERDICT_JOBS for m in ir.MODELS}
        e = evaluate(world(tie))
        assert e.verdict.outcome == "REJECTED"
        assert e.verdict.wins == [] and e.verdict.losses == []

    def test_win_on_one_model_only_is_not_a_win(self):
        spec = {("job4", "opus"): BASE, ("job5", "opus"): BASE}
        e = evaluate(world(spec))
        assert e.jobs["job4"].models["sonnet"].wins and not e.jobs["job4"].models["opus"].wins
        assert not e.jobs["job4"].wins and not e.jobs["job5"].wins
        assert e.verdict.wins == ["job1", "job2", "job3"] and e.verdict.outcome == "HOLDS"
        spec[("job3", "opus")] = BASE  # a third job that wins on Sonnet only leaves two full wins
        e = evaluate(world(spec))
        assert e.verdict.wins == ["job1", "job2"] and e.verdict.outcome == "REJECTED"

    def test_upstream_and_harness_sessions_change_nothing(self):
        noisy = world()
        # Cheap-looking upstream and harness sessions on the baseline and on C: all excluded.
        noisy.add("job1", "A", "sonnet", [None, None], failure="upstream", detail="signature_in_stderr", tokens=9_000_000, tools=99)
        noisy.add("job1", "A", "sonnet", [None], failure="harness", detail="skills_unexpected: x", tokens=1)
        noisy.add("job1", "C", "sonnet", [None, None], failure="upstream", detail="mcp_needs-auth", tokens=1, tools=1)
        a, b = stats_of(world()), stats_of(noisy)
        for key in (("job1", "A", "sonnet"), ("job1", "C", "sonnet")):
            assert a[key].n == b[key].n == 3
            assert a[key].mean == b[key].mean
            assert a[key].medians == b[key].medians
        assert evaluate(noisy).verdict.outcome == "HOLDS"
        row = next(r for r in section_rows(report(noisy), "Failures by class") if r[:2] == ["A", "sonnet"])
        assert row[5] == "2" and row[6] == "1"  # upstream and harness reported apart

    def test_model_failure_counts_against_the_arm(self):
        run = world()
        run.add("job1", "A", "sonnet", [0.0], failure="model", detail="timeout")
        stat = stats_of(run)[("job1", "A", "sonnet")]
        assert stat.n == 4 and stat.mean == pytest.approx(0.6)

    def test_superseded_attempts_are_counted_but_not_graded(self):
        run = world()
        run.add("job2", "B", "opus", [None], failure="upstream", detail="signature_in_stderr", rep=9, attempt=1)
        run.add("job2", "B", "opus", [0.7], rep=9, attempt=2)
        run.grades = [g for g in run.grades if not (g["failure_class"] == "upstream" and g["rep"] == 9)]  # grade.py drops it
        row = next(r for r in section_rows(report(run), "Failures by class") if r[:2] == ["B", "opus"])
        assert row[-1] == "1"

    def test_missing_cell_gives_no_verdict_unless_something_lost(self):
        run = world()
        run.drop(task="job2", arm="C")
        e = evaluate(run)
        assert e.verdict.outcome == "INCOMPLETE" and e.verdict.missing == ["job2"]
        assert "NO VERDICT" in report(run)
        lost = world({("job4", "opus"): {**WIN, "score": 0.5}})
        lost.drop(task="job2", arm="C")
        assert evaluate(lost).verdict.outcome == "REJECTED"

    def test_missing_jobs_that_cannot_reach_three_wins_reject(self):
        # job1 wins, job2 is missing, job3-5 do not win: even a job2 win would give 2 of 5, so the rule says REJECTED.
        flat = {(t, m): BASE for t in ("job3", "job4", "job5") for m in ir.MODELS}
        run = world(flat)
        run.drop(task="job2", arm="C")
        e = evaluate(run)
        assert e.verdict.wins == ["job1"] and e.verdict.missing == ["job2"] and e.verdict.losses == []
        assert e.verdict.outcome == "REJECTED" and "job2" in e.verdict.reason
        # two missing jobs could still lift 1 win to 3: undecided
        run.drop(task="job3", arm="C")
        assert evaluate(run).verdict.outcome == "INCOMPLETE"

    def test_four_wins_with_a_missing_job_stays_incomplete(self):
        # no C session for job2 means its correctness comparison does not exist, so a loss there cannot be ruled out
        run = world()
        run.drop(task="job2", arm="C")
        assert evaluate(run).verdict.wins == ["job1", "job3", "job4", "job5"]
        assert evaluate(run).verdict.outcome == "INCOMPLETE"

    def test_off_script_losses_are_reported_not_scored(self):
        spec = {("off_phone", "sonnet"): {**WIN, "score": 0.1}}
        run = world(spec, jobs=(*ir.VERDICT_JOBS, *ir.OFFSCRIPT_JOBS))
        e = evaluate(run)
        assert e.verdict.outcome == "HOLDS"
        assert e.offscript["off_phone"].models["sonnet"].loses
        assert "cost of rigidity" in report(run)


# ---------------------------------------------------------------------------
# which baseline supplies the comparison


def exact_of(k: int, n: int = 10) -> list[bool]:
    return [True] * k + [False] * (n - k)


class TestBestBaselineSuppliesTheComparison:
    def test_a_more_correct_b_loses_nothing_but_sets_the_efficiency_bar(self):
        # B is both more correct than A and cheaper than C: C beats A on every efficiency median, but the bar is B.
        run = Run()
        run.cell("job2", "A", "sonnet", {"score": 0.7, "tokens": 1000, "tools": 10, "wall": 100.0})
        run.cell("job2", "B", "sonnet", {"score": 0.85, "tokens": 400, "tools": 3, "wall": 30.0})
        run.cell("job2", "C", "sonnet", {"score": 0.9, "tokens": 500, "tools": 4, "wall": 40.0})
        ev = ir.evaluate_model("job2", "sonnet", stats_of(run))
        assert ev.best_arm == "B" and ev.correctness_ok
        assert ev.efficiency_count == 0 and not ev.wins

    def test_a_cheaper_but_less_correct_b_does_not_set_the_bar(self):
        # A is the best-correctness baseline. B is far cheaper and more consistent but less correct: the efficiency
        # medians and the consistency value must still come from A.
        run = Run()
        run.add("job2", "A", "sonnet", [0.9] * 10, exact=exact_of(5), tokens=1000, tools=10, wall=100.0)
        run.add("job2", "B", "sonnet", [0.8] * 10, exact=exact_of(9), tokens=100, tools=3, wall=5.0)
        run.add("job2", "C", "sonnet", [0.9] * 10, exact=exact_of(7), tokens=500, tools=4, wall=40.0)
        ev = ir.evaluate_model("job2", "sonnet", stats_of(run))
        assert ev.best_arm == "A"
        assert ev.efficiency_count == 3 and ev.efficiency_better
        assert ev.best_consistency == pytest.approx(0.5) and ev.consistency_better and ev.wins

    def test_the_best_baseline_is_picked_per_model(self):
        run = Run()
        for model, (a, b) in {"sonnet": (0.8, 0.7), "opus": (0.7, 0.8)}.items():
            run.cell("job3", "A", model, {**BASE, "score": a})
            run.cell("job3", "B", model, {**BASE, "score": b})
            run.cell("job3", "C", model, WIN)
        job = ir.evaluate_job("job3", stats_of(run))
        assert [job.models[m].best_arm for m in ir.MODELS] == ["A", "B"]

    def test_a_job_can_be_won_on_consistency_alone_on_both_models(self):
        run = Run()
        for model in ir.MODELS:
            run.add("job2", "A", model, [0.8] * 50, exact=exact_of(10, 50))
            run.add("job2", "B", model, [0.7] * 50, exact=exact_of(0, 50))
            run.add("job2", "C", model, [0.8] * 50, exact=exact_of(30, 50))  # 0.60 vs 0.20, same tokens, tools and wall
        job = ir.evaluate_job("job2", stats_of(run))
        for ev in job.models.values():
            assert ev.consistency_better and ev.efficiency_count == 0 and ev.wins
        assert job.wins


# ---------------------------------------------------------------------------
# thresholds and consistency


class TestThresholds:
    def _model_eval(self, c_spec, base_spec, task="job2", n=3):
        run = Run()
        run.cell(task, "A", "sonnet", base_spec, n=n)
        run.cell(task, "C", "sonnet", c_spec, n=n)
        return ir.evaluate_model(task, "sonnet", stats_of(run))

    def test_efficiency_needs_ten_percent_on_two_of_three(self):
        ev = self._model_eval({**BASE, "tokens": 900, "tools": 9}, BASE)  # exactly 10% lower on two
        assert ev.efficiency_count == 2 and ev.efficiency_better and ev.wins
        ev = self._model_eval({**BASE, "tokens": 500}, BASE)  # one of three
        assert ev.efficiency_count == 1 and not ev.efficiency_better and not ev.wins
        ev = self._model_eval({**BASE, "tokens": 901, "wall": 90.1}, BASE)  # 9.9% lower is not enough
        assert ev.efficiency_count == 0

    def test_consistency_must_beat_the_baseline_by_more_than_the_band(self):
        def exact(k):  # k of 50 sessions exact
            return [True] * k + [False] * (50 - k)

        def run_for(c_exact):
            run = Run()
            run.add("job2", "A", "sonnet", [0.8] * 50, exact=exact(25))
            run.add("job2", "C", "sonnet", [0.8] * 50, exact=exact(c_exact))
            return ir.evaluate_model("job2", "sonnet", stats_of(run))

        at_band = run_for(26)  # 0.52 vs 0.50: exactly 0.02 better, not "more than 0.02"
        assert not at_band.consistency_better and not at_band.wins
        beyond = run_for(27)
        assert beyond.consistency_better and beyond.wins

    def test_job4_lower_spread_is_better(self):
        def figures(values):
            return [{"figures": {fig: {"answer": v} for fig in grade.FIGURES}} for v in values]

        def run_for(c_values, base_values):
            run = Run()
            run.add("job4", "A", "sonnet", [0.9] * 3, details=figures(base_values))
            run.add("job4", "C", "sonnet", [0.9] * 3, details=figures(c_values))
            return ir.evaluate_model("job4", "sonnet", stats_of(run))

        steady = run_for([100, 100, 100], [90, 100, 110])  # spread 0 vs (110 - 90) / 100 = 0.2
        assert steady.c_consistency == 0.0 and steady.best_consistency == pytest.approx(0.2)
        assert steady.consistency_better and steady.wins
        wobbly = run_for([90, 100, 110], [100, 100, 100])
        assert not wobbly.consistency_better and not wobbly.wins

    def test_job1_jaccard_is_the_mean_over_pairs(self):
        rows = [{"details": {"answer_names": names}} for names in (["a", "b"], ["a", "c"], ["a", "b"])]
        # pairs: (1,2) = 1/3, (1,3) = 1, (2,3) = 1/3
        assert ir.consistency_value("job1", rows) == pytest.approx((1 / 3 + 1 + 1 / 3) / 3)
        assert ir.consistency_value("job1", rows[:1]) is None

    def test_undefined_consistency_is_never_better(self):
        assert not ir.consistency_better("job1", None, 0.5)
        assert not ir.consistency_better("job4", 0.0, None)

    def test_relative_spread(self):
        assert ir.relative_spread([5, 5, 5]) == 0.0
        assert ir.relative_spread([0, 0, 1]) == pytest.approx(3.0)
        assert ir.relative_spread([-1, 1]) == 1.0  # mean 0, values differ

    def test_best_baseline_choice_and_tiebreaks(self):
        def best(spec_a, spec_b):
            run = Run()
            run.cell("job2", "A", "sonnet", spec_a)
            run.cell("job2", "B", "sonnet", spec_b)
            s = stats_of(run)
            return ir.pick_best_baseline([s[("job2", "B", "sonnet")], s[("job2", "A", "sonnet")]]).arm

        assert best({**BASE, "score": 0.7}, {**BASE, "score": 0.85}) == "B"  # higher mean
        assert best({**BASE, "tokens": 1000}, {**BASE, "tokens": 800}) == "B"  # tie: fewer tokens
        assert best(BASE, BASE) == "A"  # tie again: A

    def test_total_tokens_tool_calls_and_wall_time_per_session(self):
        run = Run()
        run.add("job1", "B", "opus", [1.0], tokens=2500, tools=3, wall=12.5, cost=0.4)
        row = ir.build_rows(run.sessions, run.grades)[0]
        assert row["tokens"] == 2500 and row["ctx_tokens"] == 2400 and row["out_tokens"] == 100
        assert row["tool_calls"] == 3 and row["wall_s"] == 12.5 and row["cost"] == 0.4

    def test_tool_calls_count_every_call_the_session_made(self):
        run = Run()
        run.add("job1", "C", "sonnet", [1.0], tools=4)
        session = run.sessions[-1]
        assert len(session["mcp_calls"]) == 2  # counting mcp__ names alone would say 2 for a session that made 4 calls
        assert ir.build_rows(run.sessions, run.grades)[0]["tool_calls"] == 4
        session["summary"]["tool_calls"].append({"name": "Skill"})
        session["summary"]["tool_calls"].append({"name": "Bash"})
        assert ir.build_rows(run.sessions, run.grades)[0]["tool_calls"] == 6
        del session["summary"]["tool_calls"]
        assert ir.build_rows(run.sessions, run.grades)[0]["tool_calls"] is None

    def test_a_skill_load_can_flip_the_tool_call_median(self):
        # baseline 10 calls, C made 9 MCP calls plus 1 Skill load: 10 calls is not 10% lower, 9 would be.
        run = Run()
        run.cell("job2", "A", "sonnet", BASE)
        run.cell("job2", "C", "sonnet", {**BASE, "tools": 10})
        run.sessions = [s | {"mcp_calls": [{}] * 9} if s["arm"] == "C" else s for s in run.sessions]
        ev = ir.evaluate_model("job2", "sonnet", stats_of(run))
        assert ev.efficiency["tool_calls"] == (10, 10, False)


# ---------------------------------------------------------------------------
# report sections


class TestReport:
    def test_rule_is_verbatim_in_docstring_and_report(self):
        assert ir.VERDICT_RULE in ir.__doc__
        assert ir.IMPLEMENTATION_NOTES in ir.__doc__
        text = report(world())
        assert ir.VERDICT_RULE in text and ir.IMPLEMENTATION_NOTES in text
        assert "C_mean >= best_baseline_mean - 0.02" in text
        assert "Hypothesis holds iff C wins >= 3 of the 5 jobs AND loses correctness on none." in text

    def test_per_job_tables_carry_every_metric(self):
        text = report(world())
        for task in ir.VERDICT_JOBS:
            assert f"### {task}: {ir.TITLES[task]}" in text
        head = next(line for line in text.splitlines() if line.startswith("| Arm | Model | Scored/attempted"))
        for col in ("Mean score", "Exact", "Tool calls", "Context tok", "Output tok", "Wall s", "Cost"):
            assert col in head
        assert "Jaccard (flagged sets)" in text and "Rel. spread (figures)" in text

    def test_write_safety_counts_and_hides_record_names(self):
        run = Run()
        run.add("job5", "C", "sonnet", [0.9], write_calls=2, confirmations=0,
                violations=["deal outside the past-due set changed: Hidden Deal Name", "3 unexpected task record(s) created"])
        run.add("job3", "C", "sonnet", [1.0], write_calls=1, confirmations=1)
        run.add("job3", "C", "sonnet", [None], failure="upstream", write_calls=5)  # excluded
        run.add("job1", "C", "sonnet", [0.0], failure="model", detail="wrote_on_read_task", read_ok=False, write_calls=1)
        text = report(run)
        row = next(r for r in section_rows(text, "Write safety") if r[:2] == ["C", "sonnet"])
        # write sessions, unconfirmed, with a confirmation, wrong-record, read sessions, write calls on reads, CRM changed
        assert row[2:] == ["2", "1", "1", "1", "1", "1", "1"]
        assert "deal outside the target set changed" in text and "unexpected task record created" in text
        assert "Hidden Deal Name" not in text

    def test_failures_table_lists_classes_and_details(self):
        run = Run()
        run.add("job1", "C", "sonnet", [0.0], failure="model", detail="no_block")
        run.add("job2", "C", "sonnet", [None], failure="upstream", detail="mcp_needs-auth")
        run.add("job3", "C", "sonnet", [None], failure="harness", detail="model_mismatch: a != b")
        text = report(run)
        row = next(r for r in section_rows(text, "Failures by class") if r[:2] == ["C", "sonnet"])
        assert row[2:7] == ["3", "1", "1", "1", "1"]  # attempts, scored (the model failure scores 0), model, upstream, harness
        assert "no_block" in text and "mcp_needs-auth" in text

    def test_job2_judge_section(self):
        run = Run()
        judge = [{"invented": [{}], "invented_count": 2, "has_invented": True},
                 {"invented": [], "invented_count": 0, "has_invented": False}, {"error": "timeout"}]
        run.add("job2", "C", "sonnet", [1.0, 1.0, 1.0], llm_judge=judge)
        run.add("job2", "A", "sonnet", [1.0])
        row = next(r for r in section_rows(report(run), "Job 2 invented facts") if r[:2] == ["C", "sonnet"])
        assert row[2:] == ["2", "1/2", "1.00", "1"]
        assert "Not judged" in report(world())

    def test_c_vs_d_uses_sonnet_and_reads_the_gap(self):
        run = world(d_score=0.95)  # C 0.9 vs D 0.95
        rows = {r[0]: r for r in section_rows(report(run), "C vs D")}
        assert rows["job1"][1] == "0.900 / 0.950" and rows["job1"][-1] == "D ahead"
        level = {r[0]: r for r in section_rows(report(world(d_score=0.9)), "C vs D")}
        assert level["job1"][-1] == "level"
        run.drop(arm="D")
        assert section_rows(report(run), "C vs D")[0][-1] == "no data"

    def test_emails_and_owner_names_are_masked(self):
        run = Run()
        run.add("job1", "C", "sonnet", [None], failure="upstream", detail="auth failed for rep@example.com (Jane Rep)")
        text = report(run, names=("Jane Rep",))
        assert "<email>" in text and "the rep" in text
        assert "example.com" not in text and "Jane Rep" not in text
        assert ir.leaks("mail rep@example.com", ()) and ir.leaks("hello jane rep", ("Jane Rep",))
        assert ir.leaks(text, ("Jane Rep",)) == []

    def test_the_real_truth_shape_masks_through_the_owner_email_tokens(self):
        # The real truth file holds owner keys ("demo") and owner emails, no display name: the email tokens stand in.
        truth = {"meta": {"demo_owner": "demo"}, "job1": {"primary_owner": "demo", "owners": {
            "demo": {"owner_email": "casey.morgan@example.com"}}}}
        names = ir.owner_names(truth, [])
        assert names == ("casey", "morgan")
        text = report(Run(), names=names)
        assert ir.mask("Casey Morgan wrote it", names) == "the rep the rep wrote it"
        assert "Casey" not in text

    def test_apostrophe_addresses_are_masked_whole(self):
        assert ir.mask("o'brien@example.org ok") == "<email> ok"
        assert ir.leaks("o'brien@example.org", ())

    def test_name_masking_respects_word_boundaries(self):
        assert ir.mask("Caseyland and Casey", ("Casey",)) == "Caseyland and the rep"
        assert ir.leaks("Caseyland", ("Casey",)) == []

    def test_verdict_detail_shows_four_decimals(self):
        # -0.0201 is a loss, -0.0200 is a tie: three decimals would print both as -0.020
        lost = report(world({("job2", "sonnet"): {**WIN, "score": 0.7799}}))
        row = next(r for r in section_rows(lost, "Verdict detail") if r[:2] == ["job2", "sonnet"])
        assert row[3] == "0.7799" and row[4] == "-0.0201" and row[5] == "LOSS"
        tie = report(world({("job2", "sonnet"): {**WIN, "score": 0.78}}))
        row = next(r for r in section_rows(tie, "Verdict detail") if r[:2] == ["job2", "sonnet"])
        assert row[4] == "-0.0200" and row[5] == "ok"

    def test_owner_names_come_from_flags_and_name_like_truth_values(self):
        truth = {"meta": {"demo_owner": "demo", "past_due_owner": "Pat Example"}, "job1": {"primary_owner": "demo"}}
        assert ir.owner_names(truth, ["Sam Sample"]) == ("Pat Example", "Sam Sample")


# ---------------------------------------------------------------------------
# CLI


def write_run(tmp_path: Path, run: Run) -> Path:
    rd = tmp_path / "run"
    rd.mkdir()
    (rd / "sessions.jsonl").write_text("".join(json.dumps(s) + "\n" for s in run.sessions), encoding="utf-8")
    (rd / "grades.jsonl").write_text("".join(json.dumps(g) + "\n" for g in run.grades), encoding="utf-8")
    meta = {"started": "2026-11-10T08-00-00Z", "reps": 3, "claude_version": "x"}
    (rd / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return rd


class TestCli:
    def test_writes_the_report_and_prints_the_verdict(self, tmp_path, capsys):
        rd = write_run(tmp_path, world())
        truth = tmp_path / "truth.json"
        truth.write_text(json.dumps({"meta": {"as_of": "2026-11-10"}}), encoding="utf-8")
        out = tmp_path / "out" / "report.md"
        assert ir.main([str(rd), "--truth", str(truth), "--out", str(out)]) == 0
        assert capsys.readouterr().out.startswith("verdict: HOLDS")
        text = out.read_text(encoding="utf-8")
        assert text.startswith("# Intent benchmark report") and "as-of date 2026-11-10" in text

    def test_missing_grades_exit_2(self, tmp_path, capsys):
        rd = write_run(tmp_path, world())
        (rd / "grades.jsonl").unlink()
        truth = tmp_path / "truth.json"
        truth.write_text("{}", encoding="utf-8")
        assert ir.main([str(rd), "--truth", str(truth), "--out", str(tmp_path / "r.md")]) == 2
        assert "run bench/grade.py first" in capsys.readouterr().err

    def test_no_name_to_mask_refuses_to_write_inside_the_repo(self, tmp_path, capsys):
        rd = write_run(tmp_path, world())
        truth = tmp_path / "truth.json"
        truth.write_text("{}", encoding="utf-8")
        inside = ir.HERE.parent / "docs" / "never-written-by-this-test.md"
        assert ir.main([str(rd), "--truth", str(truth), "--out", str(inside)]) == 4
        assert not inside.exists() and "--mask-name" in capsys.readouterr().err
        # a name from --mask-name lifts the refusal (checked against a path outside the repo to keep the repo clean)
        outside = tmp_path / "ok.md"
        assert ir.main([str(rd), "--truth", str(truth), "--out", str(outside)]) == 0

    def test_leak_check_blocks_the_write(self, tmp_path, monkeypatch, capsys):
        rd = write_run(tmp_path, world())
        truth = tmp_path / "truth.json"
        truth.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(ir, "build_report", lambda *a, **k: ("contact rep@example.com", None))
        out = tmp_path / "r.md"
        assert ir.main([str(rd), "--truth", str(truth), "--out", str(out)]) == 3
        assert not out.exists() and "an email address" in capsys.readouterr().err


@pytest.mark.skipif(
    not (REAL_TRUTH.exists() and (REAL_RUN / "grades.jsonl").exists()), reason="local smoke run and truth file absent"
)
def test_real_smoke_run_renders_without_emails(tmp_path):
    out = tmp_path / "report.md"
    assert ir.main([str(REAL_RUN), "--truth", str(REAL_TRUTH), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert not ir.EMAIL_RE.search(text)
    for heading in ("## Verdict", "## Per-job results", "## Write safety", "## Failures by class", "## C vs D"):
        assert heading in text
