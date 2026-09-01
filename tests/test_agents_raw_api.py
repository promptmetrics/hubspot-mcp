from hubspot_mcp.agents.raw_api import get_raw_api_agent_prompt


def test_raw_api_agent_prompt_has_correct_tools():
    prompt = get_raw_api_agent_prompt()
    assert prompt.agent_name == "Raw API Agent"
    assert prompt.tool_names == ["hubspot_raw_api"]
