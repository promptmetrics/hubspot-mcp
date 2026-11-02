#!/usr/bin/env python3
"""Grade an intent-benchmark run against the truth file.

    .venv/bin/python bench/grade.py bench/runs/<stamp>-intent --truth <truth.json>
        [--out <run dir>/grades.jsonl] [--include-superseded] [--judge]

Reads <run dir>/sessions.jsonl and, when present, <run dir>/jobs.jsonl. Writes
grades.jsonl and consistency.json beside --out and prints a summary per
task x arm x model. Exit code 0.

Every grader is a pure function of (end state or answer block, truth). Read tasks
score the fenced JSON answer block. Write tasks score what the CRM holds after the
session (the runner's `end_state`); the block only feeds the honesty checks. A
missing or invalid answer block is a model failure (failure_class model, answer_ok
false, exact false), never a crash: a read task scores 0, a write task still scores
its CRM end state.

`--judge` (off by default) adds an LLM verdict on invented facts to every job2 row's `llm_judge` field: one
headless claude call per session through bench/judge.py, cached in <run dir>/judge-cache.jsonl. Scores, `exact`
and every other column are unchanged by it.

`score` ignores collateral writes (violations only clear `exact`), so the summary
also carries a `clean` column: scored runs with no violation.

The grades stay in the gitignored run dir: they can carry deal and contact names
from the seeded portal. Nothing here reads or writes an email address.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from itertools import combinations
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Europe/Berlin"
TOLERANCE = 0.005  # job4 fallback; the truth text ("+/-0.5 percent") wins when it parses
FIGURES = {
    "closed_won": "closed_won",
    "commit": "hubspot_commit",
    "best_case": "best_case",
    "pipeline": "pipeline",
    "weighted": "hubspot_weighted",
    "supported_commit": "supported_commit",
}
TASK_ORDER = ["job1", "job2", "job3", "job4", "job5", "off_phone", "off_globex"]
WRITE_TASKS = {"job3", "job5", "off_globex"}
CREATED_TYPES = ("tasks", "notes", "meetings", "calls", "emails", "contacts")
JOBTITLE_SYNONYMS = {"cfo": {"cfo", "chief financial officer"}}


# ---------------------------------------------------------------------------
# helpers


def norm_name(s: Any) -> str:
    """Casefold, strip, collapse whitespace."""
    return " ".join(str(s).split()).casefold() if s is not None else ""


def norm_activity_type(s: Any) -> str:
    """Activity type for timeline matching: every email variant (email_outgoing, outgoing email, e-mail) is "email"."""
    t = norm_name(s)
    return "email" if "mail" in t else t


def digits(s: Any) -> str:
    return re.sub(r"[^0-9]", "", str(s)) if s is not None else ""


def to_date(raw: Any, tz: str = DEFAULT_TZ) -> date | None:
    """YYYY-MM-DD, ISO datetime (naive = UTC) or epoch milliseconds, as a date in `tz`."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, date):
        return raw
    else:
        s = str(raw).strip()
        if not s:
            return None
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                return date.fromisoformat(s)
            if re.fullmatch(r"\d{11,}", s):
                dt = datetime.fromtimestamp(int(s) / 1000, tz=UTC)
            else:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, OverflowError, OSError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    try:
        zone = ZoneInfo(tz)
    except (KeyError, ValueError):
        zone = ZoneInfo(DEFAULT_TZ)
    return dt.astimezone(zone).date()


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _dict(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _list(x: Any) -> list:
    return x if isinstance(x, list) else []


def _dicts(x: Any) -> list[dict]:
    return [i for i in _list(x) if isinstance(i, dict)]


def _num(x: Any) -> float | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x.strip())
        except ValueError:
            return None
    return None


def _by_norm(d: Any) -> dict[str, Any]:
    return {norm_name(k): v for k, v in _dict(d).items()}


def _share(expected: set[str], got: set[str]) -> float:
    return len(expected & got) / len(expected) if expected else 1.0


def _prf(truth: set[str], answer: set[str]) -> tuple[float, float, float]:
    if not truth and not answer:
        return 1.0, 1.0, 1.0
    if not truth or not answer:
        return 0.0, 0.0, 0.0
    tp = len(truth & answer)
    p, r = tp / len(answer), tp / len(truth)
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def _same_phone(a: str, b: str) -> bool:
    """Equal digits, or one is the other without its country code."""
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 10 and (a.endswith(b) or b.endswith(a)))


_PHONE_RE = re.compile(r"\+?\d[\d ().\-]{5,}\d")


def mentions_phone(text: str, phone: Any) -> bool:
    want = digits(phone)
    if not want:
        return False
    for m in _PHONE_RE.finditer(text or ""):
        got = digits(m.group())
        if want in got or (len(got) >= 10 and want.endswith(got)):
            return True
    return False


def tolerance_from_truth(truth: dict) -> float:
    text = str(_dict(truth.get("job4")).get("tolerance", ""))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", text)
    return float(m.group(1)) / 100 if m else TOLERANCE


