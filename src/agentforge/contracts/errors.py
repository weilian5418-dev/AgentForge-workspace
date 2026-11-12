"""Stable error codes, HTTP mapping, and response envelope."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator

from agentforge.contracts.base import ContractModel


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    HOST_REJECTED = "HOST_REJECTED"
    ORIGIN_REJECTED = "ORIGIN_REJECTED"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    INVALID_STATE = "INVALID_STATE"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    HTTP_ERROR = "HTTP_ERROR"
    # v1.4 agent collaboration
    AGENT_NOT_AUTHENTICATED = "AGENT_NOT_AUTHENTICATED"
    CAPABILITY_UNVERIFIED = "CAPABILITY_UNVERIFIED"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    SESSION_STATE_UNKNOWN = "SESSION_STATE_UNKNOWN"
    STOP_UNCONFIRMED = "STOP_UNCONFIRMED"
    APPROVAL_MAPPING_UNSUPPORTED = "APPROVAL_MAPPING_UNSUPPORTED"
    APPROVAL_NOT_CONFIRMED = "APPROVAL_NOT_CONFIRMED"
    CANDIDATE_STALE = "CANDIDATE_STALE"
    STAGE_GATE_NOT_MET = "STAGE_GATE_NOT_MET"
    ASSIGNMENT_STALE = "ASSIGNMENT_STALE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    MODEL_AUTHORIZATION_DENIED = "MODEL_AUTHORIZATION_DENIED"
    CONTEXT_MANIFEST_MISMATCH = "CONTEXT_MANIFEST_MISMATCH"
    MODEL_CALL_UNKNOWN = "MODEL_CALL_UNKNOWN"
    # D11 driver / recovery
    DRIVER_BUSY = "DRIVER_BUSY"
    DRIVER_FENCE_LOST = "DRIVER_FENCE_LOST"
    STALE_BINDING = "STALE_BINDING"
    WAIT_SCOPE_MISMATCH = "WAIT_SCOPE_MISMATCH"
    CHECKPOINT_INCOMPATIBLE = "CHECKPOINT_INCOMPATIBLE"
    NATIVE_STATE_UNKNOWN = "NATIVE_STATE_UNKNOWN"
    WORKSPACE_MISMATCH = "WORKSPACE_MISMATCH"
    PENDING_SIDE_EFFECTS = "PENDING_SIDE_EFFECTS"
    PENDING_STOP_VERIFICATION = "PENDING_STOP_VERIFICATION"
    RECOVERY_EVIDENCE_STALE = "RECOVERY_EVIDENCE_STALE"
    RESUME_UNSUPPORTED = "RESUME_UNSUPPORTED"
    HANDOFF_CONFIRMATION_REQUIRED = "HANDOFF_CONFIRMATION_REQUIRED"


ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 422,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.HOST_REJECTED: 403,
    ErrorCode.ORIGIN_REJECTED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.VERSION_CONFLICT: 409,
    ErrorCode.INVALID_STATE: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.BUDGET_EXCEEDED: 409,
    ErrorCode.CAPABILITY_UNAVAILABLE: 503,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.HTTP_ERROR: 400,
    ErrorCode.AGENT_NOT_AUTHENTICATED: 409,
    ErrorCode.CAPABILITY_UNVERIFIED: 409,
    ErrorCode.CAPABILITY_MISMATCH: 409,
    ErrorCode.SESSION_STATE_UNKNOWN: 409,
    ErrorCode.STOP_UNCONFIRMED: 409,
    ErrorCode.APPROVAL_MAPPING_UNSUPPORTED: 422,
    ErrorCode.APPROVAL_NOT_CONFIRMED: 409,
    ErrorCode.CANDIDATE_STALE: 409,
    ErrorCode.STAGE_GATE_NOT_MET: 409,
    ErrorCode.ASSIGNMENT_STALE: 409,
    ErrorCode.BUDGET_EXHAUSTED: 409,
    ErrorCode.PROVIDER_AUTH_FAILED: 502,
    ErrorCode.PROVIDER_UNAVAILABLE: 503,
    ErrorCode.MODEL_AUTHORIZATION_DENIED: 403,
    ErrorCode.CONTEXT_MANIFEST_MISMATCH: 409,
    ErrorCode.MODEL_CALL_UNKNOWN: 409,
    ErrorCode.DRIVER_BUSY: 409,
    ErrorCode.DRIVER_FENCE_LOST: 409,
    ErrorCode.STALE_BINDING: 409,
    ErrorCode.WAIT_SCOPE_MISMATCH: 409,
    ErrorCode.CHECKPOINT_INCOMPATIBLE: 409,
    ErrorCode.NATIVE_STATE_UNKNOWN: 409,
    ErrorCode.WORKSPACE_MISMATCH: 409,
    ErrorCode.PENDING_SIDE_EFFECTS: 409,
    ErrorCode.PENDING_STOP_VERIFICATION: 409,
    ErrorCode.RECOVERY_EVIDENCE_STALE: 409,
    ErrorCode.RESUME_UNSUPPORTED: 422,
    ErrorCode.HANDOFF_CONFIRMATION_REQUIRED: 409,
}

RETRYABLE_CODES = frozenset(
    {
        ErrorCode.CAPABILITY_UNAVAILABLE,
        ErrorCode.INTERNAL_ERROR,
        ErrorCode.PROVIDER_UNAVAILABLE,
    }
)

_SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "token",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "credential",
    "database_url",
    "connection_string",
)


def http_status_for(code: ErrorCode | str) -> int:
    try:
        return ERROR_HTTP_STATUS[ErrorCode(code)]
    except ValueError:
        return 400


def default_retryable(code: ErrorCode | str) -> bool:
    try:
        return ErrorCode(code) in RETRYABLE_CODES
    except ValueError:
        return False


def sanitize_error_details(details: dict[str, Any] | None) -> dict[str, str]:
    """Drop secrets and oversized values from error details."""
    if not details:
        return {}
    cleaned: dict[str, str] = {}
    for key, value in details.items():
        lowered = key.lower()
        if any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS):
            continue
        text = value if isinstance(value, str) else str(value)
        if len(text) > 512:
            text = text[:509] + "..."
        if any(
            marker in text.lower()
            for marker in ("bearer ", "postgresql+", "password=", "api_token")
        ):
            continue
        cleaned[key] = text
    return cleaned


class ErrorDetail(ContractModel):
    code: ErrorCode | str
    message: str = Field(min_length=1, max_length=1024)
    retryable: bool = False
    details: dict[str, str] = Field(default_factory=dict)
    request_id: str = Field(min_length=1, max_length=128)

    @field_validator("details")
    @classmethod
    def scrub_details(cls, value: dict[str, str]) -> dict[str, str]:
        return sanitize_error_details(value)


class ErrorResponse(ContractModel):
    error: ErrorDetail


def build_error(
    *,
    code: ErrorCode | str,
    message: str,
    request_id: str,
    retryable: bool | None = None,
    details: dict[str, Any] | None = None,
) -> ErrorResponse:
    return ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            retryable=default_retryable(code) if retryable is None else retryable,
            details=sanitize_error_details(details),
            request_id=request_id,
        )
    )
