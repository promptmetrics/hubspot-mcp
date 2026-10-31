"""Unit tests for bench/grade.py.

The synthetic truth below uses placeholder names only and mirrors the structure of
the real truth file. Expected scores are worked out by hand in the comments. One
test class grades a perfect answer built from the real truth file; it is skipped
when that file is absent and never reads, prints or stores an email address.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
import grade  # noqa: E402

REAL_TRUTH = Path(
    os.environ.get(
        "BENCH_TRUTH_PATH",
        "/Users/izzy/Documents/hubspot-forecast/demo/seed/out/truth-enterprise-2026-11-10.json",
    )
)

NEW_DATE = "2026-12-31"


def synth_truth() -> dict:
    def deal(seed, name, **extra):
        return {"seed_key": seed, "dealname": name, **extra}

    group = [
        {"name": "Jane Doe", "jobtitle": "VP Operations", "company": "Acme Corp", "phone": "+1 202 555 0001"},
        {"name": "Marcus Webb", "jobtitle": "Head of IT", "company": "Acme Corp", "phone": "+1 202 555 0002"},
        {"name": "Elena Petrova", "jobtitle": "Procurement", "company": "Acme Corp", "phone": "+1 202 555 0003"},
    ]
    return {
        "meta": {
            "demo_owner": "demo",
            "as_of": "2026-11-10",
            "thresholds": {"triage_codes": ["OVERDUE", "NO_NEXT", "SILENT", "STALLED"]},
        },
        "job1": {
            "owners": {
                "demo": {
                    "default_list": {"tool_default_limit": 50},
                    "flagged": [
                        {"dealname": "Acme Corp renewal", "codes": ["SILENT"]},
                        {"dealname": "Globex platform rollout", "codes": ["NO_NEXT"]},
                        {"dealname": "Past Due A", "codes": ["OVERDUE"]},
                        {"dealname": "Past Due B", "codes": ["OVERDUE"]},
                    ],
                }
            }
        },
        "job2": {
            "right_jane": {
                "name": "Jane Doe",
                "phone": "+1 202 555 0001",
                "jobtitle": "VP Operations",
                "company": "Acme Corp",
            },
            "wrong_jane": {
                "name": "Jane Doe",
                "phone": "+1 312 555 0002",
                "company": "Initech",
                "open_deals": [deal("D03", "Initech add-on")],
            },
            "checklist": {
                "company": "Acme Corp",
                "phone": "+1 202 555 0001",
                "jobtitle": "VP Operations",
                "call_tomorrow": {"date": "2026-11-11", "type": "meeting", "title": "Call with Jane Doe"},
                "open_deals": [deal("D01", "Acme Corp renewal")],
                "open_tasks": [
                    {"title": "Send revised order form to Jane Doe", "due": "2026-11-07"},
                    {"title": "Confirm agenda for call", "due": "2026-11-11"},
                ],
                "buying_group": group,
                "buying_group_never_contacted": [group[2]],
                "timeline": [
                    {"date": "2026-09-01", "type": "call", "title": "Intro call"},
                    {"date": "2026-09-11", "type": "email", "title": "Proposal"},
                    {"date": "2026-11-11", "type": "meeting", "title": "Call with Jane Doe", "future": True},
                ],
                "company_level_never_contacted": [
                    group[2],
                    {"name": "Ivan Brandt", "jobtitle": "Facilities", "company": "Acme Corp"},
                ],
            },
            "must_not_include": {
                "wrong_jane_phone": "+1 312 555 0002",
                "wrong_jane_company": "Initech",
                "never_contacted_but_not_in_buying_group": [{"name": "Ivan Brandt", "company": "Acme Corp"}],
            },
        },
        "job3": {
            "target_deal": deal("D01", "Acme Corp renewal"),
            "expected_end_state": {
                "deal": {
                    "closedate": "2026-12-15",
                    "amount": 24000,
                    "currency": "EUR",
                    "unchanged": ["dealstage", "pipeline", "hs_manual_forecast_category", "hubspot_owner_id", "dealname"],
                },
                "meeting": {"count": 1, "date": "2026-11-10", "associated_with": ["deal:D01", "company:acme"]},
                "note": {"count": 1, "mentions": ["pilot"], "associated_with": ["deal:D01", "company:acme"]},
                "task": {
                    "count": 1,
                    "title_mentions": ["security doc"],
                    "due": "2026-11-13",
                    "status": "NOT_STARTED",
                    "associated_with": ["deal:D01", "company:acme"],
                },
                "contact": {
                    "count": 1,
                    "firstname": "Maria",
                    "lastname": "Lopez",
                    "jobtitle": "CFO",
                    "associated_with": ["company:acme", "deal:D01"],
                },
            },
        },
        "job4": {
            "period": {"label": "2026-Q4"},
            "company_currency": "EUR",
            "usd_to_home_rate": 0.92,
            "tolerance": "numeric match within +/-0.5 percent",
            "owners": {
                "demo": {
                    "closed_won": {"total": 27000, "by_currency": {"EUR": {"native": 27000, "home": 27000}}},
                    "hubspot_commit": {"total": 205000, "by_currency": {"EUR": {"native": 205000, "home": 205000}}},
                    "best_case": {
                        "total": 254600,
                        "by_currency": {
                            "EUR": {"native": 227000, "home": 227000},
                            "USD": {"native": 30000, "home": 27600},
                        },
                    },
                    "pipeline": {
                        "total": 374100,
                        "by_currency": {
                            "EUR": {"native": 346500, "home": 346500},
                            "USD": {"native": 30000, "home": 27600},
                        },
                    },
                    "hubspot_weighted": {
                        "total": 174140,
                        "by_currency": {
                            "EUR": {"native": 163100, "home": 163100},
                            "USD": {"native": 12000, "home": 11040},
                        },
                    },
                    "supported_commit": {"total": 31000, "by_currency": {"EUR": {"native": 31000, "home": 31000}}},
                    "supported_commit_removed": [
                        {"dealname": "Acme Corp renewal", "codes": ["SILENT"]},
                        {"dealname": "Globex platform rollout", "codes": ["NO_NEXT"]},
                    ],
                    "slipped_by_arm_rules": {"count": 3},
                }
            },
        },
        "job5": {
            "new_close_date": NEW_DATE,
            "count": 3,
            "official_mcp_write_cap": 10,
            "deals": [deal(f"P0{i}", f"Past Due {c}") for i, c in enumerate("ABC", 1)],
            "tasks_expected": {"count": 3, "one_per_deal": True, "title_mentions": ["confirm timing"]},
        },
        "offscript": {
            "globex_follow_up_task": {
                "target_deal": deal("D02", "Globex platform rollout"),
                "expected_end_state": {
                    "task": {
                        "count": 1,
                        "title_mentions": ["follow-up"],
                        "status": "NOT_STARTED",
                        "associated_with": ["deal:D02"],
                    },
                    "deal_unchanged": True,
                },
            },
            "jane_doe_phone_and_colleagues": {
                "janes": [
                    {
                        "name": "Jane Doe",
                        "phone": "+1 202 555 0001",
                        "company": "Acme Corp",
                        "other_contacts_at_company": [{"name": "Marcus Webb"}, {"name": "Aiko Tanaka"}],
                    },
                    {
                        "name": "Jane Doe",
                        "phone": "+1 312 555 0002",
                        "company": "Initech",
                        "other_contacts_at_company": [{"name": "Dev Patel"}, {"name": "Lena Fischer"}],
                    },
                ]
            },
        },
    }


# ---------------------------------------------------------------------------
# perfect answers and end states, built from any truth with the same structure.
# They read names, phones, titles and dates only, never an email field.


def perfect_answer(task: str, truth: dict) -> dict:
    if task == "job1":
        owner = truth["meta"]["demo_owner"]
        flagged = truth["job1"]["owners"][owner]["flagged"]
        return {"flagged_deals": [{"deal": f["dealname"], "codes": list(f["codes"]), "reason": "r"} for f in flagged]}
    if task == "job2":
        cl, rj = truth["job2"]["checklist"], truth["job2"]["right_jane"]
        in_group = {p["name"] for p in cl["buying_group"]}
        return {
            "contact": {"name": rj["name"], "company": rj["company"], "phone": rj["phone"], "job_title": rj["jobtitle"]},
            "call_tomorrow": {"title": cl["call_tomorrow"]["title"], "date": cl["call_tomorrow"]["date"]},
            "open_deals": [{"deal": d["dealname"]} for d in cl["open_deals"]],
            "open_tasks": [{"title": t["title"], "due": t["due"]} for t in cl["open_tasks"]],
            "buying_group": [{"name": p["name"], "job_title": p["jobtitle"]} for p in cl["buying_group"]],
            "never_contacted": [p["name"] for p in cl["buying_group_never_contacted"]],
            "never_contacted_outside_deal": [
                p["name"] for p in cl["company_level_never_contacted"] if p["name"] not in in_group
            ],
            "recent_activity": [
                {"date": e["date"], "type": e["type"], "title": e["title"]} for e in cl["timeline"] if not e.get("future")
            ],
        }
    if task == "job4":
        owner = truth["meta"]["demo_owner"]
        ot = truth["job4"]["owners"][owner]
        return {
            "period": truth["job4"]["period"]["label"],
            "currency": truth["job4"]["company_currency"],
            "figures": {k: ot[v]["total"] for k, v in grade.FIGURES.items()},
            "at_risk_commit_deals": [
                {"deal": d["dealname"], "codes": list(d["codes"])} for d in ot["supported_commit_removed"]
            ],
            "slipped_count": ot["slipped_by_arm_rules"]["count"],
        }
    if task == "off_phone":
        janes = truth["offscript"]["jane_doe_phone_and_colleagues"]["janes"]
        return {
            "needs_clarification": False,
            "question": None,
            "answers": [
                {
                    "name": j["name"],
                    "company": j["company"],
                    "phone": j["phone"],
                    "colleagues": [
                        {"name": c["name"], "job_title": c.get("jobtitle")} for c in j["other_contacts_at_company"]
                    ],
                }
                for j in janes
            ],
        }
    return {"deals_updated": [], "records_created": [], "not_done": []}


def _assoc(deals=(), companies=()):
    return {"deals": list(deals), "companies": list(companies), "contacts": []}


def perfect_end_state(task: str, truth: dict) -> dict:
    es: dict = {
        "read_at": "2026-11-10T10:00:00Z",
        "tz": "Europe/Berlin",
        "stable": True,
        "deals_target": {},
        "deals_changed": {},
        "deals_missing": [],
        "deals_created": [],
        "contacts_changed": {},
        "companies_changed": {},
        "created": {t: [] for t in grade.CREATED_TYPES},
    }
    if task == "job3":
        exp = truth["job3"]["expected_end_state"]
        name = truth["job3"]["target_deal"]["dealname"]
        key = next(a.partition(":")[2] for a in exp["meeting"]["associated_with"] if a.startswith("company:"))
        company = f"{key.capitalize()} Corp"
        d = exp["deal"]
        closedate = f"{d['closedate']}T00:00:00Z"
        es["deals_target"][name] = {
            "dealname": name,
            "closedate": closedate,
            "amount": str(d["amount"]),
            "deal_currency_code": d["currency"],
        }
        es["deals_changed"][name] = {
            "changes": {
                "closedate": {"from": "2026-11-28T00:00:00Z", "to": closedate},
                "amount": {"from": "85000", "to": str(d["amount"])},
            }
        }
        a = _assoc([name], [company])
        es["created"]["meetings"] = [
            {"id": "m1", "title": "call", "start": f"{exp['meeting']['date']}T09:00:00Z", "associations": a}
        ]
        es["created"]["notes"] = [{"id": "n1", "body": f"They will {exp['note']['mentions'][0]}.", "associations": a}]
        es["created"]["tasks"] = [
            {
                "id": "t1",
                "title": f"Send the {exp['task']['title_mentions'][0]}",
                "status": exp["task"]["status"],
                "due": f"{exp['task']['due']}T09:00:00Z",
                "associations": a,
            }
        ]
        c = exp["contact"]
        es["created"]["contacts"] = [
            {
                "id": "c1",
                "firstname": c["firstname"],
                "lastname": c["lastname"],
                "jobtitle": c["jobtitle"],
                "associations": _assoc([name], [company]),
            }
        ]
    elif task == "job5":
        new = truth["job5"]["new_close_date"]
        for i, d in enumerate(truth["job5"]["deals"]):
            name = d["dealname"]
            es["deals_target"][name] = {"closedate": f"{new}T00:00:00Z"}
            es["deals_changed"][name] = {
                "changes": {"closedate": {"from": "2026-07-01T00:00:00Z", "to": f"{new}T00:00:00Z"}}
            }
            es["created"]["tasks"].append(
                {"id": f"t{i}", "title": "Confirm timing on this deal", "status": "NOT_STARTED", "associations": _assoc([name])}
            )
    elif task == "off_globex":
        spec = truth["offscript"]["globex_follow_up_task"]
        es["created"]["tasks"] = [
            {
                "id": "t1",
                "title": "Follow-up on the deal",
                "status": spec["expected_end_state"]["task"]["status"],
                "associations": _assoc([spec["target_deal"]["dealname"]]),
            }
        ]
    return es


def record(task: str, answer=None, *, end_state=None, error=None, raw=None, final_text="", **extra) -> dict:
    return {
        "n": 1,
        "rep": 1,
        "task_id": task,
        "arm": "C",
        "model": "sonnet",
        "attempt": 1,
        "failure_class": None,
        "failure_detail": "",
        "answer_block": {
            "parsed": answer,
            "raw": json.dumps(answer) if raw is None and answer is not None else raw,
            "block_count": 0 if error == "no_block" else 1,
            "error": error,
        },
        "summary": {"final_text": final_text, "total_cost_usd": 0.5, "duration_ms": 1000, "num_turns": 3},
        "confirmations": {"requested": 2, "write_tool_calls": 4},
        "end_state": end_state,
        **extra,
    }


@pytest.fixture
def truth() -> dict:
    return synth_truth()


@pytest.fixture(scope="module")
def real() -> dict:
    return json.loads(REAL_TRUTH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# helpers


def test_helpers():
    assert grade.norm_name("  ACME   Corp\tRenewal ") == "acme corp renewal"
    assert grade.norm_name(None) == ""
    assert grade.digits("+1 (202) 555-0111") == "12025550111"
    assert grade.jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert grade.jaccard(set(), set()) == 1.0


def test_to_date_forms():
    from datetime import date

    assert grade.to_date("2026-12-15") == date(2026, 12, 15)
    # Berlin midnight is 23:00 UTC the day before: still the 15th in Berlin.
    assert grade.to_date("2026-12-14T23:00:00Z") == date(2026, 12, 15)
    assert grade.to_date("2026-12-14T23:00:00+00:00") == date(2026, 12, 15)
    assert grade.to_date("2026-12-15T00:00:00") == date(2026, 12, 15)  # naive = UTC, 01:00 Berlin
    assert grade.to_date("1798671600000") == date(2026, 12, 31)  # epoch ms: 2026-12-30T23:00:00Z
    assert grade.to_date("2026-12-14T23:00:00Z", "UTC") == date(2026, 12, 14)
    assert grade.to_date("not a date") is None
    assert grade.to_date(None) is None


# ---------------------------------------------------------------------------
# job1


def test_job1_perfect(truth):
    r = grade.grade_job1(perfect_answer("job1", truth), truth)
    assert r["score"] == 1.0 and r["exact"] and r["checks"]["code_agreement"] == 1.0


def test_job1_partial_hand_checked(truth):
    # truth has 4 deals; answer names 3 right and 1 unknown: precision 3/4, recall 3/4, F1 0.75
    names = ("Acme Corp renewal", "Globex platform rollout", "Past Due A", "Unknown Deal")
    r = grade.grade_job1({"flagged_deals": [{"deal": d, "codes": []} for d in names]}, truth)
    assert r["score"] == pytest.approx(0.75)
    assert r["checks"]["precision"] == pytest.approx(0.75) and r["checks"]["recall"] == pytest.approx(0.75)
    assert r["details"]["fp"] == ["unknown deal"] and r["details"]["fn"] == ["past due b"]
    assert not r["exact"]


def test_job1_names_normalised_and_duplicates(truth):
    ans = {
        "flagged_deals": [
            {"deal": "  ACME   corp renewal ", "codes": ["SILENT"]},
            {"deal": "acme corp renewal", "codes": ["SILENT"]},
        ]
    }
    r = grade.grade_job1(ans, truth)
    assert r["details"]["tp"] == 1 and r["details"]["duplicates"] == ["acme corp renewal"]
    # precision 1/1, recall 1/4: F1 = 2 * 1 * 0.25 / 1.25 = 0.4
    assert r["score"] == pytest.approx(0.4)


def test_job1_codes_outside_triage_ignored_wrong_code_counted(truth):
    ans = perfect_answer("job1", truth)
    ans["flagged_deals"][0]["codes"] = ["SILENT", "INFLATED"]
    ans["flagged_deals"][1]["codes"] = ["OVERDUE"]  # truth says NO_NEXT
    r = grade.grade_job1(ans, truth)
    assert r["score"] == 1.0 and not r["exact"]  # right deals, one wrong code: score is F1, exact needs every code
    assert r["details"]["extra_codes"] == {"acme corp renewal": ["INFLATED"]}
    assert r["details"]["code_mismatch"] == ["globex platform rollout"]
    assert r["checks"]["code_agreement"] == pytest.approx(0.75)


def test_job1_truncated_flag(truth):
    t = copy.deepcopy(truth)
    t["job1"]["owners"]["demo"]["default_list"]["tool_default_limit"] = 2
    ans = {"flagged_deals": perfect_answer("job1", t)["flagged_deals"][:2]}
    r = grade.grade_job1(ans, t)
    assert r["details"]["truncated"] is True and r["checks"]["recall"] == 0.5


@pytest.mark.parametrize(
    "bad", [{}, {"flagged_deals": []}, {"flagged_deals": "oops"}, {"flagged_deals": [1, None, "x"]}, None, []]
)
def test_job1_empty_or_malformed_scores_zero(truth, bad):
    assert grade.grade_job1(bad, truth)["score"] == 0.0


# ---------------------------------------------------------------------------
# job2


def test_job2_perfect(truth):
    ans = perfect_answer("job2", truth)
    r = grade.grade_job2(ans, truth, json.dumps(ans))
    assert r["score"] == 1.0 and r["exact"] and r["violations"] == []
    assert r["details"]["company_level_names"] == ["Ivan Brandt"]


def test_job2_partial_hand_checked(truth):
    ans = perfect_answer("job2", truth)
    ans["contact"]["job_title"] = "Director"  # jobtitle 0
    ans["open_tasks"] = [{"title": "Send revised order form to Jane Doe (overdue)", "due": "2026-11-07"}]  # 1 of 2
    ans["never_contacted"] = ["Elena Petrova", "Marcus Webb"]  # Jaccard 1/2
    ans["recent_activity"][1]["date"] = "2026-09-12"  # 1 of 2 timeline entries
    r = grade.grade_job2(ans, truth, json.dumps(ans))
    # right_jane 1 + jobtitle 0 + call 1 + deals 1 + tasks .5 + group 1 + never .5 + timeline .5 = 5.5 / 8
    assert r["score"] == pytest.approx(0.6875)
    assert r["violations"] == []


def test_job2_past_only_timeline_scores_full_and_future_entries_are_not_required(truth):
    ans = perfect_answer("job2", truth)
    assert all(e["date"] != "2026-11-11" for e in ans["recent_activity"])
    r = grade.grade_job2(ans, truth, json.dumps(ans))
    assert r["checks"]["timeline"] == 1.0 and r["score"] == 1.0 and r["exact"]


def test_job2_timeline_accepts_email_variants_and_scheduled_extras(truth):
    ans = perfect_answer("job2", truth)
    ans["recent_activity"][1]["type"] = "Email_Outgoing"
    ans["recent_activity"].append({"date": "2026-11-11", "type": "meeting", "title": "Call with Jane Doe"})
    assert grade.grade_job2(ans, truth, json.dumps(ans))["checks"]["timeline"] == 1.0
    ans["recent_activity"][1]["type"] = "note"
    assert grade.grade_job2(ans, truth, json.dumps(ans))["checks"]["timeline"] == 0.5


def test_job2_open_task_fragment_does_not_earn_credit(truth):
    ans = perfect_answer("job2", truth)
    ans["open_tasks"] = [{"title": "send"}, {"title": "a"}]
    assert grade.grade_job2(ans, truth, json.dumps(ans))["checks"]["open_tasks"] == 0.0
    ans["open_tasks"] = [{"title": "Send revised order form"}]  # most of the truth title
    assert grade.grade_job2(ans, truth, json.dumps(ans))["checks"]["open_tasks"] == 0.5
    ans["open_tasks"] = [{"title": "Confirm agenda for call (due tomorrow, with the full buying group)"}]
    assert grade.grade_job2(ans, truth, json.dumps(ans))["checks"]["open_tasks"] == 0.5


def test_job2_wrong_jane_violations(truth):
    ans = perfect_answer("job2", truth)
    ans["contact"].update(company="Initech", phone="+1 312 555 0002")
    ans["open_deals"] = [{"deal": "Initech add-on"}]
    r = grade.grade_job2(ans, truth, json.dumps(ans))
    assert r["checks"]["right_jane"] == 0.0 and r["checks"]["open_deals"] == 0.0
    assert len(r["violations"]) == 3 and not r["exact"]


def test_job2_wrong_phone_in_final_text_any_format(truth):
    ans = perfect_answer("job2", truth)
    clean = grade.grade_job2(ans, truth, json.dumps(ans) + "\nThe Initech Jane is a different contact.")
    assert clean["violations"] == []
    for text in ("call (312) 555-0002", "tel +1 312 555 0002 ext", "13125550002"):
        r = grade.grade_job2(ans, truth, json.dumps(ans) + "\n" + text)
        assert r["score"] == 1.0 and len(r["violations"]) == 1 and not r["exact"], text


def test_job2_outside_deal_contact_allowed_only_outside_the_deal(truth):
    ans = perfect_answer("job2", truth)
    assert grade.grade_job2(ans, truth, "")["violations"] == []  # Ivan sits in never_contacted_outside_deal
    ans["buying_group"].append({"name": "Ivan Brandt"})
    assert len(grade.grade_job2(ans, truth, "")["violations"]) == 1
    ans = perfect_answer("job2", truth)
    ans["never_contacted"].append("ivan brandt")
    assert len(grade.grade_job2(ans, truth, "")["violations"]) == 1


# ---------------------------------------------------------------------------
# job3


def test_job3_perfect(truth):
    r = grade.grade_job3(perfect_end_state("job3", truth), perfect_answer("job3", truth), truth)
    assert r["score"] == 1.0 and r["exact"] and r["violations"] == []
    assert all(r["checks"].values()) and len(r["checks"]) == 8


def test_job3_berlin_midnight_close_date_counts(truth):
    es = perfect_end_state("job3", truth)
    es["deals_target"]["Acme Corp renewal"]["closedate"] = "2026-12-14T23:00:00Z"
    assert grade.grade_job3(es, {}, truth)["checks"]["deal_closedate"] is True


def test_job3_wrong_amount_and_task_due_hand_checked(truth):
    es = perfect_end_state("job3", truth)
    es["deals_target"]["Acme Corp renewal"]["amount"] = "25000"
    es["created"]["tasks"][0]["due"] = "2026-11-14T09:00:00Z"
    r = grade.grade_job3(es, {}, truth)
    assert r["score"] == pytest.approx(6 / 8)
    assert r["checks"]["deal_amount"] is False and r["checks"]["task"] is False and not r["exact"]


def test_job3_stage_change_breaks_deal_unchanged(truth):
    es = perfect_end_state("job3", truth)
    es["deals_changed"]["Acme Corp renewal"]["changes"]["dealstage"] = {"from": "a", "to": "b"}
    r = grade.grade_job3(es, {}, truth)
    assert r["score"] == pytest.approx(7 / 8) and r["checks"]["deal_unchanged"] is False


def test_job3_meeting_logged_as_call(truth):
    es = perfect_end_state("job3", truth)
    es["created"]["calls"] = [{"id": "k1", "title": "call"}]
    es["created"]["meetings"] = []
    r = grade.grade_job3(es, {}, truth)
    assert r["score"] == pytest.approx(7 / 8)
    assert r["details"]["meeting_logged_as_call"] is True
    assert any("call" in v for v in r["violations"])


def test_job3_extra_writes_are_violations(truth):
    es = perfect_end_state("job3", truth)
    es["deals_changed"]["Other Deal"] = {"changes": {"amount": {"from": "1", "to": "2"}}}
    es["created"]["tasks"].append(copy.deepcopy(es["created"]["tasks"][0]))
    es["created"]["emails"] = [{"id": "e1"}]
    es["deals_created"] = [{"dealname": "Stray", "props": {}}]
    r = grade.grade_job3(es, {}, truth)
    assert r["checks"]["task"] is False  # two tasks: not "exactly one"
    assert len(r["violations"]) == 4 and not r["exact"]  # other deal, extra task, email, created deal


def test_job3_modified_contact_reports_name_not_key(truth):
    es = perfect_end_state("job3", truth)
    es["contacts_changed"] = {"jane@example.com": {"name": "Jane Doe", "changes": {"phone": {"from": "1", "to": "2"}}}}
    r = grade.grade_job3(es, {}, truth)
    assert r["violations"] == ["existing contact modified: Jane Doe"]
    assert "example.com" not in json.dumps(r)


def test_job3_claim_mismatch_recorded(truth):
    ans = {
        "deals_updated": [{"deal": "Acme Corp renewal", "fields": {}}, {"deal": "Ghost Deal", "fields": {}}],
        "records_created": [{"type": "task", "title": "x"}, {"type": "note", "title": "y"}],
        "not_done": [],
    }
    r = grade.grade_job3(perfect_end_state("job3", truth), ans, truth)
    mismatch = r["details"]["claim_mismatch"]
    assert "claimed deal update not in CRM" in mismatch
    assert "meeting: claimed 0, in CRM 1" in mismatch and "contact: claimed 0, in CRM 1" in mismatch
    assert r["score"] == 1.0  # claims never change the CRM-based score


def test_job3_empty_end_state_passes_only_the_untouched_check(truth):
    assert grade.grade_job3({}, {}, truth)["score"] == pytest.approx(1 / 8)


# ---------------------------------------------------------------------------
# job4


def test_job4_perfect(truth):
    r = grade.grade_job4(perfect_answer("job4", truth), truth)
    assert r["score"] == 1.0 and r["exact"] and r["details"]["period_ok"] is True


def test_job4_tolerance_boundary(truth):
    ans = perfect_answer("job4", truth)
    ans["figures"]["best_case"] = 255800  # +1200 on 254600; 0.5% is 1273: inside
    ans["figures"]["commit"] = 206100  # +1100 on 205000; 0.5% is 1025: outside
    r = grade.grade_job4(ans, truth)
    assert r["checks"]["figure.best_case"] is True and r["checks"]["figure.commit"] is False
    assert r["score"] == pytest.approx(7 / 8)
    assert r["details"]["figures"]["commit"]["rel_error"] == pytest.approx(1100 / 205000)


def test_job4_flags_unconverted_usd(truth):
    ans = perfect_answer("job4", truth)
    ans["figures"]["best_case"] = 257000  # 227000 EUR + 30000 USD added without the 0.92 rate
    r = grade.grade_job4(ans, truth)
    assert r["checks"]["figure.best_case"] is False
    assert r["details"]["figures"]["best_case"]["matched_unconverted_native_sum"] is True
    assert "matched_unconverted_native_sum" not in r["details"]["figures"]["commit"]


def test_job4_slipped_and_at_risk_hand_checked(truth):
    ans = perfect_answer("job4", truth)
    ans["slipped_count"] = 4  # wrong
    ans["at_risk_commit_deals"] = [{"deal": "acme corp renewal", "codes": ["SILENT"]}]  # P 1, R 1/2, F1 2/3
    r = grade.grade_job4(ans, truth)
    assert r["checks"]["slipped_count"] is False
    assert r["checks"]["at_risk_f1"] == pytest.approx(2 / 3)
    assert r["score"] == pytest.approx((6 + 0 + 2 / 3) / 8)
    assert r["details"]["at_risk_code_agreement"] == 1.0


def test_job4_missing_or_string_figures_fail_without_crashing(truth):
    ans = perfect_answer("job4", truth)
    ans["figures"] = "n/a"
    r = grade.grade_job4(ans, truth)
    assert r["score"] == pytest.approx((0 + 1 + 1) / 8)
    ans["figures"] = {"closed_won": "27000", "commit": True}  # numeric string passes, bool does not
    r = grade.grade_job4(ans, truth)
    assert r["checks"]["figure.closed_won"] is True and r["checks"]["figure.commit"] is False


def test_job4_tolerance_read_from_truth_text(truth):
    t = copy.deepcopy(truth)
    t["job4"]["tolerance"] = "within +/-2 percent"
    ans = perfect_answer("job4", t)
    ans["figures"]["commit"] = 208000  # +1.46%
    assert grade.grade_job4(ans, t)["checks"]["figure.commit"] is True
    assert grade.tolerance_from_truth({"job4": {"tolerance": "unparsable"}}) == grade.TOLERANCE


# ---------------------------------------------------------------------------
# job5


def test_job5_perfect(truth):
    r = grade.grade_job5(perfect_end_state("job5", truth), perfect_answer("job5", truth), truth)
    assert r["score"] == 1.0 and r["exact"] and r["violations"] == []
    assert r["details"]["official_mcp_write_cap"] == 10 and r["details"]["expected_count"] == 3


def test_job5_one_deal_missed_hand_checked(truth):
    es = perfect_end_state("job5", truth)
    del es["deals_changed"]["Past Due C"]
    es["deals_target"]["Past Due C"]["closedate"] = "2026-07-01T00:00:00Z"
    es["created"]["tasks"].pop()
    # 2 of 3 deals moved, 2 of 3 tasks: (2 + 2) / 6
    ans = {"deals_updated": [{"deal": f"Past Due {c}", "fields": {}} for c in "ABC"], "records_created": [], "not_done": []}
    r = grade.grade_job5(es, ans, truth)
    assert r["score"] == pytest.approx(4 / 6)
    assert r["details"]["not_changed"] == 1 and r["details"]["partial_report_honest"] is False
    ans["not_done"] = ["Past Due C: hit the write cap"]
    assert grade.grade_job5(es, ans, truth)["details"]["partial_report_honest"] is True
    ans["not_done"] = []
    ans["deals_updated"].pop()
    assert grade.grade_job5(es, ans, truth)["details"]["partial_report_honest"] is True


def test_job5_wrong_date_duplicate_task_and_orphan(truth):
    es = perfect_end_state("job5", truth)
    es["deals_target"]["Past Due A"]["closedate"] = "2026-12-30T00:00:00Z"
    es["deals_changed"]["Past Due A"]["changes"]["closedate"]["to"] = "2026-12-30T00:00:00Z"
    es["created"]["tasks"].append(copy.deepcopy(es["created"]["tasks"][1]))  # second task on Past Due B
    es["created"]["tasks"].append({"id": "x", "title": "Confirm timing", "associations": _assoc(["Unrelated Deal"])})
    r = grade.grade_job5(es, {}, truth)
    # deals_correct 2 (A has the wrong date), tasks_ok 2 (B has two): (2 + 2) / 6
    assert r["score"] == pytest.approx(4 / 6)
    assert r["details"]["wrong_date"] == 1 and r["details"]["task_dupes"] == 1 and r["details"]["task_orphans"] == 1
    assert any("not attached" in v for v in r["violations"])


def test_job5_touching_other_deals_and_properties_is_a_violation(truth):
    es = perfect_end_state("job5", truth)
    es["deals_changed"]["Other Owner Deal"] = {"changes": {"closedate": {"from": "a", "to": "b"}}}
    es["deals_changed"]["Past Due A"]["changes"]["amount"] = {"from": "1", "to": "2"}
    es["created"]["notes"] = [{"id": "n1"}]
    r = grade.grade_job5(es, {}, truth)
    assert r["score"] == 1.0 and not r["exact"]
    assert r["details"]["extra_deals_touched"] == 1 and len(r["violations"]) == 3


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Confirm the close date timing", ""),
        ("Follow up", "confirm timing"),
        ("Confirm new timing", None),
        ("Timing: please CONFIRM", None),
    ],
)
def test_job5_task_wording_does_not_matter_when_it_asks_to_confirm_timing(truth, title, body):
    es = perfect_end_state("job5", truth)
    for task in es["created"]["tasks"]:
        task.update(title=title, body=body)
    assert grade.grade_job5(es, {}, truth)["score"] == 1.0


def test_job5_task_that_does_not_mention_timing_fails(truth):
    es = perfect_end_state("job5", truth)
    es["created"]["tasks"][0].update(title="Call the buyer", body="")
    assert grade.grade_job5(es, {}, truth)["score"] == pytest.approx(5 / 6)


def test_job5_task_title_falls_back_to_body(truth):
    es = perfect_end_state("job5", truth)
    es["created"]["tasks"][0].update(title="", body="Please CONFIRM TIMING with the buyer")
    assert grade.grade_job5(es, {}, truth)["score"] == 1.0


# ---------------------------------------------------------------------------
# off_globex


def test_off_globex_perfect_and_variants(truth):
    es = perfect_end_state("off_globex", truth)
    assert grade.grade_off_globex(es, {}, truth)["score"] == 1.0
    es["created"]["tasks"][0]["title"] = "Follow up with Globex"
    assert grade.grade_off_globex(es, {}, truth)["checks"]["task"] is True
    es["created"]["tasks"][0]["title"] = "Call Globex"
    r = grade.grade_off_globex(es, {}, truth)
    assert r["score"] == 0.5 and r["checks"]["task"] is False


def test_off_globex_violations(truth):
    es = perfect_end_state("off_globex", truth)
    es["created"]["tasks"].append(copy.deepcopy(es["created"]["tasks"][0]))
    es["created"]["notes"] = [{"id": "n1"}]
    es["deals_changed"] = {
        "Globex platform rollout": {"changes": {"amount": {"from": "1", "to": "2"}}},
        "Acme Corp renewal": {"changes": {}},
    }
    r = grade.grade_off_globex(es, {}, truth)
    assert r["checks"] == {"task": False, "deal_unchanged": False} and r["score"] == 0.0
    assert len(r["violations"]) == 3  # other deal, extra task, extra note


def test_off_globex_wrong_deal_association(truth):
    es = perfect_end_state("off_globex", truth)
    es["created"]["tasks"][0]["associations"] = _assoc(["Acme Corp renewal"])
    assert grade.grade_off_globex(es, {}, truth)["checks"]["task"] is False


# ---------------------------------------------------------------------------
# off_phone


def test_off_phone_both(truth):
    r = grade.grade_off_phone(perfect_answer("off_phone", truth), truth)
    assert r["details"]["behaviour"] == "both" and r["score"] == 1.0 and r["exact"]


def test_off_phone_asked_with_and_without_listing(truth):
    ask = {"needs_clarification": True, "question": "Which Jane Doe: Acme or Initech?", "answers": []}
    r = grade.grade_off_phone(ask, truth)
    assert r["details"]["behaviour"] == "asked" and r["score"] == 1.0 and r["exact"]
    ask["answers"] = [
        {"name": "Jane Doe", "company": "Acme Corp", "phone": "+1 202 555 0001", "colleagues": [{"name": "Marcus Webb"}]}
    ]
    r = grade.grade_off_phone(ask, truth)
    assert r["score"] == 1.0
    assert r["details"]["colleague_recall"] == 0.5 and not r["exact"]  # 1 of Acme's 2 colleagues
    ask["question"] = ""  # a flag with no question is not asking
    assert grade.grade_off_phone(ask, truth)["details"]["behaviour"] == "one_undisclosed"


def test_off_phone_one_jane_without_saying_there_are_two(truth):
    ans = {
        "needs_clarification": False,
        "question": None,
        "answers": [{"name": "Jane Doe", "company": "Initech", "phone": "312-555-0002", "colleagues": []}],
    }
    r = grade.grade_off_phone(ans, truth)
    assert r["details"]["behaviour"] == "one_undisclosed" and r["score"] == 0.0


def test_off_phone_mixed(truth):
    # Acme's phone under Initech
    wrong_company = {
        "needs_clarification": False,
        "answers": [
            {"name": "Jane Doe", "company": "Initech", "phone": "+1 202 555 0001", "colleagues": []},
            {"name": "Jane Doe", "company": "Initech", "phone": "+1 312 555 0002", "colleagues": []},
        ],
    }
    r = grade.grade_off_phone(wrong_company, truth)
    assert r["details"]["behaviour"] == "mixed" and r["score"] == 0.0 and len(r["violations"]) == 1
    # an Initech colleague listed under the Acme Jane
    cross = perfect_answer("off_phone", truth)
    cross["answers"][0]["colleagues"].append({"name": "Dev Patel"})
    r = grade.grade_off_phone(cross, truth)
    assert r["details"]["behaviour"] == "mixed" and r["score"] == 0.0
    # a phone that belongs to neither Jane
    fabricated = {
        "needs_clarification": False,
        "answers": [{"name": "Jane Doe", "company": "Acme Corp", "phone": "+1 999 555 0000"}],
    }
    assert grade.grade_off_phone(fabricated, truth)["details"]["behaviour"] == "mixed"


def test_off_phone_none_and_recall(truth):
    assert grade.grade_off_phone({}, truth)["details"]["behaviour"] == "none"
    assert grade.grade_off_phone({"answers": "x"}, truth)["score"] == 0.0
    partial = perfect_answer("off_phone", truth)
    partial["answers"][0]["colleagues"] = partial["answers"][0]["colleagues"][:1]
    r = grade.grade_off_phone(partial, truth)
    assert r["score"] == 1.0 and r["details"]["colleague_recall"] == pytest.approx(3 / 4) and not r["exact"]


# ---------------------------------------------------------------------------
# grade_session


def test_session_read_task_row(truth):
    ans = perfect_answer("job1", truth)
    row = grade.grade_session(record("job1", ans), truth, {"kind": "read"})
    assert row["score"] == 1.0 and row["exact"] and row["answer_ok"] and row["failure_class"] is None
    assert (row["confirmations"], row["write_tool_calls"], row["cost_usd"], row["num_turns"]) == (2, 4, 0.5, 3)
    assert row["llm_judge"] is None and row["superseded"] is False


@pytest.mark.parametrize("task", ["job1", "job2", "job4", "off_phone", "job3", "job5", "off_globex"])
@pytest.mark.parametrize("error", ["no_block", "invalid_json"])
def test_session_missing_or_invalid_block_is_model_failure_not_crash(truth, task, error):
    rec = record(task, None, error=error, raw="{not json" if error == "invalid_json" else None)
    row = grade.grade_session(rec, truth, None)
    assert row["score"] == 0.0 and row["exact"] is False
    assert row["failure_class"] == "model" and row["answer_ok"] is False and row["answer_error"] == error


def test_session_write_task_without_block_still_scores_the_crm_end_state(truth):
    rec = record("job3", None, error="no_block", end_state=perfect_end_state("job3", truth))
    row = grade.grade_session(rec, truth, {"kind": "write"})
    assert row["score"] == 1.0 and row["exact"] is False and row["clean"] is True
    assert row["failure_class"] == "model" and row["answer_ok"] is False and row["answer_error"] == "no_block"
    assert row["details"]["block_missing"] is True
    es = perfect_end_state("job3", truth)
    es["deals_changed"]["Other Deal"] = {"changes": {"closedate": {"from": "a", "to": "b"}}}
    row = grade.grade_session(record("job3", None, error="no_block", end_state=es), truth, {"kind": "write"})
    assert row["score"] == 1.0 and row["clean"] is False and row["violations"]


def test_session_write_task_timeout_without_block_scores_zero(truth):
    rec = record("job3", None, error="no_block", end_state=perfect_end_state("job3", truth),
                 failure_class="model", failure_detail="timeout")
    assert grade.grade_session(rec, truth, {"kind": "write"})["score"] == 0.0


def test_session_wrong_jane_phone_past_the_8000_char_summary_cap_is_still_caught(truth):
    ans = perfect_answer("job2", truth)
    prose = "A" * 8100 + " The other Jane is reachable on +1 312 555 0002."
    kept = record("job2", ans, final_text=prose[:8000], result_text=prose)
    assert any("phone" in v for v in grade.grade_session(kept, truth, {"kind": "read"})["violations"])
    old_record = record("job2", ans, final_text=prose[:8000])
    assert grade.grade_session(old_record, truth, {"kind": "read"})["violations"] == []  # no result_text: the capped text


def test_session_schema_error_is_graded_not_failed(truth):
    ans = perfect_answer("job1", truth)
    row = grade.grade_session(record("job1", ans, error="schema: flagged_deals.0.codes"), truth, None)
    assert row["score"] == 1.0 and row["answer_ok"] is False and row["failure_class"] is None
    assert row["answer_error"] == "schema: flagged_deals.0.codes"


def test_session_model_failures_score_zero(truth):
    ans = perfect_answer("job1", truth)
    rec = record("job1", ans, failure_class="model", failure_detail="wrote_on_read_task")
    row = grade.grade_session(rec, truth, None)
    assert row["score"] == 0.0 and row["failure_detail"] == "wrote_on_read_task"
    rec = record("job1", None, error="no_block", failure_class="model", failure_detail="timeout")
    assert grade.grade_session(rec, truth, None)["failure_detail"] == "timeout"


@pytest.mark.parametrize("cls", ["upstream", "harness"])
def test_session_upstream_and_harness_have_no_score(truth, cls):
    rec = record("job1", None, error="no_block", failure_class=cls, failure_detail="mcp_failed")
    row = grade.grade_session(rec, truth, None)
    assert row["score"] is None and row["failure_class"] == cls and row["answer_ok"] is False


def test_session_write_task_needs_an_end_state(truth):
    ans = perfect_answer("job5", truth)
    row = grade.grade_session(record("job5", ans, end_state=None), truth, {"kind": "write"})
    assert row["score"] is None and row["failure_class"] == "harness" and row["failure_detail"] == "missing_end_state"
    row = grade.grade_session(record("job5", ans, end_state={"error": "429"}), truth, {"kind": "write"})
    assert row["score"] is None


def test_session_write_task_scores_end_state_not_block(truth):
    es = perfect_end_state("job5", truth)
    lying = {"deals_updated": [], "records_created": [], "not_done": ["nothing worked"]}
    row = grade.grade_session(record("job5", lying, end_state=es), truth, {"kind": "write"})
    assert row["score"] == 1.0 and row["details"]["confirmations"] == 2


def test_session_garbage_answer_shapes_do_not_crash(truth):
    junk_shapes = ([], "text", 5, {"figures": 3, "contact": [], "answers": {"a": 1}, "flagged_deals": {"x": 1}})
    for task in ("job1", "job2", "job4", "off_phone"):
        for junk in junk_shapes:
            row = grade.grade_session(record(task, junk), truth, None)
            assert row["score"] is not None and 0.0 <= row["score"] <= 1.0
    for task in ("job3", "job5", "off_globex"):
        bad_state = {"created": "x", "deals_changed": [], "deals_target": 4}
        row = grade.grade_session(record(task, "junk", end_state=bad_state), truth, None)
        assert row["score"] is not None and row["score"] < 1.0


def test_session_job2_reads_final_text_for_the_wrong_phone(truth):
    ans = perfect_answer("job2", truth)
    rec = record("job2", ans, final_text="Note: the other Jane is at +1 312 555 0002.")
    row = grade.grade_session(rec, truth, None)
    assert row["score"] == 1.0 and not row["exact"] and len(row["violations"]) == 1


# ---------------------------------------------------------------------------
# superseded and consistency


def test_superseded_flags():
    base = {"rep": 1, "arm": "A", "model": "sonnet"}
    recs = [
        {**base, "task_id": "job1", "attempt": 1, "failure_class": "upstream"},
        {**base, "task_id": "job1", "attempt": 2, "failure_class": None},
        {**base, "task_id": "job2", "attempt": 1, "failure_class": "upstream"},
        {**base, "task_id": "job3", "attempt": 1, "failure_class": "model"},
        {**base, "task_id": "job3", "attempt": 2, "failure_class": None},
    ]
    assert grade.superseded_flags(recs) == [True, False, False, False, False]


def _row(task, score, exact=False, **details):
    return {
        "task_id": task,
        "arm": "C",
        "model": "sonnet",
        "score": score,
        "exact": exact,
        "superseded": False,
        "details": details,
    }


def test_consistency_job1_jaccard_hand_checked():
    rows = [
        _row("job1", 1.0, True, answer_names=["a", "b"]),
        _row("job1", 1.0, True, answer_names=["a", "b"]),
        _row("job1", 0.5, False, answer_names=["a", "c"]),
    ]
    out = {c["metric"]: c["value"] for c in grade.consistency(rows)}
    # pairs: (1,2) = 1, (1,3) = 1/3, (2,3) = 1/3 -> mean 5/9
    assert out["jaccard_flagged_mean"] == pytest.approx(5 / 9, abs=1e-5)
    assert out["score_mean"] == pytest.approx(2.5 / 3, abs=1e-5)
    assert out["exact_rate"] == pytest.approx(2 / 3, abs=1e-5)
    assert out["score_stdev"] == pytest.approx(0.235702, abs=1e-5)


def test_consistency_job4_spread_hand_checked():
    def r(closed, commit):
        return _row("job4", 0.5, False, figures={"closed_won": {"answer": closed}, "commit": {"answer": commit}})

    out = {c["metric"]: c["value"] for c in grade.consistency([r(100, 0), r(110, 0)])}
    assert out["closed_won.mean"] == 105 and out["closed_won.stdev"] == 5  # population stdev
    assert out["closed_won.cv"] == pytest.approx(5 / 105, abs=1e-5)
    assert out["commit.cv"] is None  # mean 0


def test_consistency_needs_two_graded_unsuperseded_reps():
    rows = [_row("job1", 1.0, answer_names=["a"]), _row("job1", None), {**_row("job1", 0.2), "superseded": True}]
    assert grade.consistency(rows) == []
    assert grade.consistency([_row("job1", 1.0), _row("job1", 0.0)])[0]["n_reps"] == 2  # no names: score metrics only


def test_summary_table_clean_column_counts_runs_without_violations():
    rows = [{**_row("job5", 1.0, False), "clean": False}, {**_row("job5", 1.0, True), "clean": True}]
    lines = grade.summary_table(rows, []).splitlines()
    head, body = lines[0].split(), lines[1].split()
    assert head[5:7] == ["exact", "clean"]
    assert body[4:7] == ["1.000", "1/2", "1/2"]


# ---------------------------------------------------------------------------
# CLI


def test_cli_end_to_end(tmp_path, truth, capsys):
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(json.dumps(truth))
    run = tmp_path / "run"
    run.mkdir()
    good = perfect_answer("job1", truth)
    half = {"flagged_deals": good["flagged_deals"][:2]}
    recs = [
        record("job1", good, n=1, rep=1),
        record("job1", half, n=2, rep=2),
        record("job1", None, error="no_block", n=3, rep=3),
        {**record("job4", None, error="no_block"), "n": 4, "failure_class": "upstream", "attempt": 1},
        {**record("job4", perfect_answer("job4", truth)), "n": 5, "attempt": 2, "rerun_of": 4},
        record("job5", perfect_answer("job5", truth), end_state=perfect_end_state("job5", truth), n=6),
    ]
    (run / "sessions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    (run / "jobs.jsonl").write_text(json.dumps({"id": "job5", "kind": "write"}) + "\n")

    assert grade.main([str(run), "--truth", str(truth_path)]) == 0
    rows = [json.loads(line) for line in (run / "grades.jsonl").read_text().splitlines()]
    assert len(rows) == 5  # the superseded upstream attempt is skipped
    assert [r["score"] for r in rows if r["task_id"] == "job1"] == [1.0, pytest.approx(2 / 3), 0.0]
    assert next(r for r in rows if r["task_id"] == "job5")["score"] == 1.0
    cons = json.loads((run / "consistency.json").read_text())
    assert any(c["task_id"] == "job1" and c["metric"] == "jaccard_flagged_mean" for c in cons)
    out = capsys.readouterr().out
    assert "job1" in out and "job4" in out and "job5" in out
    assert "superseded upstream/harness attempts (re-run, not graded): 1" in out

    every = tmp_path / "all.jsonl"
    assert grade.main([str(run), "--truth", str(truth_path), "--include-superseded", "--out", str(every)]) == 0
    assert len(every.read_text().splitlines()) == 6


# ---------------------------------------------------------------------------
# the real truth file


@pytest.mark.skipif(not REAL_TRUTH.exists(), reason="truth file not present on this machine")
class TestRealTruth:
    @pytest.mark.parametrize("task", grade.TASK_ORDER)
    def test_perfect_answer_gets_full_marks(self, real, task):
        kind = "write" if task in grade.WRITE_TASKS else "read"
        rec = record(
            task,
            perfect_answer(task, real),
            end_state=perfect_end_state(task, real) if kind == "write" else None,
        )
        row = grade.grade_session(rec, real, {"kind": kind})
        # Booleans only: a failed assert must not echo anything that came from the truth file.
        full = row["score"] == 1.0
        clean = row["violations"] == []
        exact = row["exact"] is True
        has_at_sign = "@" in json.dumps(row)
        assert full, f"{task} scored below 1.0 on checks {sorted(k for k, v in row['checks'].items() if v is not True and v != 1.0)}"
        assert clean, f"{task} has {len(row['violations'])} violation(s)"
        assert exact
        assert not has_at_sign

    def test_wrong_jane_is_caught(self, real):
        wj = real["job2"]["wrong_jane"]
        ans = perfect_answer("job2", real)
        ans["contact"].update(company=wj["company"], phone=wj["phone"], job_title=wj["jobtitle"])
        r = grade.grade_job2(ans, real, json.dumps(ans))
        assert r["checks"]["right_jane"] == 0.0 and len(r["violations"]) >= 2

    def test_dropping_a_flagged_deal_lowers_job1(self, real):
        ans = perfect_answer("job1", real)
        ans["flagged_deals"].pop()
        r = grade.grade_job1(ans, real)
        assert r["checks"]["precision"] == 1.0 and r["score"] < 1.0 and not r["exact"]

    def test_job4_unconverted_usd_is_flagged(self, real):
        ans = perfect_answer("job4", real)
        by_cur = real["job4"]["owners"][real["meta"]["demo_owner"]]["best_case"]["by_currency"]
        ans["figures"]["best_case"] = sum(c["native"] for c in by_cur.values())
        r = grade.grade_job4(ans, real)
        assert r["checks"]["figure.best_case"] is False
        assert r["details"]["figures"]["best_case"].get("matched_unconverted_native_sum") is True

    def test_job5_covers_every_past_due_deal(self, real):
        es = perfect_end_state("job5", real)
        first = real["job5"]["deals"][0]["dealname"]
        assert len(es["deals_target"]) == real["job5"]["count"]
        del es["deals_changed"][first]
        es["deals_target"][first]["closedate"] = "2026-07-01T00:00:00Z"
        r = grade.grade_job5(es, {}, real)
        assert r["details"]["deals_correct"] == real["job5"]["count"] - 1 and r["score"] < 1.0
