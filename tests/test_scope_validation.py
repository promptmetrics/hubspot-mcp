from hubspot_mcp.validation import format_scope_error, validate_scopes


def test_validate_scopes_returns_empty_when_all_granted():
    blocked = validate_scopes(
        ["objects", "properties"],
        [
            "crm.objects.contacts.read",
            "crm.objects.contacts.write",
            "crm.objects.contacts.delete",
            "crm.schemas.contacts.read",
            "crm.schemas.contacts.write",
            "crm.schemas.contacts.delete",
        ],
        target_object="contacts",
    )
    assert blocked == {}


def test_validate_scopes_reports_missing_per_agent():
    blocked = validate_scopes(
        ["objects"],
        ["crm.objects.contacts.read"],
        target_object="contacts",
    )
    assert blocked == {"objects": ["crm.objects.contacts.delete", "crm.objects.contacts.write"]}


def test_validate_scopes_accepts_none_portal_scopes():
    blocked = validate_scopes(["lists"], None)
    assert blocked == {"lists": ["crm.lists.read", "crm.lists.write"]}


def test_format_scope_error_empty():
    assert format_scope_error({}) == ""


def test_format_scope_error_lists_missing_scopes():
    text = format_scope_error({"objects": ["crm.objects.contacts.write"]})
    assert "Missing HubSpot OAuth scopes:" in text
    assert "objects: crm.objects.contacts.write" in text