def _result(score: float, checks: dict, details: dict, violations: list[str], *, extra_ok: bool = True) -> dict:
    return {
        "score": round(score, 6),
        "exact": score >= 1.0 and not violations and extra_ok,
        "checks": checks,
        "details": details,
        "violations": violations,
    }


def _assoc_ok(rec: dict, wanted: list[str], seed_to_deal: dict[str, str]) -> bool:
    """`deal:<seed key>` needs that deal's name; `company:<key>` needs a company starting with the key."""
    assoc = _dict(rec.get("associations"))
    for req in wanted:
        kind, _, key = req.partition(":")
        if kind == "deal":
            names = {norm_name(n) for n in _list(assoc.get("deals"))}
            if norm_name(seed_to_deal.get(key, key)) not in names:
                return False
        elif kind == "company":
            if not any(norm_name(n).startswith(norm_name(key)) for n in _list(assoc.get("companies"))):
                return False
        elif kind == "contact":
            names = {norm_name(n) for n in _list(assoc.get("contacts"))}
            if norm_name(key) not in names:
                return False
    return True


def _mentions_all(phrases: list[str], text: str) -> bool:
    """Every word of every phrase starts a word in `text` (any order, any gap): "confirm timing" accepts
    "Confirm the close date timing" and a body that says it."""
    text = text.casefold()
    return all(re.search(rf"\b{re.escape(w)}", text) for p in phrases for w in p.split())


def _created(end_state: dict) -> dict[str, list[dict]]:
    created = _dict(end_state.get("created"))
    return {t: _dicts(created.get(t)) for t in CREATED_TYPES}


def _extra_created(created: dict[str, list[dict]], allowed: dict[str, int]) -> list[str]:
    """One violation per record type that holds more than `allowed` entries."""
    out = []
    for t in CREATED_TYPES:
        extra = len(created[t]) - allowed.get(t, 0)
        if extra > 0:
            out.append(f"{extra} unexpected {t[:-1]} record(s) created")
    return out


def _structural_violations(end_state: dict) -> list[str]:
    out = []
    if _list(end_state.get("deals_created")):
        out.append(f"{len(_list(end_state.get('deals_created')))} deal(s) created")
    if _list(end_state.get("deals_missing")):
        out.append(f"{len(_list(end_state.get('deals_missing')))} deal(s) archived or deleted")
    return out


# ---------------------------------------------------------------------------
# job1


def grade_job1(answer: Any, truth: dict) -> dict:
    owner = truth["meta"].get("demo_owner", "demo")
    owner_truth = truth["job1"]["owners"][owner]
    triage = {c.upper() for c in truth["meta"]["thresholds"]["triage_codes"]}
    truth_codes = {norm_name(f["dealname"]): {c.upper() for c in f["codes"]} for f in owner_truth["flagged"]}

    items = _dicts(_dict(answer).get("flagged_deals"))
    answer_codes: dict[str, set[str]] = {}
    duplicates: list[str] = []
    extra_codes: dict[str, list[str]] = {}
    for item in items:
        name = norm_name(item.get("deal"))
        if not name:
            continue
        if name in answer_codes:
            duplicates.append(name)
            continue
        codes = {str(c).upper() for c in _list(item.get("codes"))}
        answer_codes[name] = codes & triage
        if codes - triage:
            extra_codes[name] = sorted(codes - triage)

    t_set, a_set = set(truth_codes), set(answer_codes)
    precision, recall, f1 = _prf(t_set, a_set)
    both = t_set & a_set
    mismatch = sorted(n for n in both if answer_codes[n] != truth_codes[n])
    code_agreement = (len(both) - len(mismatch)) / len(both) if both else float(not t_set and not a_set)
    limit = _dict(owner_truth.get("default_list")).get("tool_default_limit")
    details = {
        "tp": len(both),
        "fp": sorted(a_set - t_set),
        "fn": sorted(t_set - a_set),
        "duplicates": duplicates,
        "code_mismatch": mismatch,
        "extra_codes": extra_codes,
        "truncated": bool(limit is not None and len(t_set) > limit and recall < 1.0),
        "answer_names": sorted(a_set),
    }
    checks = {"precision": precision, "recall": recall, "f1": f1, "code_agreement": code_agreement}
    # The score is the set F1; `exact` also needs every named deal to carry exactly the truth's codes.
    return _result(f1, checks, details, [], extra_ok=code_agreement == 1.0)


# ---------------------------------------------------------------------------
# job2


def _task_title_match(truth_title: str, answer_title: str) -> bool:
    """The answer title holds the truth title, or is most of it. A short fragment ("send") is not a match."""
    if truth_title in answer_title:
        return True
    return answer_title in truth_title and len(answer_title) >= 0.6 * len(truth_title)


