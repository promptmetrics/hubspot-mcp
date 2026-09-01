from hubspot_mcp.agents.engagements import get_engagements_agent_prompt


def test_engagements_agent_prompt_has_correct_tools():
    prompt = get_engagements_agent_prompt()
    assert prompt.agent_name == "Engagements Agent"
    expected = [
        "hubspot_get_engagement",
        "hubspot_search_engagements",
        "hubspot_create_note",
        "hubspot_create_task",
        "hubspot_create_email",
        "hubspot_create_meeting",
        "hubspot_create_call",
    ]
    assert sorted(prompt.tool_names) == sorted(expected)
