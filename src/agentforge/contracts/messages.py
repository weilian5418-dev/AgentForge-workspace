"""Structured collaboration message contracts (BE-27 / v1.4 / D09)."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import (
    AssignmentVersion,
    OpaqueId,
    PlanVersion,
    RelativePath,
    Sha256Hex,
    ShortText,
    UtcDateTime,
)

# Public clients may not invent stage.release — that type is platform-derived only.
PublicMessageType = Literal[
    "task.assignment",
    "clarification.request",
    "contract.change_request",
    "stage.submission",
    "review.feedback",
    "task.rework",
]
PlatformDerivedMessageType = Literal["stage.release"]
# Control / fact types are routed by ProtocolRouter before model text delivery.
ControlProtocolType = Literal[
    "control.stop_request",
    "control.stop_response",
    "control.shutdown_approved",
    "control.permission_response",
    "control.contract_change_response",
]
FactEventType = Literal[
    "fact.idle",
    "fact.turn_finished",
    "fact.submission_received",
    "fact.native_execution",
    "fact.session_terminated",
]
MessageType = PublicMessageType | PlatformDerivedMessageType
ProtocolMessageType = ControlProtocolType | FactEventType

# Transmission ≠ reply. awaiting_reply must not erase received.
TransmissionStatus = Literal[
    "pending",
    "claimed",
    "sent",
    "received",
    "unknown",
    "failed",
    "expired",
]
ReplyStatus = Literal[
    "not_required",
    "awaiting_reply",
    "responded",
    "timed_out",
]

# Legacy flat delivery status — readable during migration.
DeliveryStatus = Literal[
    "pending",
    "dispatching",
    "sent",
    "received",
    "awaiting_reply",
    "responded",
    "failed",
    "expired",
    "unknown",
]

ProtocolRequestStatus = Literal[
    "pending",
    "matched",
    "timed_out",
    "conflict",
    "cancelled",
    "failed",
]

DeliveryMode = Literal[
    "realtime",
    "next_safe_boundary",
    "independent_task",
    "manual_assisted",
]


class PublicMessagePayloadModel(ApiInputModel):
    """Public nested payloads: camelCase aliases only; discriminator required (no default)."""


class TaskAssignmentPayload(PublicMessagePayloadModel):
    type: Literal["task.assignment"]
    work_package_id: OpaqueId
    objective: ShortText
    stage_id: OpaqueId | None = None


class ClarificationRequestPayload(PublicMessagePayloadModel):
    type: Literal["clarification.request"]
    question: ShortText
    context_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)


class ContractChangeRequestPayload(PublicMessagePayloadModel):
    type: Literal["contract.change_request"]
    summary: ShortText
    proposed_contract_hash: Sha256Hex | None = None


class StageSubmissionPayload(PublicMessagePayloadModel):
    type: Literal["stage.submission"]
    stage_id: OpaqueId
    candidate_ref: OpaqueId
    attempt_refs: list[OpaqueId] = Field(min_length=1, max_length=32)


class ReviewFeedbackPayload(PublicMessagePayloadModel):
    type: Literal["review.feedback"]
    stage_id: OpaqueId
    candidate_id: OpaqueId
    verdict: Literal["approve", "request_changes", "needs_input"]
    summary: ShortText


class TaskReworkPayload(PublicMessagePayloadModel):
    type: Literal["task.rework"]
    stage_id: OpaqueId
    target_role_id: OpaqueId
    reason: ShortText
    paths: list[RelativePath] = Field(default_factory=list, max_length=64)


class StageReleasePayload(PublicMessagePayloadModel):
    """Platform-derived only — not accepted on MessageCreateRequest."""

    type: Literal["stage.release"]
    stage_id: OpaqueId
    gate_decision_id: OpaqueId
    candidate_id: OpaqueId
    manifest_hash: Sha256Hex


PublicMessagePayload = Annotated[
    TaskAssignmentPayload
    | ClarificationRequestPayload
    | ContractChangeRequestPayload
    | StageSubmissionPayload
    | ReviewFeedbackPayload
    | TaskReworkPayload,
    Field(discriminator="type"),
]

MessagePayload = Annotated[
    TaskAssignmentPayload
    | ClarificationRequestPayload
    | ContractChangeRequestPayload
    | StageSubmissionPayload
    | ReviewFeedbackPayload
    | TaskReworkPayload
    | StageReleasePayload,
    Field(discriminator="type"),
]


class CollaborationMessage(ContractModel):
    """Immutable message content and version refs — not delivery progress."""

    schema_version: Literal["1"] = "1"
    message_id: OpaqueId
    project_id: OpaqueId
    run_id: OpaqueId
    stage_id: OpaqueId | None = None
    work_package_id: OpaqueId | None = None
    sender_ref: ShortText
    recipient_refs: list[ShortText] = Field(min_length=1, max_length=32)
    payload: MessagePayload
    artifact_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    plan_version: PlanVersion | None = None
    assignment_version: AssignmentVersion | None = None
    reply_required: bool = Field(default=False, strict=True)
    reply_to: OpaqueId | None = None
    request_id: OpaqueId | None = None
    correlation_id: OpaqueId | None = None
    created_at: UtcDateTime
    expires_at: UtcDateTime | None = None
    derived_from_gate_decision_id: OpaqueId | None = None

    @property
    def type(self) -> MessageType:
        return self.payload.type

    @model_validator(mode="after")
    def release_must_bind_gate_decision(self) -> Self:
        if self.payload.type != "stage.release":
            return self
        if self.derived_from_gate_decision_id is None:
            raise ValueError(
                "stage.release messages must set derivedFromGateDecisionId from a "
                "committed GateDecision"
            )
        if self.payload.gate_decision_id != self.derived_from_gate_decision_id:
            raise ValueError("stage.release payload gateDecisionId must match envelope binding")
        return self


class MessageCreateRequest(ApiInputModel):
    """Public message input — excludes platform-derived stage.release."""

    recipient_refs: list[ShortText] = Field(min_length=1, max_length=32)
    payload: PublicMessagePayload
    stage_id: OpaqueId | None = None
    work_package_id: OpaqueId | None = None
    reply_required: bool = Field(default=False, strict=True)
    reply_to: OpaqueId | None = None
    request_id: OpaqueId | None = None

    @property
    def type(self) -> PublicMessageType:
        return self.payload.type


class MessageDelivery(ContractModel):
    """Per-recipient delivery progress — transmission and reply are separate."""

    delivery_id: OpaqueId
    message_id: OpaqueId
    recipient_instance_id: OpaqueId
    attempt_id: OpaqueId
    transmission_status: TransmissionStatus | None = None
    reply_status: ReplyStatus | None = None
    # Legacy flat status — must agree with transmission/reply facets when set.
    status: DeliveryStatus | None = None
    native_receipt_ref: ShortText | None = None
    received_at: UtcDateTime | None = None
    responded_at: UtcDateTime | None = None
    retry_count: int = Field(default=0, ge=0, strict=True)
    next_attempt_at: UtcDateTime | None = None
    error_code: ShortText | None = None
    delivery_mode: DeliveryMode | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_flat_status(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        from agentforge.messaging.delivery_status import split_legacy_delivery_status

        status = data.get("status")
        transmission = data.get("transmissionStatus", data.get("transmission_status"))
        reply = data.get("replyStatus", data.get("reply_status"))
        if status is not None and transmission is None and reply is None:
            facets = split_legacy_delivery_status(status)
            data = {
                **data,
                "transmissionStatus": facets.transmission,
                "replyStatus": facets.reply,
            }
        return data

    @model_validator(mode="after")
    def sync_transmission_and_reply(self) -> Self:
        from agentforge.messaging.delivery_status import (
            combine_delivery_status_facets,
            reply_must_not_downgrade_transmission,
            split_legacy_delivery_status,
        )

        if self.transmission_status is None and self.status is None:
            raise ValueError("transmissionStatus or legacy status is required")
        if self.transmission_status is None and self.status is not None:
            facets = split_legacy_delivery_status(self.status)
            object.__setattr__(self, "transmission_status", facets.transmission)
            object.__setattr__(self, "reply_status", facets.reply)
        assert self.transmission_status is not None
        if self.reply_status is None:
            object.__setattr__(self, "reply_status", "not_required")

        if not reply_must_not_downgrade_transmission(
            self.transmission_status, self.reply_status
        ):
            raise ValueError(
                "reply status awaiting_reply/responded/timed_out requires "
                "transmissionStatus=received"
            )
        if self.status is not None:
            facets = split_legacy_delivery_status(self.status)
            if (
                self.transmission_status != facets.transmission
                or self.reply_status != facets.reply
            ):
                expected = combine_delivery_status_facets(
                    self.transmission_status, self.reply_status
                )
                if self.status != expected:
                    raise ValueError(
                        "legacy delivery status must agree with transmission/reply "
                        f"(got status={self.status}, transmission={self.transmission_status}, "
                        f"reply={self.reply_status})"
                    )
        else:
            object.__setattr__(
                self,
                "status",
                combine_delivery_status_facets(
                    self.transmission_status, self.reply_status
                ),
            )
        return self


class DeliveryAttempt(ContractModel):
    """One native send attempt and its possibly-unknown result."""

    delivery_attempt_id: OpaqueId
    delivery_id: OpaqueId
    operation_id: OpaqueId
    transmission_status: TransmissionStatus
    native_receipt_ref: ShortText | None = None
    started_at: UtcDateTime
    finished_at: UtcDateTime | None = None
    error_code: ShortText | None = None


class ProtocolRequest(ContractModel):
    """Correlated control request — matched before model text is delivered."""

    request_id: OpaqueId
    request_type: ProtocolMessageType
    allowed_response_types: list[ProtocolMessageType] = Field(min_length=1, max_length=16)
    instance_id: OpaqueId
    native_session_id: OpaqueId | None = None
    attempt_id: OpaqueId | None = None
    generation: int | None = Field(default=None, ge=1, strict=True)
    plan_version: PlanVersion | None = None
    assignment_version: AssignmentVersion | None = None
    stage_id: OpaqueId | None = None
    status: ProtocolRequestStatus
    initiated_by: ShortText
    legal_responder_refs: list[ShortText] = Field(min_length=1, max_length=16)
    created_at: UtcDateTime
    deadline_at: UtcDateTime | None = None
    matched_response_ref: OpaqueId | None = None
    conflict_refs: list[OpaqueId] = Field(default_factory=list, max_length=16)


class NativeEventReceipt(ContractModel):
    """Authenticated native event after normalize/dedupe — not a stage decision."""

    receipt_id: OpaqueId
    instance_id: OpaqueId
    native_session_id: OpaqueId | None = None
    source: ShortText
    dedupe_key: ShortText
    cursor: ShortText | None = None
    event_type: ProtocolMessageType | ShortText
    normalized_payload: dict[str, object] = Field(default_factory=dict)
    received_at: UtcDateTime
    authentic: bool = Field(strict=True)


class MessageListResponse(ContractModel):
    items: list[CollaborationMessage] = Field(default_factory=list, max_length=200)


class MessageDeliveryListResponse(ContractModel):
    items: list[MessageDelivery] = Field(default_factory=list, max_length=200)