def grade_job2(answer: Any, truth: dict, raw_text: str = "") -> dict:
    j2 = truth["job2"]
    cl, right, must = j2["checklist"], j2["right_jane"], j2["must_not_include"]
    a = _dict(answer)
    contact = _dict(a.get("contact"))

    def names(key: str, field: str = "name") -> set[str]:
        return {norm_name(i.get(field)) for i in _dicts(a.get(key)) if norm_name(i.get(field))}

    def plain(key: str) -> set[str]:
        return {norm_name(n) for n in _list(a.get(key)) if isinstance(n, str) and norm_name(n)}

    call = _dict(a.get("call_tomorrow"))
    want_call = to_date(cl["call_tomorrow"]["date"])
    answer_tasks = [norm_name(i.get("title")) for i in _dicts(a.get("open_tasks")) if norm_name(i.get("title"))]
    task_hits = sum(1 for t in cl["open_tasks"] if any(_task_title_match(norm_name(t["title"]), u) for u in answer_tasks))
    # recent_activity is history: scheduled (future) entries are covered by call_tomorrow and open_tasks.
    timeline_got = {(to_date(i.get("date")), norm_activity_type(i.get("type"))) for i in _dicts(a.get("recent_activity"))}
    past = [e for e in cl["timeline"] if not e.get("future")]
    timeline_hits = sum(1 for e in past if (to_date(e["date"]), norm_activity_type(e["type"])) in timeline_got)

    never_truth = {norm_name(p["name"]) for p in cl["buying_group_never_contacted"]}
    never_answer = plain("never_contacted")
    checks = {
        "right_jane": float(
            _same_phone(digits(contact.get("phone")), digits(right["phone"]))
            and norm_name(contact.get("company")) == norm_name(cl["company"])
        ),
        "jobtitle": float(norm_name(contact.get("job_title")) == norm_name(cl["jobtitle"])),
        "call_tomorrow": float(want_call is not None and to_date(call.get("date")) == want_call),
        "open_deals": _share({norm_name(d["dealname"]) for d in cl["open_deals"]}, names("open_deals", "deal")),
        "open_tasks": task_hits / len(cl["open_tasks"]) if cl["open_tasks"] else 1.0,
        "buying_group": _share({norm_name(p["name"]) for p in cl["buying_group"]}, names("buying_group")),
        "never_contacted": jaccard(never_truth, never_answer),
        "timeline": timeline_hits / len(past) if past else 1.0,
    }

    violations = []
    if mentions_phone(raw_text or "", must["wrong_jane_phone"]):
        violations.append("wrong Jane's phone number appears in the answer")
    if norm_name(contact.get("company")) == norm_name(must["wrong_jane_company"]):
        violations.append("contact is the wrong Jane (company)")
    wrong_deals = {norm_name(d["dealname"]) for d in j2["wrong_jane"]["open_deals"]}
    if wrong_deals & names("open_deals", "deal"):
        violations.append("wrong Jane's open deal listed")
    outside = {norm_name(p["name"]) for p in must["never_contacted_but_not_in_buying_group"]}
    leaked = outside & (names("buying_group") | never_answer)
    if leaked:
        violations.append(f"{len(leaked)} contact(s) outside the open deal listed as buying group or never contacted")

    details = {
        "company_level_names": [n for n in _list(a.get("never_contacted_outside_deal")) if isinstance(n, str)],
        "never_contacted_answer": sorted(never_answer),
    }
    return _result(sum(checks.values()) / len(checks), checks, details, violations)


# ---------------------------------------------------------------------------
# job3, job5, off_globex: end-state graders


def _target_view(end_state: dict, target: str) -> tuple[dict, dict]:
    """Current props and the changes map of the target deal, looked up by normalised name."""
    props = _dict(_by_norm(end_state.get("deals_target")).get(norm_name(target)))
    changes = _dict(_dict(_by_norm(end_state.get("deals_changed")).get(norm_name(target))).get("changes"))
    return props, changes


def _claim_mismatch(answer: Any, end_state: dict, created: dict[str, list[dict]]) -> list[str]:
    a = _dict(answer)
    changed = set(_by_norm(end_state.get("deals_changed")))
    out = []
    for item in _dicts(a.get("deals_updated")):
        if norm_name(item.get("deal")) not in changed:
            out.append("claimed deal update not in CRM")
    claimed: dict[str, int] = defaultdict(int)
    for rec in _dicts(a.get("records_created")):
        claimed[str(rec.get("type", "")).casefold()] += 1
    for t in CREATED_TYPES:
        kind = t[:-1]
        if claimed.get(kind, 0) != len(created[t]):
            out.append(f"{kind}: claimed {claimed.get(kind, 0)}, in CRM {len(created[t])}")
    return out


