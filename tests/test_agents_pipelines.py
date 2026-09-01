from hubspot_mcp.agents.pipelines import get_pipelines_agent_prompt


def test_pipelines_agent_prompt_has_correct_tools():
    prompt = get_pipelines_agent_prompt()
    assert prompt.agent_name == "Pipelines Agent"
    expected = [
        "hubspot_get_pipeline",
        "hubspot_list_pipelines",
        "hubspot_create_pipeline",
        "hubspot_update_pipeline",
        "hubspot_reorder_stages",
    ]
    assert sorted(prompt.tool_names) == sorted(expected)
