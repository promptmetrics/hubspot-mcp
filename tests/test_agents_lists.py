from hubspot_mcp.agents.lists import get_lists_agent_prompt


def test_lists_agent_prompt_has_correct_tools():
    prompt = get_lists_agent_prompt()
    assert prompt.agent_name == "Lists Agent"
    expected = [
        "hubspot_get_list",
        "hubspot_list_lists",
        "hubspot_create_list",
        "hubspot_update_list",
        "hubspot_add_to_list",
        "hubspot_remove_from_list",
    ]
    assert sorted(prompt.tool_names) == sorted(expected)