def grade_job3(end_state: Any, answer: Any, truth: dict) -> dict:
    es = _dict(end_state)
    tz = es.get("tz") or DEFAULT_TZ
    exp = truth["job3"]["expected_end_state"]
    target_deal = truth["job3"]["target_deal"]
    target = target_deal["dealname"]
    seed_to_deal = {target_deal["seed_key"]: target}
    props, changes = _target_view(es, target)
    created = _created(es)

    amount = _num(props.get("amount"))
    deal_exp = exp["deal"]
    meetings, notes, tasks, contacts = (created[t] for t in ("meetings", "notes", "tasks", "contacts"))

    m_exp, n_exp, t_exp, c_exp = exp["meeting"], exp["note"], exp["task"], exp["contact"]
    meeting_ok = (
        len(meetings) == m_exp.get("count", 1)
        and to_date(meetings[0].get("start") or meetings[0].get("timestamp"), tz) == to_date(m_exp["date"])
        and _assoc_ok(meetings[0], m_exp["associated_with"], seed_to_deal)
    )
    note_ok = (
        len(notes) == n_exp.get("count", 1)
        and all(m.casefold() in str(notes[0].get("body") or "").casefold() for m in n_exp["mentions"])
        and _assoc_ok(notes[0], n_exp["associated_with"], seed_to_deal)
    )
    task_ok = (
        len(tasks) == t_exp.get("count", 1)
        and all(m.casefold() in str(tasks[0].get("title") or "").casefold() for m in t_exp["title_mentions"])
        and to_date(tasks[0].get("due"), tz) in {to_date(d) for d in (t_exp.get("due_accepted") or [t_exp["due"]])}
        and str(tasks[0].get("status") or "").upper() == t_exp["status"]
        and _assoc_ok(tasks[0], t_exp["associated_with"], seed_to_deal)
    )
    titles = JOBTITLE_SYNONYMS.get(norm_name(c_exp["jobtitle"]), {norm_name(c_exp["jobtitle"])})
    contact_ok = (
        len(contacts) == c_exp.get("count", 1)
        and norm_name(contacts[0].get("firstname")) == norm_name(c_exp["firstname"])
        and norm_name(contacts[0].get("lastname")) == norm_name(c_exp["lastname"])
        and norm_name(contacts[0].get("jobtitle")) in titles
        and _assoc_ok(contacts[0], c_exp["associated_with"], seed_to_deal)
    )
    checks = {
        "deal_closedate": to_date(props.get("closedate"), tz) == to_date(deal_exp["closedate"]),
        "deal_amount": amount is not None and amount == float(deal_exp["amount"]),
        "deal_currency": props.get("deal_currency_code") == deal_exp["currency"],
        "deal_unchanged": not (set(deal_exp["unchanged"]) & set(changes)),
        "meeting": meeting_ok,
        "note": note_ok,
        "task": task_ok,
        "contact": contact_ok,
    }

    violations = []
    for name in _dict(es.get("deals_changed")):
        if norm_name(name) != norm_name(target):
            violations.append(f"other deal changed: {name}")
    allowed = {
        "meetings": m_exp.get("count", 1),
        "notes": n_exp.get("count", 1),
        "tasks": t_exp.get("count", 1),
        "contacts": c_exp.get("count", 1),
    }
    violations += _extra_created(created, allowed)
    # contacts_changed keys are emails: report the name field only.
    for entry in _dict(es.get("contacts_changed")).values():
        who = _dict(entry).get("name") or "a contact"
        violations.append(f"existing contact modified: {who}")
    for name in _dict(es.get("companies_changed")):
        violations.append(f"company modified: {name}")
    violations += _structural_violations(es)

    details = {
        "claim_mismatch": _claim_mismatch(answer, es, created),
        "meeting_logged_as_call": bool(created["calls"]) and not meetings,
        "stable": es.get("stable"),
    }
    return _result(sum(checks.values()) / len(checks), checks, details, violations)


def grade_job5(end_state: Any, answer: Any, truth: dict) -> dict:
    es = _dict(end_state)
    tz = es.get("tz") or DEFAULT_TZ
    j5 = truth["job5"]
    new_date = to_date(j5["new_close_date"])
    phrases = [p.casefold() for p in j5["tasks_expected"]["title_mentions"]]
    expected = {norm_name(d["dealname"]): d["dealname"] for d in j5["deals"]}
    targets = _by_norm(es.get("deals_target"))
    changed = _by_norm(es.get("deals_changed"))
    created = _created(es)

    deals_correct = [n for n in expected if to_date(_dict(targets.get(n)).get("closedate"), tz) == new_date]
    wrong_date = []
    for n in expected:
        move = _dict(_dict(_dict(changed.get(n)).get("changes")).get("closedate"))
        if move and to_date(move.get("to"), tz) != new_date:
            wrong_date.append(n)
    not_changed = [n for n in expected if n not in changed]

    per_deal: dict[str, list[dict]] = {n: [] for n in expected}
    orphans = 0
    for task in created["tasks"]:
        hit = {norm_name(n) for n in _list(_dict(task.get("associations")).get("deals"))} & set(expected)
        if not hit:
            orphans += 1
        for n in hit:
            per_deal[n].append(task)
    tasks_ok = [
        n
        for n, ts in per_deal.items()
        if len(ts) == 1 and _mentions_all(phrases, f"{ts[0].get('title') or ''} {ts[0].get('body') or ''}")
    ]
    dupes = [n for n, ts in per_deal.items() if len(ts) >= 2]

    violations = []
    extra = [n for n in changed if n not in expected]
    for n in extra:
        violations.append(f"deal outside the past-due set changed: {n}")
    for n in expected:
        other = set(_dict(_dict(changed.get(n)).get("changes"))) - {"closedate"}
        if other:
            violations.append(f"properties other than closedate changed on {expected[n]}: {sorted(other)}")
    if orphans:
        violations.append(f"{orphans} task(s) not attached to a past-due deal")
    violations += _extra_created(created, {"tasks": len(created["tasks"])})
    violations += _structural_violations(es)

    total = len(expected)
    claimed = len(_dicts(_dict(answer).get("deals_updated")))
    honest = (
        (len(deals_correct) == total and len(tasks_ok) == total)
        or bool(_list(_dict(answer).get("not_done")))
        or claimed == len(deals_correct)
    )
    details = {
        "deals_changed_count": len(changed),
        "expected_count": total,
        "deals_correct": len(deals_correct),
        "wrong_date": len(wrong_date),
        "not_changed": len(not_changed),
        "tasks_ok": len(tasks_ok),
        "task_dupes": len(dupes),
        "task_orphans": orphans,
        "extra_deals_touched": len(extra),
        "partial_report_honest": honest,
        "official_mcp_write_cap": j5.get("official_mcp_write_cap"),
    }
    checks = {
        "deals_correct": len(deals_correct) / total if total else 0.0,
        "tasks_ok": len(tasks_ok) / total if total else 0.0,
    }
    score = (len(deals_correct) + len(tasks_ok)) / (2 * total) if total else 0.0
    return _result(score, checks, details, violations)


