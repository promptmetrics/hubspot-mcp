"""The thin Pronova skills (arms C and D, and the demo) must not break the benchmark's answer block.

Reads the real skill files; skips when the plugin folder is absent (CI). Pins: the frontmatter name matches the folder
(the runner's skill-leak check compares names), every skill keeps the closing answer-block rule word for word, and no
skill holds a fenced json block (the runner reads only the last one in a session's answer, so an example invites a
copy).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILLS = Path("/Users/izzy/Documents/hubspot-plugin/skills")
NAMES = ["bulk-hygiene", "call-prep", "forecast", "log-meeting", "pipeline-triage"]
ANSWER_RULE = ("end with the block exactly as the task states it. "
               "The block replaces nothing above it and nothing follows it.")
RULE_LEAD = "- If the task asks for a closing JSON block, write the "
JSON_FENCE = re.compile(r"^[ \t]*```[ \t]*json", re.I | re.M)
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿]")

pytestmark = pytest.mark.skipif(not SKILLS.is_dir(), reason="hubspot-plugin skills folder not present")


def skill_files() -> list[Path]:
    return sorted(SKILLS.glob("*/SKILL.md")) if SKILLS.is_dir() else []


def frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", "SKILL.md must open with frontmatter"
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip().strip("'\"")
    raise AssertionError("frontmatter is not closed")


def test_the_five_skills_are_present():
    assert [p.parent.name for p in skill_files()] == NAMES


@pytest.mark.parametrize("path", skill_files(), ids=lambda p: p.parent.name)
def test_the_frontmatter_name_matches_the_folder(path):
    meta = frontmatter(path.read_text(encoding="utf-8"))
    assert meta.get("name") == path.parent.name
    assert meta.get("description")


@pytest.mark.parametrize("path", skill_files(), ids=lambda p: p.parent.name)
def test_the_answer_block_rule_is_present_and_last(path):
    text = path.read_text(encoding="utf-8")
    assert text.count(ANSWER_RULE) == 1
    last = text.rstrip("\n").splitlines()[-1]
    assert last.startswith(RULE_LEAD) and last.endswith(ANSWER_RULE)


@pytest.mark.parametrize("path", skill_files(), ids=lambda p: p.parent.name)
def test_no_fenced_json_block(path):
    assert not JSON_FENCE.search(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", skill_files(), ids=lambda p: p.parent.name)
def test_no_emoji_and_no_real_address(path):
    text = path.read_text(encoding="utf-8")
    assert not EMOJI.findall(text.replace("✓", ""))
    assert not re.findall(r"[\w.+-]+@(?!example\.com)[\w-]+\.[a-z]{2,}", text)


def rules_codes() -> set[str]:
    from hubspot_mcp.intent.rules import CODES
    return set(CODES)


def test_every_triage_code_a_skill_prints_is_a_code_the_rules_report():
    codes = rules_codes()
    for path in skill_files():
        text = path.read_text(encoding="utf-8")
        printed = set(re.findall(r"\b[A-Z]{3,}(?:_[A-Z]+)+\b", text))
        assert printed <= codes, f"{path.parent.name}: {sorted(printed - codes)}"


def test_no_skill_pairs_stalled_with_last_contact_evidence():
    for path in skill_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            assert not ("STALLED" in line and "last contact" in line), f"{path.parent.name}: {line.strip()}"


@pytest.mark.parametrize("name", ["log-meeting", "bulk-hygiene"])
def test_a_write_skill_has_a_path_for_a_rep_who_approved_in_the_task(name):
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    assert "has not already approved" in text
    assert "approved in the task" in text and "returned a preview that you confirmed" in text


def test_the_forecast_example_closes_its_own_arithmetic():
    text = (SKILLS / "forecast" / "SKILL.md").read_text(encoding="utf-8")
    commit = int(re.search(r"^Commit\s+XXX ([\d,]+)", text, re.M).group(1).replace(",", ""))
    supported = int(re.search(r"^Supported commit\s+XXX ([\d,]+)", text, re.M).group(1).replace(",", ""))
    removed = [int(m.replace(",", "")) for m in re.findall(r"XXX ([\d,]+) · commit · .*removed from supported commit", text)]
    assert commit - sum(removed) == supported
