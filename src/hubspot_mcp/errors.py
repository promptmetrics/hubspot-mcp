from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    VALIDATION = "VALIDATION"
    AUTH = "AUTH"
    SCOPE = "SCOPE"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMIT = "RATE_LIMIT"
    SERVER = "SERVER"
    UNKNOWN = "UNKNOWN"


class HubSpotError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        category: ErrorCategory | None = None,
        field_errors: list[dict] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.category = category or ErrorCategory.UNKNOWN
        self.field_errors = field_errors

    def __str__(self) -> str:
        return f"HubSpotError({self.status_code}): {self.message}"


class RateLimitError(HubSpotError):
    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message, status_code=429, category=ErrorCategory.RATE_LIMIT)
        self.retry_after = retry_after


class ScopeError(HubSpotError):
    def __init__(self, message: str, required_scopes: list[str] | None = None):
        super().__init__(message, status_code=403, category=ErrorCategory.SCOPE)
        self.required_scopes = required_scopes or []
