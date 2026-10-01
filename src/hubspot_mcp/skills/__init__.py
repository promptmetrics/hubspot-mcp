"""HubSpot's Sales skills, served from this server.

The nine ``SKILL.md`` files under this package are HubSpot's "HubSpot Sales"
Claude plugin 2.3.0, vendored unmodified (see NOTICE and LICENSE beside them).
Two helper files under ``_helpers/`` are the plugin's sub-agent recipes; an MCP
host has no sub-agents to delegate to, so they are served as loadable text and
the skill header tells the model to run their fetches itself.

Everything is read through ``importlib.resources`` so a source checkout and an
installed wheel resolve identically, the same way the workflow blueprints do.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files

HELPER_DIR = "_helpers"

# The names HubSpot's official connector gives its tools, as the skills use
# them. Inferring a skill's tools from these mentions is the same rule the
# plugin's own tooling relies on; the skills declare no allowed-tools list.
CONNECTOR_TOOL_NAMES: tuple[str, ...] = (
    "search_crm_objects",
    "get_crm_objects",
    "manage_crm_objects",
    "get_user_details",
    "get_organization_details",
    "discover_hubspot_schema",
    "search_properties",
    "get_properties",
    "search_owners",
    "tool_guidance",
)
_TOOL_RE = re.compile(r"\b(" + "|".join(CONNECTOR_TOOL_NAMES) + r")\b")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_QUOTED_RE = re.compile(r"[\"“]([^\"”]{3,80})[\"”]")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str  # the full file, frontmatter included, byte-identical to the vendored copy
    tools: tuple[str, ...]  # connector tool names the text mentions
    triggers: tuple[str, ...]  # quoted phrases from the description, for keyword routing
    helper: bool = False


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal YAML front matter: ``key: value`` and ``key: >`` folded blocks."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    key: str | None = None
    folded: list[str] = []
    for line in m.group(1).splitlines():
        if line and not line[0].isspace() and ":" in line:
            if key is not None:
                out[key] = " ".join(folded).strip()
            key, _, rest = line.partition(":")
            key = key.strip()
            rest = rest.strip()
            folded = [] if rest in (">", "|", "") else [rest]
        elif key is not None:
            folded.append(line.strip())
    if key is not None:
        out[key] = " ".join(folded).strip()
    return out


def _load(name: str, text: str, helper: bool) -> Skill:
    fm = _parse_frontmatter(text)
    description = fm.get("description", "")
    return Skill(
        name=fm.get("name", name),
        description=description,
        body=text,
        tools=tuple(sorted(set(_TOOL_RE.findall(text)))),
        triggers=tuple(dict.fromkeys(t.strip() for t in _QUOTED_RE.findall(description))),
        helper=helper,
    )


@lru_cache(maxsize=1)
def load_skills() -> dict[str, Skill]:
    """Name → Skill for the nine skills and the two helpers."""
    root = files(__package__)
    out: dict[str, Skill] = {}
    for entry in root.iterdir():
        if entry.name.startswith(("_", ".")) or not entry.is_dir():
            continue
        skill_file = entry / "SKILL.md"
        if skill_file.is_file():
            skill = _load(entry.name, skill_file.read_text(encoding="utf-8"), helper=False)
            out[skill.name] = skill
    helpers = root / HELPER_DIR
    if helpers.is_dir():
        for entry in helpers.iterdir():
            if entry.name.endswith(".md"):
                skill = _load(entry.name[:-3], entry.read_text(encoding="utf-8"), helper=True)
                out[skill.name] = skill
    return out


def skill_names() -> list[str]:
    return sorted(n for n, s in load_skills().items() if not s.helper)


def helper_names() -> list[str]:
    return sorted(n for n, s in load_skills().items() if s.helper)


@dataclass
class SkillScore:
    name: str
    score: int
    hits: list[str] = field(default_factory=list)


_STOP = frozenset(
    "the and for with this that from not are its per any all can into only has have been but was will one each "
    "them they you your what which when user wants want need needs use skill show give get make tell about "
    "today day my me our we i a an of to in on at by or is it do does just like".split()
)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z'-]{2,}", text.lower()) if w not in _STOP}


@lru_cache(maxsize=1)
def _skill_vocab() -> dict[str, set[str]]:
    """Per skill: the distinctive words of its triggers and name, minus words shared by 3+ skills."""
    raw = {
        n: _words(" ".join(load_skills()[n].triggers) + " " + n.replace("-", " "))
        for n in skill_names()
    }
    common = {w for w in set().union(*raw.values()) if sum(w in v for v in raw.values()) >= 3}
    return {n: v - common for n, v in raw.items()}


def keyword_rank(request_text: str) -> list[SkillScore]:
    """Deterministic fallback: trigger phrases (weight 3) and distinctive trigger words (weight 1).

    Same shape as ``agent_routing.route_request``: best first, empty when
    nothing matches. The ``hubspot`` skill's triggers are generic, so ties
    break away from it.
    """
    text = request_text.lower()
    words = _words(text)
    ranked: list[SkillScore] = []
    for name in skill_names():
        skill = load_skills()[name]
        hits = [t for t in skill.triggers if re.search(r"\b" + re.escape(t.lower()) + r"\b", text)]
        word_hits = sorted(_skill_vocab()[name] & words)
        score = 3 * len(hits) + len(word_hits)
        # One stray word is not a match; a phrase hit or two distinctive words is.
        if score >= 2:
            ranked.append(SkillScore(name, score, hits + word_hits))
    ranked.sort(key=lambda s: (-s.score, s.name == "hubspot", s.name))
    return ranked
