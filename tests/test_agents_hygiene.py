from hubspot_mcp.agents.hygiene import get_hygiene_agent_prompt


def test_hygiene_agent_prompt_has_correct_tools():
    prompt = get_hygiene_agent_prompt()
    assert prompt.agent_name == "Hygiene Agent"
    expected = [
        "hubspot_find_duplicates",
        "hubspot_merge_objects",
        "hubspot_bulk_update_objects",
        "hubspot_preview_segment",
    ]
    assert sorted(prompt.tool_names) == sorted(expected)
