from hubspot_mcp.scope_registry import get_required_scopes, get_required_scopes_for_agent


def test_read_object_scopes():
    scopes = get_required_scopes(["hubspot_get_object"], target_object="contacts")
    assert scopes == {"crm.objects.contacts.read"}


def test_create_object_scopes():
    scopes = get_required_scopes(["hubspot_create_object"], target_object="contacts")
    assert scopes == {"crm.objects.contacts.write"}


def test_delete_object_scopes():
    scopes = get_required_scopes(["hubspot_delete_object"], target_object="contacts")
    assert scopes == {"crm.objects.contacts.write", "crm.objects.contacts.delete"}


def test_object_scopes_default_to_all_standard_types_when_object_unknown():
    scopes = get_required_scopes(["hubspot_update_object"])
    assert "crm.objects.contacts.write" in scopes
    assert "crm.objects.companies.write" in scopes
    assert "crm.objects.deals.write" in scopes
    assert "crm.objects.tickets.write" in scopes


def test_property_scopes_use_schema_family():
    scopes = get_required_scopes(["hubspot_create_property"], target_object="companies")
    assert scopes == {"crm.schemas.companies.write"}


def test_workflow_scopes():
    scopes = get_required_scopes(["hubspot_create_workflow"])
    assert scopes == {"automation"}


def test_list_scopes():
    assert get_required_scopes(["hubspot_list_lists"]) == {"crm.lists.read"}
    assert get_required_scopes(["hubspot_create_list"]) == {"crm.lists.write"}


def test_user_scopes():
    assert get_required_scopes(["hubspot_list_users"]) == {"settings.users.read"}
    assert get_required_scopes(["hubspot_create_user"]) == {"settings.users.write"}


def test_multiple_tools_merge_scopes():
    scopes = get_required_scopes(
        ["hubspot_get_object", "hubspot_create_object"],
        target_object="deals",
    )
    assert scopes == {"crm.objects.deals.read", "crm.objects.deals.write"}


def test_agent_scope_lookup():
    scopes = get_required_scopes_for_agent("objects", target_object="contacts")
    assert "crm.objects.contacts.read" in scopes
    assert "crm.objects.contacts.write" in scopes
    assert "crm.objects.contacts.delete" in scopes


def test_unknown_agent_returns_empty_set():
    assert get_required_scopes_for_agent("not_an_agent") == set()
