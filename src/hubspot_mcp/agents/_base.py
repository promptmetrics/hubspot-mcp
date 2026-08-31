from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hubspot_mcp.config import PortalConfig
from hubspot_mcp.research import RESEARCH_PROMPT_BLOCK
from hubspot_mcp.tools import ToolDef, list_tools

SELF_CORRECTION_PROMPT_BLOCK = """\
## Self-correction rules

When a tool call returns an error, attempt to resolve it before giving up:

1. **VALIDATION errors** — examine the failing fields. If a property name is
   rejected, check for typos against known schema fields and retry with the
   corrected name. If a value has the wrong type, cast or reformat it.

2. **CONFLICT errors** — search for existing records that may be causing the
   conflict. If a duplicate is found, suggest a merge strategy or update the
   existing record instead of creating a new one.

3. **NOT_FOUND errors** — search for alternatives (e.g., similar object IDs,
   related records, or lists/workflows by a similar name). Report missing
   prerequisites clearly if no alternative exists.

If you produce a corrected payload after resolving an error, return it in
your response as structured JSON with these fields:

  {
    "status": "corrected",
    "correction_reason": "<brief explanation of what was wrong and how it was fixed>",
    "corrected_payload": { <the fixed payload> }
  }

Corrected payloads must re-enter the human-in-the-loop approval flow. Do not
execute a corrected write without user approval.
"""

REFLECTION_PROMPT_BLOCK = """\
## Write verification (reflection)

After executing any CREATE, UPDATE, or BATCH write operation, you MUST
verify the write succeeded before reporting success to the user:

1. Re-fetch the affected record using the read tool (e.g. `hubspot_get_object`
   or the equivalent read operation for the domain).
2. Compare the returned properties against the values you intended to write.
3. Report any mismatches or missing fields clearly.
4. If verification fails, surface the discrepancy and do not claim success.

Return verification results as structured JSON when available:

  {
    "status": "verified" | "mismatch",
    "verified": true | false,
    "mismatches": [{"field": "...", "expected": "...", "actual": "..."}],
    "missing_fields": ["..."]
  }
"""

TERSE_OUTPUT_PROMPT_BLOCK = """\
## Output rules

You are terse and final-result-oriented. The user is non-technical.
- Do not narrate steps, reasoning, or process. No "Let me…", "Now I'll…".
- Return only the final result the parent needs to surface to the user.
- Carve-out for writes: when you produce a write preview, you MUST include
  everything the preview returns — action_id, affected records with the
  exact field changes (current → proposed values), and (for destructive ops)
  the required count. Never suppress or abbreviate the preview. State
  blockers in one line.
- Return verification verdicts (verified/mismatch/partial/error) as-is with
  the mismatches list; do not pad them with narration.
"""


@dataclass
class AgentPrompt:
    agent_name: str
    system_prompt: str
    tool_names: list[str] = field(default_factory=list)
    domain_description: str = ""


def format_tool_descriptions(tools: list[ToolDef]) -> str:
    lines: list[str] = []
    for t in tools:
        lines.append(f"- {t.name}: {t.description}")
    return "\n".join(lines)


def build_agent_prompt(
    agent_name: str,
    domain_description: str,
    available_tools: list[ToolDef],
    portal_config: PortalConfig | None = None,
) -> AgentPrompt:
    tool_list = format_tool_descriptions(available_tools)
    portal_info = ""
    if portal_config:
        portal_info = (
            f"\nPortal context:\n"
            f"- Portal ID: {portal_config.portal_id}\n"
            f"- Tier: {portal_config.tier}\n"
        )

    has_write_tools = any(
        any(kw in t.name for kw in ("create", "update", "delete", "batch"))
        for t in available_tools
    )

    system_prompt = (
        f"You are the {agent_name} for HubSpot CRM.\n\n"
        f"{domain_description}\n\n"
        f"Available tools:\n{tool_list}\n"
        f"{portal_info}\n"
        f"Instructions:\n"
        f"- Use the available tools to fulfill the user's request.\n"
        f"- Always return results as structured JSON.\n"
        f"- If a tool returns an error, surface it clearly with the tool name.\n"
        f"- For write operations, confirm the action before executing.\n"
        f"- If the request is ambiguous, ask for clarification.\n"
        f"- Date/datetime filters: HubSpot stores date and datetime property "
        f"values as **epoch-milliseconds** (e.g. `createdate`, `closedate`, "
        f"`hs_lastmodifieddate`, `lastmodifieddate`). When you build a search "
        f"filter on a date/datetime property, express the comparison `value` as "
        f"epoch-milliseconds — NOT an ISO-8601 string and NOT seconds-epoch. "
        f"An ISO string or seconds value mis-compares and silently returns "
        f"wrong or empty results. Convert the user's date to epoch-ms "
        f"(milliseconds since 1970-01-01 UTC) before sending the filter.\n"
        f"\n{TERSE_OUTPUT_PROMPT_BLOCK}\n"
        f"\n{SELF_CORRECTION_PROMPT_BLOCK}\n"
        f"\n{RESEARCH_PROMPT_BLOCK}\n"
    )
    if has_write_tools:
        system_prompt += f"\n{REFLECTION_PROMPT_BLOCK}\n"

    return AgentPrompt(
        agent_name=agent_name,
        system_prompt=system_prompt,
        tool_names=[t.name for t in available_tools],
        domain_description=domain_description,
    )
