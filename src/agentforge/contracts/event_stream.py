"""SSE cursor / control frames / public event projection contracts (D12)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from agentforge.contracts.base import ContractModel
from agentforge.contracts.common import EventSequence, OpaqueId, RunSequence, ShortText, UtcDateTime
from agentforge.contracts.events import EventEnvelope, EventType

ControlReason = Literal[
    "cursor_expired",
    "gap_detected",
    "retention_elapsed",
    "cursor_ahead",
    "cursor_invalid",
    "cursor_conflict",
    "cursor_scope_mismatch",
    "auth_revoked",
    "server_draining",
    "stream_limit_reached",
    "snapshot_required",
    "unknown_event_type",
]

ControlEventName = Literal[
    "resync_required",
    "auth_revoked",
    "server_draining",
    "stream_limit_reached",
    "snapshot_required",
]


class ResolvedCursor(ContractModel):
    """Result of resolving Last-Event-ID / afterSequence — no secrets."""

    run_id: OpaqueId
    after_sequence: RunSequence
    last_event_id: OpaqueId | None = None
    latest_sequence: RunSequence
    replay_floor_sequence: RunSequence
    resync_required: bool = Field(strict=True)
    reason: ControlReason | None = None


class SseControlFrame(ContractModel):
    """Connection control frame — never carries a business event id/sequence."""

    schema_version: Literal["1"] = "1"
    event: ControlEventName
    reason: ControlReason
    snapshot_url: ShortText | None = None
    message: ShortText | None = None
    run_id: OpaqueId | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_business_sequence_fields(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        forbidden = {
            "id",
            "eventId",
            "event_id",
            "sequence",
            "lastSequence",
            "last_sequence",
            "payload",
        }
        for key in data:
            if key in forbidden:
                raise ValueError(
                    f"control frames must not forge business cursor fields ({key})"
                )
        return data


class PublicEventEnvelope(ContractModel):
    """Whitelisted public projection of a committed business event."""

    schema_version: Literal["1"] = "1"
    event_id: OpaqueId
    run_id: OpaqueId
    sequence: EventSequence
    type: EventType
    timestamp: UtcDateTime
    payload: dict[str, Any] = Field(default_factory=dict)


class StateInvalidatedPayload(ContractModel):
    """Safe stand-in when an internal event cannot expose details."""

    original_type: ShortText
    reason: Literal["redacted", "unauthorized_fields", "oversized"] = "redacted"


class EventBatch(ContractModel):
    events: list[PublicEventEnvelope] = Field(default_factory=list, max_length=100)
    next_after_sequence: RunSequence
    resync_required: bool = Field(default=False, strict=True)
    reason: ControlReason | None = None
    encoded_bytes: int = Field(default=0, ge=0, strict=True)


class EventStreamOpenRequest(ContractModel):
    """Documented query/header rules for GET /runs/{id}/events."""

    after_sequence: RunSequence | None = None
    last_event_id: OpaqueId | None = None
    max_batch: int = Field(default=100, ge=1, le=100, strict=True)

    @model_validator(mode="after")
    def require_one_cursor_on_reconnect_semantics(self) -> EventStreamOpenRequest:
        # First connect must use afterSequence from a snapshot; both None → snapshot_required
        # at the service layer. Mixing inconsistent values is a conflict.
        return self


def assert_business_envelope_not_control(envelope: EventEnvelope) -> None:
    """Business EventEnvelope must not be used for pure connection control."""
    if envelope.type == "resync_required":
        raise ValueError(
            "resync_required is a connection control frame; do not allocate Run sequence"
        )
