from hubspot_mcp.agents.properties import get_properties_agent_prompt


def test_properties_agent_prompt_has_correct_tools():
    prompt = get_properties_agent_prompt()
    assert prompt.agent_name == "Properties Agent"
    expected = [
        "hubspot_get_property",
        "hubspot_list_properties",
        "hubspot_create_property",
        "hubspot_update_property",
        "hubspot_delete_property",
    ]
    assert sorted(prompt.tool_names) == sorted(expected)
