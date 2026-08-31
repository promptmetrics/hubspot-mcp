import pytest

from hubspot_mcp.redaction import redact, redact_dict_for_disk, _looks_like_name


def test_redact_off_returns_unchanged():
    data = {"email": "secret@example.com", "phone": "+1-555-1234"}
    assert redact(data, level="off") == data


def test_redact_pii_email():
    result = redact("Contact secret@example.com for details", level="pii")
    assert "secret@example.com" not in result
    assert "<email:" in result


def test_redact_pii_phone():
    result = redact("Call +1-555-123-4567", level="pii")
    assert "+1-555-123-4567" not in result
    assert "<phone:" in result


def test_redact_pii_name_heuristic():
    long_name = "Jonathan Bartholomew III"
    result = redact(long_name, level="pii")
    assert long_name not in result
    assert "<name:" in result


def test_redact_pii_short_name_preserved():
    result = redact("Jon", level="pii")
    assert result == "Jon"


def test_redact_full_masks_all_long_strings():
    result = redact({"name": "Alice", "city": "NYC"}, level="full")
    assert "Alice" not in str(result)
    assert "NYC" in str(result)  # 3 chars, not masked


def test_redact_nested_dict():
    data = {
        "user": {"email": "a@b.com", "phone": "555-1234"},
        "tags": ["vip", "active"],
    }
    result = redact(data, level="pii")
    assert "a@b.com" not in str(result)
    assert "555-1234" not in str(result)
    assert "vip" in str(result)


def test_redact_list():
    data = ["a@b.com", "c@d.com", "plain text"]
    result = redact(data, level="pii")
    assert "a@b.com" not in str(result)
    assert "c@d.com" not in str(result)
    assert "plain text" in str(result)


def test_redact_numbers_untouched():
    assert redact(42, level="full") == 42
    assert redact(True, level="full") is True


def test_redact_invalid_level_raises():
    with pytest.raises(ValueError):
        redact("hello", level="invalid")


def test_redact_dict_for_disk():
    data = {"email": "user@example.com", "count": 5}
    result = redact_dict_for_disk(data)
    assert "user@example.com" not in str(result)


def test_looks_like_name_true():
    assert _looks_like_name("Alice Wonderland") is True


def test_looks_like_name_false_digits():
    assert _looks_like_name("Alice 123") is False


def test_looks_like_name_short():
    assert _looks_like_name("AB") is True  # 2 chars, alpha_or_space ratio is 1.0


def test_redaction_deterministic():
    r1 = redact("a@b.com", level="pii")
    r2 = redact("a@b.com", level="pii")
    assert r1 == r2


def test_redact_none_returns_none():
    assert redact(None, level="pii") is None


def test_redact_empty_string():
    assert redact("", level="pii") == ""
    assert redact("", level="full") == ""


def test_redact_preserves_url():
    url = "https://developers.hubspot.com/docs"
    assert redact(url, level="pii") == url


def test_redact_preserves_domain():
    domain = "www.example.com"
    assert redact(domain, level="pii") == domain


def test_redact_preserves_timestamp():
    ts = "2026-05-08T12:34:56+00:00"
    assert redact(ts, level="pii") == ts


def test_redact_full_boundary():
    assert redact("abc", level="full") == "abc"  # 3 chars, preserved
    assert redact("abcd", level="full") != "abcd"  # 4 chars, masked


def test_redact_dict_for_disk_custom_level():
    data = {"name": "Alice Wonderland"}
    result = redact_dict_for_disk(data, level="full")
    assert "Alice Wonderland" not in str(result)
