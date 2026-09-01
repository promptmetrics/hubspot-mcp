from hubspot_mcp.agents.users import get_users_agent_prompt


def test_users_agent_prompt_has_correct_tools():
    prompt = get_users_agent_prompt()
    assert prompt.agent_name == "Users Agent"
    expected = [
        "hubspot_get_user",
        "hubspot_list_users",
        "hubspot_create_user",
        "hubspot_update_user",
        "hubspot_deactivate_user",
    ]
    assert sorted(prompt.tool_names) == sorted(expected)
