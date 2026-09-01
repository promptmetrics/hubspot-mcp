from hubspot_mcp.agents.service import get_service_agent_prompt


def test_service_agent_prompt_has_correct_tools():
    prompt = get_service_agent_prompt()
    assert prompt.agent_name == "Service Agent"
    expected = [
        "hubspot_get_knowledge_base_article",
        "hubspot_list_kb_articles",
        "hubspot_get_ticket_pipeline",
        "hubspot_create_ticket_pipeline",
        "hubspot_list_service_automation",
        "hubspot_get_feedback_survey",
    ]
    assert sorted(prompt.tool_names) == sorted(expected)
    assert "knowledge base" in prompt.system_prompt
    assert "ticket" in prompt.system_prompt
