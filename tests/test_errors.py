import pytest

from hubspot_mcp.errors import (
    ErrorCategory,
    HubSpotError,
    RateLimitError,
    ScopeError,
)


def test_hubspot_error_str():
    exc = HubSpotError("something failed", status_code=400)
    assert str(exc) == "HubSpotError(400): something failed"


def test_hubspot_error_defaults_to_unknown_category():
    exc = HubSpotError("something failed", status_code=400)
    assert exc.category == ErrorCategory.UNKNOWN
    assert exc.field_errors is None


def test_hubspot_error_with_category_and_field_errors():
    exc = HubSpotError(
        "validation failed",
        status_code=400,
        category=ErrorCategory.VALIDATION,
        field_errors=[{"field": "email", "message": "invalid"}],
    )
    assert exc.category == ErrorCategory.VALIDATION
    assert exc.field_errors == [{"field": "email", "message": "invalid"}]


def test_rate_limit_error():
    exc = RateLimitError("Rate limit exceeded", retry_after=30)
    assert exc.status_code == 429
    assert exc.retry_after == 30
    assert exc.category == ErrorCategory.RATE_LIMIT


def test_scope_error():
    exc = ScopeError("Missing scopes", required_scopes=["crm.objects.contacts.read"])
    assert exc.status_code == 403
    assert exc.required_scopes == ["crm.objects.contacts.read"]
    assert exc.category == ErrorCategory.SCOPE


def test_error_category_values():
    assert ErrorCategory.VALIDATION == "VALIDATION"
    assert ErrorCategory.AUTH == "AUTH"
    assert ErrorCategory.SCOPE == "SCOPE"
    assert ErrorCategory.NOT_FOUND == "NOT_FOUND"
    assert ErrorCategory.CONFLICT == "CONFLICT"
    assert ErrorCategory.RATE_LIMIT == "RATE_LIMIT"
    assert ErrorCategory.SERVER == "SERVER"
    assert ErrorCategory.UNKNOWN == "UNKNOWN"
