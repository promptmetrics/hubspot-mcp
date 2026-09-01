from hubspot_mcp.agents.analytics import get_analytics_agent_prompt


def test_analytics_agent_prompt_has_reporting_tools():
    prompt = get_analytics_agent_prompt()
    assert "hubspot_create_report" in prompt.tool_names
    assert "hubspot_create_dashboard" in prompt.tool_names
    assert "hubspot_schedule_email" in prompt.tool_names


def test_analytics_agent_prompt_mentions_dashboards_and_email():
    prompt = get_analytics_agent_prompt()
    assert "dashboard" in prompt.domain_description.lower()
    assert "email" in prompt.domain_description.lower()


def test_analytics_agent_prompt_has_write_tools():
    prompt = get_analytics_agent_prompt()
    write_tools = [t for t in prompt.tool_names if any(kw in t for kw in ("create", "update", "delete", "batch"))]
    assert len(write_tools) > 0
    assert "create" in prompt.system_prompt.lower()