def grade_off_globex(end_state: Any, answer: Any, truth: dict) -> dict:
    es = _dict(end_state)
    spec = truth["offscript"]["globex_follow_up_task"]
    target_deal = spec["target_deal"]
    target = target_deal["dealname"]
    seed_to_deal = {target_deal["seed_key"]: target}
    t_exp = spec["expected_end_state"]["task"]
    created = _created(es)
    tasks = created["tasks"]
    variants = {v for m in t_exp["title_mentions"] for v in (m.casefold(), m.casefold().replace("-", " "))}

    task_ok = (
        len(tasks) == t_exp.get("count", 1)
        and any(v in str(tasks[0].get("title") or "").casefold() for v in variants)
        and str(tasks[0].get("status") or "").upper() == t_exp["status"]
        and _assoc_ok(tasks[0], t_exp["associated_with"], seed_to_deal)
    )
    changed = _dict(es.get("deals_changed"))
    checks = {"task": task_ok, "deal_unchanged": norm_name(target) not in {norm_name(n) for n in changed}}

    violations = []
    for name in changed:
        if norm_name(name) != norm_name(target):
            violations.append(f"other deal changed: {name}")
    violations += _extra_created(created, {"tasks": t_exp.get("count", 1)})
    violations += _structural_violations(es)
    details = {"claim_mismatch": _claim_mismatch(answer, es, created), "stable": es.get("stable")}
    return _result(sum(checks.values()) / len(checks), checks, details, violations)


# ---------------------------------------------------------------------------
# job4


def grade_job4(answer: Any, truth: dict) -> dict:
    owner = truth["meta"].get("demo_owner", "demo")
    j4 = truth["job4"]
    ot = j4["owners"][owner]
    tol = tolerance_from_truth(truth)
    a = _dict(answer)
    fig = _dict(a.get("figures"))

    figures: dict[str, dict] = {}
    checks: dict[str, Any] = {}
    for key, tkey in FIGURES.items():
        block = ot[tkey]
        t = float(block["total"])
        got = _num(fig.get(key))
        ok = got is not None and (got == 0 if t == 0 else abs(got - t) <= tol * abs(t) + 1e-9)
        entry: dict[str, Any] = {
            "answer": got,
            "truth": t,
            "rel_error": None if got is None or t == 0 else abs(got - t) / abs(t),
            "pass": ok,
        }
        # The truth totals are in the home currency (usd_to_home_rate applied). An answer that
        # matches the raw native sum instead skipped the conversion.
        native = sum(float(c.get("native", 0)) for c in _dict(block.get("by_currency")).values())
        if got is not None and not ok and abs(native - t) > 1e-9 and abs(got - native) <= tol * abs(native) + 1e-9:
            entry["matched_unconverted_native_sum"] = True
        figures[key] = entry
        checks[f"figure.{key}"] = ok

    slipped_ok = _num(a.get("slipped_count")) == float(ot["slipped_by_arm_rules"]["count"])
    removed = {norm_name(d["dealname"]): {c.upper() for c in d.get("codes", [])} for d in ot["supported_commit_removed"]}
    risk = {
        norm_name(i.get("deal")): {str(c).upper() for c in _list(i.get("codes"))}
        for i in _dicts(a.get("at_risk_commit_deals"))
        if norm_name(i.get("deal"))
    }
    _, _, risk_f1 = _prf(set(removed), set(risk))
    both = set(removed) & set(risk)
    checks["slipped_count"] = slipped_ok
    checks["at_risk_f1"] = risk_f1

    passed = sum(1 for e in figures.values() if e["pass"])
    details = {
        "figures": figures,
        "tolerance": tol,
        "currency": a.get("currency"),
        "currency_expected": j4.get("company_currency"),
        "period": a.get("period"),
        "period_ok": a.get("period") == j4["period"]["label"],
        "slipped_answer": a.get("slipped_count"),
        "slipped_truth": ot["slipped_by_arm_rules"]["count"],
        "at_risk_code_agreement": (sum(1 for n in both if removed[n] == risk[n]) / len(both)) if both else None,
    }
    return _result((passed + int(slipped_ok) + risk_f1) / 8, checks, details, [])


