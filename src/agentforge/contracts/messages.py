"""Structured collaboration message contracts (BE-27 / v1.4)."""

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
MessageType = PublicMessageType | PlatformDerivedMessageType

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

    @property
    def type(self) -> PublicMessageType:
        return self.payload.type


class MessageDelivery(ContractModel):
    delivery_id: OpaqueId
    message_id: OpaqueId
    recipient_instance_id: OpaqueId
    attempt_id: OpaqueId
    status: DeliveryStatus
    native_receipt_ref: ShortText | None = None
    received_at: UtcDateTime | None = None
    responded_at: UtcDateTime | None = None
    retry_count: int = Field(default=0, ge=0, strict=True)
    next_attempt_at: UtcDateTime | None = None
    error_code: ShortText | None = None
    delivery_mode: Literal["realtime", "next_safe_boundary"] | None = None


class MessageListResponse(ContractModel):
    items: list[CollaborationMessage] = Field(default_factory=list, max_length=200)


class MessageDeliveryListResponse(ContractModel):
    items: list[MessageDelivery] = Field(default_factory=list, max_length=200)
