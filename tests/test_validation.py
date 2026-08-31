import pytest

from hubspot_mcp.cache import SchemaCache
from hubspot_mcp.validation import (
    ValidationError,
    filter_writable_properties,
    validate_properties,
    _type_compatible,
)


@pytest.fixture
def cache(tmp_path):
    cache = SchemaCache("123", base_dir=tmp_path)
    cache.set(
        "contacts",
        {
            "results": [
                {"name": "email", "type": "string"},
                {"name": "phone", "type": "string"},
                {"name": "age", "type": "number"},
                {"name": "is_customer", "type": "bool"},
                {"name": "lifecyclestage", "type": "enumeration"},
                {"name": "createdate", "type": "date"},
            ]
        },
    )
    return cache


def test_validation_all_valid(cache, tmp_path):
    result = validate_properties(
        "contacts",
        {"email": "test@example.com", "age": 30, "is_customer": True},
        "123",
        base_dir=tmp_path,
    )
    assert result["valid"] is True
    assert result["errors"] == []


def test_validation_unknown_property(cache, tmp_path):
    result = validate_properties(
        "contacts",
        {"emial": "test@example.com"},
        "123",
        base_dir=tmp_path,
    )
    assert result["valid"] is False
    assert result["errors"][0]["reason"] == "unknown_property"
    assert "email" in result["errors"][0]["suggestions"]


def test_validation_type_mismatch_string(cache, tmp_path):
    result = validate_properties(
        "contacts",
        {"age": "thirty"},
        "123",
        base_dir=tmp_path,
    )
    assert result["valid"] is False
    assert "type_mismatch" in result["errors"][0]["reason"]


def test_validation_type_mismatch_bool(cache, tmp_path):
    result = validate_properties(
        "contacts",
        {"is_customer": "yes"},
        "123",
        base_dir=tmp_path,
    )
    assert result["valid"] is False
    assert "type_mismatch" in result["errors"][0]["reason"]


def test_validation_no_schema(tmp_path):
    result = validate_properties(
        "contacts",
        {"email": "test@example.com"},
        "123",
        base_dir=tmp_path,
    )
    assert result["valid"] is True
    assert result["errors"] == []


def test_type_compatible_string():
    assert _type_compatible("hello", "string") is True
    assert _type_compatible(123, "string") is False


def test_type_compatible_number():
    assert _type_compatible(42, "number") is True
    assert _type_compatible(3.14, "number") is True
    assert _type_compatible(True, "number") is False
    assert _type_compatible("42", "number") is False


def test_type_compatible_bool():
    assert _type_compatible(True, "bool") is True
    assert _type_compatible(False, "bool") is True
    assert _type_compatible("true", "bool") is False


def test_type_compatible_none():
    assert _type_compatible(None, "string") is True
    assert _type_compatible(None, "number") is True
    assert _type_compatible(None, "bool") is True


def test_validation_allows_none_value(cache, tmp_path):
    result = validate_properties(
        "contacts",
        {"email": None},
        "123",
        base_dir=tmp_path,
    )
    assert result["valid"] is True
    assert result["errors"] == []


# ---------------------------------------------------------------------------
# Bug B (0.2.4): undo replayed snapshots verbatim, including read-only system
# fields HubSpot rejects with a 400.  filter_writable_properties strips them:
# via modificationMetadata.readOnlyValue when the cached schema carries it
# (standard objects — warm_standard_schemas stores the raw /crm/v3/properties
# body), via a static system-field denylist otherwise (custom objects, whose
# cached schemas drop modificationMetadata).
# ---------------------------------------------------------------------------


def test_filter_writable_uses_schema_metadata(tmp_path):
    schema_cache = SchemaCache("123", base_dir=tmp_path)
    schema_cache.set(
        "contacts",
        {
            "results": [
                {"name": "email", "type": "string", "modificationMetadata": {"readOnlyValue": False}},
                {"name": "days_to_close", "type": "number", "modificationMetadata": {"readOnlyValue": True}},
            ]
        },
    )
    kept, stripped = filter_writable_properties(
        "contacts",
        {"email": "old@example.com", "days_to_close": "9"},
        "123",
        base_dir=tmp_path,
    )
    assert kept == {"email": "old@example.com"}
    assert stripped == ["days_to_close"]


def test_filter_writable_static_fallback_without_schema(tmp_path):
    kept, stripped = filter_writable_properties(
        "contacts",
        {
            "email": "old@example.com",
            "hs_object_id": "1",
            "createdate": "2026-01-01T00:00:00Z",
            "hs_lastmodifieddate": "2026-01-02T00:00:00Z",
            "hs_lead_status": "NEW",  # hs_* but writable — must survive
        },
        "123",
        base_dir=tmp_path,
    )
    assert kept == {"email": "old@example.com", "hs_lead_status": "NEW"}
    assert sorted(stripped) == ["createdate", "hs_lastmodifieddate", "hs_object_id"]


def test_filter_writable_custom_object_static_fallback(tmp_path):
    # discover_custom_schemas caches only {name, type} — no modificationMetadata —
    # so custom objects rely on the static system-field denylist.
    schema_cache = SchemaCache("123", base_dir=tmp_path)
    schema_cache.set("machines", {"results": [{"name": "serial", "type": "string"}]})
    kept, stripped = filter_writable_properties(
        "machines",
        {"serial": "SN-1", "hs_object_id": "9"},
        "123",
        base_dir=tmp_path,
    )
    assert kept == {"serial": "SN-1"}
    assert stripped == ["hs_object_id"]