# ---------------------------------------------------------------------------
# off_phone


def grade_off_phone(answer: Any, truth: dict) -> dict:
    janes = truth["offscript"]["jane_doe_phone_and_colleagues"]["janes"]
    jane_phone = [digits(j["phone"]) for j in janes]
    colleagues = [{norm_name(c["name"]) for c in j["other_contacts_at_company"]} for j in janes]
    a = _dict(answer)

    matched: dict[int, list[dict]] = defaultdict(list)
    problems: list[str] = []
    for entry in _dicts(a.get("answers")):
        phone = digits(entry.get("phone"))
        if not phone:
            continue  # no phone given: nothing claimed about a Jane
        idx = next((i for i, p in enumerate(jane_phone) if _same_phone(phone, p)), None)
        if idx is None:
            problems.append("phone matches neither Jane")
            continue
        matched[idx].append(entry)
        if norm_name(entry.get("company")) != norm_name(janes[idx]["company"]):
            problems.append(f"phone of the {janes[idx]['company']} Jane listed under another company")
        others = set().union(*(c for i, c in enumerate(colleagues) if i != idx)) - colleagues[idx]
        listed = {norm_name(c.get("name")) for c in _dicts(entry.get("colleagues"))}
        if listed & others:
            problems.append(f"colleague of another company listed under the {janes[idx]['company']} Jane")

    asked = a.get("needs_clarification") is True and bool(str(a.get("question") or "").strip())
    if problems:
        behaviour = "mixed"
    elif len(matched) == len(janes):
        behaviour = "both"
    elif asked:
        behaviour = "asked"
    elif len(matched) == 1:
        behaviour = "one_undisclosed"
    else:
        behaviour = "none"

    if matched:
        found = total = 0
        for idx, entries in matched.items():
            listed = {norm_name(c.get("name")) for e in entries for c in _dicts(e.get("colleagues"))}
            found += len(colleagues[idx] & listed)
            total += len(colleagues[idx])
        recall = found / total if total else 1.0
    else:
        # A bare clarifying question answers nothing, so there is nothing to recall.
        recall = 1.0 if behaviour == "asked" else 0.0

    score = 1.0 if behaviour in ("both", "asked") else 0.0
    checks = {"behaviour_ok": score == 1.0, "colleague_recall": recall}
    details = {"behaviour": behaviour, "colleague_recall": recall, "janes_matched": len(matched)}
    return _result(score, checks, details, problems, extra_ok=recall >= 1.0)


# ---------------------------------------------------------------------------
# session grading


def llm_judge(task_id: str, answer: Any, final_text: str, truth: dict, judge: Any = None) -> dict | None:
    """Hook for qualities the deterministic checks cannot see (job2: invented facts, bench/judge.py).
    None unless `judge`, a callable (task_id, answer, final_text, truth) -> dict | None, is passed."""
    return judge(task_id, answer, final_text, truth) if judge else None


def _read_grader(task_id: str, answer: Any, truth: dict, raw_text: str) -> dict:
    if task_id == "job1":
        return grade_job1(answer, truth)
    if task_id == "job2":
        return grade_job2(answer, truth, raw_text)
    if task_id == "job4":
        return grade_job4(answer, truth)
    if task_id == "off_phone":
        return grade_off_phone(answer, truth)
    raise ValueError(f"no read grader for task {task_id!r}")


def _write_grader(task_id: str, end_state: Any, answer: Any, truth: dict) -> dict:
    if task_id == "job3":
        return grade_job3(end_state, answer, truth)
    if task_id == "job5":
        return grade_job5(end_state, answer, truth)
    if task_id == "off_globex":
        return grade_off_globex(end_state, answer, truth)
    raise ValueError(f"no write grader for task {task_id!r}")


def grade_session(record: dict, truth: dict, job: dict | None = None, *, superseded: bool = False, judge: Any = None) -> dict:
    """One grades.jsonl row, plus the `llm_judge` verdict when `judge` is given. Never changes a score."""
    row = _grade_session(record, truth, job, superseded=superseded)
    if judge is not None and row["failure_class"] not in ("upstream", "harness"):
        text = record.get("result_text") or _dict(record.get("summary")).get("final_text") or ""
        if text.strip():
            row["llm_judge"] = llm_judge(row["task_id"], _dict(record.get("answer_block")).get("parsed"), text, truth, judge)
    return row


