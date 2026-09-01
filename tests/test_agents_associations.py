from hubspot_mcp.agents.associations import get_associations_agent_prompt


def test_associations_agent_prompt_has_correct_tools():
    prompt = get_associations_agent_prompt()
    assert prompt.agent_name == "Associations Agent"
    expected = [
        "hubspot_get_association_schema",
        "hubspot_create_association_schema",
        "hubspot_associate_records",
        "hubspot_disassociate_records",
        "hubspot_list_associated_records",
    ]
    assert sorted(prompt.tool_names) == sorted(expected)
