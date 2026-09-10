"""Standardized errors and error codes for Conversation Intelligence operations (B-09)."""

from enum import StrEnum
from typing import Any


class IntelligenceErrorCode(StrEnum):
    """Machine-readable error codes for conversation intelligence operations."""

    TIMEOUT = "intelligence_timeout"
    PROVIDER_UNAVAILABLE = "intelligence_provider_unavailable"
    INVALID_OUTPUT = "intelligence_invalid_output"
    INSUFFICIENT_CONTEXT = "intelligence_insufficient_context"
    STALE_SOURCE = "intelligence_stale_source"
    INTERNAL_ERROR = "intelligence_internal_error"


class IntelligenceError(Exception):
    """Safe domain exception for conversation intelligence operations.

    Ensures no raw prompt or raw message content leaks in error strings,
    and provides machine-readable error codes for API status mapping.
    """

    def __init__(
        self,
        code: IntelligenceErrorCode | str,
        message: str,
        operation: str = "intelligence_operation",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if isinstance(code, str):
            try:
                self.code = IntelligenceErrorCode(code)
            except ValueError:
                self.code = IntelligenceErrorCode.INTERNAL_ERROR
        else:
            self.code = code
        self.message = message
        self.operation = operation
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Serialize error for safe client responses without sensitive stack traces."""
        return {
            "error_code": self.code.value,
            "message": self.message,
            "operation": self.operation,
        }

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.operation}: {self.message}"
