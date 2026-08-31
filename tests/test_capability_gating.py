"""Portal capability gating for the advertised tool surface.

Upstream maps agents to capabilities and explains a refusal at dispatch. This
server exposes tools directly, so it does two things: unadvertise tools the
portal is definitively not entitled to, and explain a refusal at call time for
anything still advertised.

The distinction that matters is conclusive vs transient. ``CapabilityMatrix``
defaults workflows/users/marketing/cms/custom_objects to False, and a transient
probe failure returns those defaults uncached -- so filtering ``tools/list`` on
an inconclusive probe would silently drop a dozen tools after one blip.
"""
from __future__ import annotations

import pytest

from hubspot_mcp.capabilities import (
    CapabilityMatrix,
    missing_capabilities_for_tool,
    tool_capability_requirements,
)

GATED_SAMPLE = [
    ("hubspot_list_workflows", "workflows"),
    ("hubspot_list_users", "users"),
    ("hubspot_list_kb_articles", "service_automation"),
]


def test_tool_map_is_derived_not_hand_maintained():
    """Every gated tool must belong to an agent that declares the capability."""
    from hubspot_mcp.capabilities import _AGENT_CAPABILITY_REQUIREMENTS
    from hubspot_mcp.scope_registry import _AGENT_TOOLS

    mapping = tool_capability_requirements()
    assert mapping, "no tools are capability-gated — the derivation broke"
    for tool_name, features in mapping.items():
        owners = [a for a, tools in _AGENT_TOOLS.items() if tool_name in tools]
        declared = {f for a in owners for f in _AGENT_CAPABILITY_REQUIREMENTS.get(a, [])}
        assert set(features) <= declared


@pytest.mark.parametrize("tool_name,feature", GATED_SAMPLE)
def test_missing_capability_is_reported(tool_name, feature):
    entitled = CapabilityMatrix(**{feature: True})
    not_entitled = CapabilityMatrix(**{feature: False})
    assert missing_capabilities_for_tool(tool_name, entitled) == []
    assert feature in missing_capabilities_for_tool(tool_name, not_entitled)


def test_ungated_tools_are_never_blocked():
    """A core CRM tool must not acquire a capability requirement."""
    bare = CapabilityMatrix()
    for tool_name in ("hubspot_get_object", "hubspot_search_objects", "hubspot_update_object"):
        assert missing_capabilities_for_tool(tool_name, bare) == []


class TestConclusiveness:
    """A transient probe failure must not be mistaken for 'not entitled'."""

    def test_transient_failure_is_not_conclusive(self, tmp_path, monkeypatch):
        from hubspot_mcp import capabilities

        monkeypatch.setattr(capabilities.Path, "home", lambda: tmp_path)
        # Nothing cached: probe_portal only writes the cache when every probe
        # returned a definitive answer.
        assert capabilities.probe_was_conclusive("99999999") is False

    def test_defaults_would_hide_tools_if_treated_as_truth(self):
        """Documents exactly what the conclusiveness gate protects against."""
        blank = CapabilityMatrix()
        hidden = [t for t in tool_capability_requirements() if missing_capabilities_for_tool(t, blank)]
        assert len(hidden) >= 12, (
            "a default matrix hides this many tools — which is why "
            "_unadvertise_unavailable_tools requires a conclusive probe"
        )