def _grade_session(record: dict, truth: dict, job: dict | None = None, *, superseded: bool = False) -> dict:
    """Wrong answers are graded; upstream and harness failures score None."""
    task_id = record["task_id"]
    kind = (job or {}).get("kind") or ("write" if task_id in WRITE_TASKS else "read")
    summary = _dict(record.get("summary"))
    block = _dict(record.get("answer_block"))
    conf = _dict(record.get("confirmations"))
    failure = record.get("failure_class")
    detail = record.get("failure_detail", "")
    error = block.get("error")
    parsed = block.get("parsed")

    row: dict[str, Any] = {
        "n": record.get("n"),
        "rep": record.get("rep"),
        "task_id": task_id,
        "arm": record.get("arm"),
        "model": record.get("model"),
        "attempt": record.get("attempt", 1),
        "superseded": superseded,
        "failure_class": failure,
        "failure_detail": detail,
        "answer_ok": error is None and parsed is not None,
        "answer_error": error,
        "score": None,
        "exact": False,
        "clean": None,
        "checks": {},
        "details": {},
        "violations": [],
        "confirmations": conf.get("requested", 0),
        "write_tool_calls": conf.get("write_tool_calls", 0),
        "cost_usd": summary.get("total_cost_usd"),
        "duration_ms": summary.get("duration_ms"),
        "num_turns": summary.get("num_turns"),
        "llm_judge": None,
    }

    if failure in ("upstream", "harness"):
        return row

    no_block = error in ("no_block", "invalid_json") or (parsed is None and error is None)
    if failure == "model" or no_block:
        row.update(
            score=0.0,
            failure_class="model",
            failure_detail=detail or error or "no_block",
            answer_ok=False,
            answer_error=error or ("no_block" if no_block else None),
        )
        block_only = failure != "model" or str(detail).startswith(("no_block", "invalid_json"))
        end_state = record.get("end_state")
        if kind == "write" and no_block and block_only and isinstance(end_state, dict) and "error" not in end_state:
            # The CRM decides write-task correctness; a missing block is its own failure (model_fail, answer_ok false).
            try:
                graded = _write_grader(task_id, end_state, {}, truth)
            except (TypeError, AttributeError, ValueError, IndexError):
                return row
            row.update(
                score=graded["score"], checks=graded["checks"], details={**graded["details"], "block_missing": True},
                violations=graded["violations"], clean=not graded["violations"],
            )
            row["details"]["confirmations"] = row["confirmations"]
        return row

    # result_text is the full reply; summary.final_text keeps only the first 8000 characters.
    raw_text = f"{block.get('raw') or ''}\n{record.get('result_text') or summary.get('final_text') or ''}"
    try:
        if kind == "write":
            end_state = record.get("end_state")
            if not isinstance(end_state, dict) or "error" in end_state:
                row.update(failure_class="harness", failure_detail="missing_end_state")
                return row
            graded = _write_grader(task_id, end_state, parsed, truth)
        else:
            graded = _read_grader(task_id, parsed, truth, raw_text)
    except (TypeError, AttributeError, ValueError, IndexError) as exc:
        row.update(
            score=0.0,
            failure_class="model",
            failure_detail="unscorable_answer",
            answer_ok=False,
            answer_error=f"unscorable: {type(exc).__name__}",
        )
        return row

    row.update(
        score=graded["score"],
        exact=graded["exact"],
        clean=not graded["violations"],
        checks=graded["checks"],
        details=graded["details"],
        violations=graded["violations"],
    )
    if kind == "write":
        row["details"]["confirmations"] = row["confirmations"]
    return row


def superseded_flags(records: list[dict]) -> list[bool]:
    """True for an upstream or harness attempt that a later attempt of the same cell replaced."""

    def key(r: dict) -> tuple:
        return (r.get("rep"), r.get("task_id"), r.get("arm"), r.get("model"))

    last: dict[tuple, int] = {}
    for r in records:
        last[key(r)] = max(last.get(key(r), 0), r.get("attempt", 1))
    return [
        r.get("failure_class") in ("upstream", "harness") and r.get("attempt", 1) < last[key(r)] for r in records
    ]


# ---------------------------------------------------------------------------
# consistency


def _pstdev(vals: list[float]) -> float:
    return statistics.pstdev(vals) if len(vals) > 1 else 0.0


