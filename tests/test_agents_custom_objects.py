from hubspot_mcp.agents.custom_objects import get_custom_objects_agent_prompt


def test_custom_objects_agent_prompt_has_correct_tools():
    prompt = get_custom_objects_agent_prompt()
    assert prompt.agent_name == "Custom Objects Agent"
    expected = [
        "hubspot_get_object",
        "hubspot_search_objects",
        "hubspot_create_object",
        "hubspot_update_object",
        "hubspot_delete_object",
        "hubspot_batch_upsert_objects",
    ]
    assert sorted(prompt.tool_names) == sorted(expected)
