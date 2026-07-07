from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_REDACTION_LEVELS = frozenset({"off", "pii", "full"})

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\+?[\d\s\-\(\)]{7,20}")


def _hash_placeholder(label: str, value: str) -> str:
    short = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"<{label}:{short}>"


def redact(obj: Any, level: str = "pii") -> Any:
    """Recursively redact PII from a JSON-serializable object.

    Levels:
      - 'off':   return obj unchanged
      - 'pii':   mask emails, phone numbers, and long strings that look like names
      - 'full':  mask every string longer than 3 chars
    """
    if level not in _REDACTION_LEVELS:
        raise ValueError(f"Invalid redaction level: {level}")

    if level == "off":
        return obj

    if isinstance(obj, dict):
        return {k: redact(v, level) for k, v in obj.items()}

    if isinstance(obj, list):
        return [redact(v, level) for v in obj]

    if isinstance(obj, str):
        return _redact_string(obj, level)

    return obj


def _redact_string(value: str, level: str) -> str:
    if level == "full":
        if len(value) > 3:
            return _hash_placeholder("redacted", value)
        return value

    # level == "pii"
    result = _EMAIL_RE.sub(lambda m: _hash_placeholder("email", m.group(0)), value)

    def _phone_replace(m: re.Match[str]) -> str:
        text = m.group(0)
        # Skip date-like fragments (YYYY-MM-DD, DD-MM-YYYY, YYYY-MM, etc.)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4}|\d{2}-\d{2}-\d{2}|\d{4}-\d{2}", text):
            return text
        return _hash_placeholder("phone", text)

    result = _PHONE_RE.sub(_phone_replace, result)

    # Heuristic: long strings that are mostly letters and spaces (name-like)
    if len(result) > 20 and result == value and _looks_like_name(value):
        result = _hash_placeholder("name", value)

    return result


def _looks_like_name(value: str) -> bool:
    # Exclude URLs and domain-like strings from name heuristic
    if "://" in value or value.startswith("http") or ".com" in value or ".org" in value:
        return False
    # Simple heuristic: mostly alphabetic characters and spaces, no digits
    alpha_or_space = sum(1 for c in value if c.isalpha() or c.isspace())
    return alpha_or_space / len(value) > 0.8 and not any(c.isdigit() for c in value)


def redact_dict_for_disk(data: dict[str, Any], level: str = "pii") -> dict[str, Any]:
    """Convenience wrapper for logging entries before they hit disk."""
    return redact(data, level)