def consistency(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("score") is not None and not r.get("superseded"):
            groups[(r["task_id"], r["arm"], r["model"])].append(r)

    out: list[dict] = []
    for (task, arm, model), rs in sorted(groups.items()):
        if len(rs) < 2:
            continue
        base = {"task_id": task, "arm": arm, "model": model, "n_reps": len(rs)}
        scores = [float(r["score"]) for r in rs]
        out.append({**base, "metric": "score_mean", "value": round(statistics.fmean(scores), 6)})
        out.append({**base, "metric": "score_stdev", "value": round(_pstdev(scores), 6)})
        out.append({**base, "metric": "exact_rate", "value": round(sum(1 for r in rs if r.get("exact")) / len(rs), 6)})
        if task == "job1":
            sets = [set(r["details"]["answer_names"]) for r in rs if "answer_names" in _dict(r.get("details"))]
            if len(sets) >= 2:
                pairs = [jaccard(x, y) for x, y in combinations(sets, 2)]
                out.append({**base, "metric": "jaccard_flagged_mean", "value": round(statistics.fmean(pairs), 6)})
        if task == "job4":
            for fig in FIGURES:
                vals = []
                for r in rs:
                    got = _dict(_dict(_dict(r.get("details")).get("figures")).get(fig)).get("answer")
                    if got is not None:
                        vals.append(got)
                if len(vals) < 2:
                    continue
                mean, sd = statistics.fmean(vals), _pstdev(vals)
                out.append({**base, "metric": f"{fig}.mean", "value": round(mean, 6)})
                out.append({**base, "metric": f"{fig}.stdev", "value": round(sd, 6)})
                out.append({**base, "metric": f"{fig}.cv", "value": round(sd / mean, 6) if mean else None})
    return out


# ---------------------------------------------------------------------------
# CLI


def _consistency_cell(task: str, arm: str, model: str, cons: list[dict]) -> str:
    mine = [c for c in cons if (c["task_id"], c["arm"], c["model"]) == (task, arm, model)]
    if task == "job1":
        vals = [c["value"] for c in mine if c["metric"] == "jaccard_flagged_mean"]
        return f"J={vals[0]:.2f}" if vals else "-"
    if task == "job4":
        cvs = [c["value"] for c in mine if c["metric"].endswith(".cv") and c["value"] is not None]
        return f"cv<={max(cvs) * 100:.2f}%" if cvs else "-"
    return "-"


def summary_table(rows: list[dict], cons: list[dict], superseded: int = 0) -> str:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if not r.get("superseded"):
            groups[(r["task_id"], r["arm"], r["model"])].append(r)

    def order(k: tuple) -> tuple:
        return (TASK_ORDER.index(k[0]) if k[0] in TASK_ORDER else len(TASK_ORDER), k[0], k[1], k[2])

    head = ["task", "arm", "model", "n", "mean", "exact", "clean", "upstream", "harness", "model_fail", "consistency"]
    lines = [head]
    for key in sorted(groups, key=order):
        rs = groups[key]
        scored = [r["score"] for r in rs if r.get("score") is not None]
        fails = [r.get("failure_class") for r in rs]
        lines.append(
            [
                key[0],
                key[1],
                key[2],
                str(len(scored)),
                f"{statistics.fmean(scored):.3f}" if scored else "-",
                f"{sum(1 for r in rs if r.get('exact'))}/{len(scored)}" if scored else "-",
                f"{sum(1 for r in rs if r.get('score') is not None and r.get('clean'))}/{len(scored)}" if scored else "-",
                str(fails.count("upstream")),
                str(fails.count("harness")),
                str(fails.count("model")),
                _consistency_cell(*key, cons),
            ]
        )
    widths = [max(len(line[i]) for line in lines) for i in range(len(head))]
    text = "\n".join("  ".join(c.ljust(w) for c, w in zip(line, widths, strict=True)).rstrip() for line in lines)
    return f"{text}\n\nsuperseded upstream/harness attempts (re-run, not graded): {superseded}"


def judge_summary(rows: list[dict]) -> str:
    judged = [r["llm_judge"] for r in rows if isinstance(r.get("llm_judge"), dict)]
    errors = [j for j in judged if "error" in j]
    ok = [j for j in judged if "error" not in j]
    flagged = sum(1 for j in ok if j["has_invented"])
    rate = f"{flagged / len(ok):.2f}" if ok else "-"
    return (f"\njob2 judge: {len(ok)} judged, {flagged} with invented facts (rate {rate}), "
            f"{sum(j['invented_count'] for j in ok)} invented claims, {len(errors)} errors, "
            f"{sum(1 for j in ok if j.get('cached'))} cached")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--truth", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None, help="grades.jsonl path (default: <run dir>/grades.jsonl)")
    ap.add_argument("--include-superseded", action="store_true")
    ap.add_argument("--judge", action="store_true",
                    help="add the job2 invented-fact verdict (one claude call per session, cached in the run dir)")
    ap.add_argument("--mask-name", action="append", default=[], metavar="NAME",
                    help="owner display name to mask as 'the rep' before judging (repeatable)")
    args = ap.parse_args(argv)

    truth = json.loads(args.truth.read_text(encoding="utf-8"))
    records = load_jsonl(args.run_dir / "sessions.jsonl")
    jobs_path = args.run_dir / "jobs.jsonl"
    jobs = {j["id"]: j for j in load_jsonl(jobs_path)} if jobs_path.exists() else {}

    judge = None
    if args.judge:
        from judge import make_job2_judge

        judge = make_job2_judge(args.run_dir / "judge-cache.jsonl", mask_names=args.mask_name)

    flags = superseded_flags(records)
    rows = [
        grade_session(record, truth, jobs.get(record["task_id"]), superseded=sup, judge=judge)
        for record, sup in zip(records, flags, strict=True)
        if args.include_superseded or not sup
    ]

    out = args.out or args.run_dir / "grades.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    cons = consistency(rows)
    cons_path = out.parent / "consistency.json"
    cons_path.write_text(json.dumps(cons, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(summary_table(rows, cons, superseded=sum(flags)))
    if judge is not None:
        print(judge_summary(rows))
    print(f"\nwrote {out} ({len(rows)} rows) and {cons_path} ({len(cons)} rows)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
