from hubspot_mcp.agents.analytics import get_analytics_agent_prompt


def test_analytics_agent_prompt_has_correct_tools():
    prompt = get_analytics_agent_prompt()
    assert prompt.agent_name == "Analytics Agent"
    expected = [
        "hubspot_get_analytics_report",
        "hubspot_calculate_metrics",
        "hubspot_pipeline_velocity",
        "hubspot_create_report",
        "hubspot_create_dashboard",
        "hubspot_schedule_email",
    ]
    assert sorted(prompt.tool_names) == sorted(expected)
