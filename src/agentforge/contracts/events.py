"""SSE/business event envelopes and typed payloads.

Persistence and SSE transport are BE-02 / BE-15. This module defines wire semantics only.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agentforge.contracts.artifacts import ArtifactRef
from agentforge.contracts.base import ContractModel
from agentforge.contracts.common import (
    AssignmentVersion,
    EventSequence,
    OpaqueId,
    PlanVersion,
    RunStatus,
    Sha256Hex,
    ShortText,
    UtcDateTime,
    WorkPackageStatus,
)
from agentforge.contracts.plans import RoutingDecision
from agentforge.contracts.stages import GateResult
from agentforge.contracts.validations import ValidationReport

EventType = Literal[
    "routing.decided",
    "plan.proposed",
    "run.status_changed",
    "task.started",
    "task.blocked",
    "artifact.candidate_created",
    "validation.completed",
    "artifact.published",
    "approval.requested",
    "usage.updated",
    "run.delivery_ready",
    "resync_required",
    # v1.4 collaboration events
    "agent.discovery_completed",
    "agent.capabilities_validated",
    "assignment.confirmed",
    "instance.started",
    "instance.stop_requested",
    "instance.stopped",
    "instance.needs_reconciliation",
    "message.delivered",
    "message.delivery_unknown",
    "protocol.request_matched",
    "protocol.request_conflict",
    "native_event.ingested",
    "native_approval.requested",
    "native_approval.confirmed",
    "stage.candidate_frozen",
    "stage.rework_requested",
    "stage.released",
    "integration.validated",
    # D11 driver / control / recovery
    "driver.wait_registered",
    "driver.wake_recorded",
    "run.pause_requested",
    "run.paused",
    "run.resume_requested",
    "run.cancel_requested",
    "run.canceled",
    "recovery.started",
    "recovery.observed",
    "recovery.blocked",
    "recovery.applied",
    "run.collaboration_ready",
]


class RoutingDecidedPayload(ContractModel):
    decision: RoutingDecision


class PlanProposedPayload(ContractModel):
    plan_version: PlanVersion
    contract_hash: Sha256Hex
    summary: str = Field(default="", max_length=4096)


class RunStatusChangedPayload(ContractModel):
    status: RunStatus
    status_version: int = Field(ge=1, strict=True)
    reason: ShortText | None = None


class TaskStartedPayload(ContractModel):
    work_package_id: OpaqueId
    attempt_id: OpaqueId
    agent_instance_id: OpaqueId | None = None
    status: WorkPackageStatus = "running"


class TaskBlockedPayload(ContractModel):
    work_package_id: OpaqueId
    attempt_id: OpaqueId | None = None
    agent_instance_id: OpaqueId | None = None
    status: WorkPackageStatus
    reason: ShortText
    capability_hint: ShortText | None = None


class ArtifactCandidatePayload(ContractModel):
    artifact: ArtifactRef
    work_package_id: OpaqueId
    relative_path: ShortText


class ValidationCompletedPayload(ContractModel):
    report: ValidationReport


class ArtifactPublishedPayload(ContractModel):
    artifact: ArtifactRef
    relative_path: ShortText


class ApprovalRequestedPayload(ContractModel):
    approval_id: OpaqueId
    action: ShortText
    action_digest: Sha256Hex
    expires_at: UtcDateTime
    instance_id: OpaqueId | None = None
    stage_id: OpaqueId | None = None


class UsageUpdatedPayload(ContractModel):
    reserved_tokens: int | None = Field(default=None, ge=0, strict=True)
    actual_tokens: int | None = Field(default=None, ge=0, strict=True)
    estimated_cost: float | None = Field(default=None, ge=0, strict=True)
    currency: str | None = Field(default=None, max_length=8)
    observability: Literal["exact", "estimated", "unknown"] = "unknown"


class DeliveryReadyPayload(ContractModel):
    manifest_hash: Sha256Hex
    integration_version: OpaqueId | None = None
    artifact_count: int = Field(ge=1, strict=True)


class ResyncRequiredPayload(ContractModel):
    reason: Literal["cursor_expired", "gap_detected", "retention_elapsed"]
    snapshot_hint: ShortText = "GET /api/v1/runs/{id}"


class AgentDiscoveryCompletedPayload(ContractModel):
    discovery_id: OpaqueId
    product_count: int = Field(ge=0, strict=True)
    installation_count: int = Field(ge=0, strict=True)


class AgentCapabilitiesValidatedPayload(ContractModel):
    profile_id: OpaqueId
    check_id: OpaqueId
    access_level: ShortText


class AssignmentConfirmedPayload(ContractModel):
    plan_version: PlanVersion
    assignment_version: AssignmentVersion
    role_count: int = Field(ge=1, strict=True)


class InstanceLifecyclePayload(ContractModel):
    instance_id: OpaqueId
    role_id: OpaqueId
    agent_profile_id: OpaqueId
    stage_id: OpaqueId | None = None
    session_ref: ShortText | None = None


class MessageDeliveredPayload(ContractModel):
    message_id: OpaqueId
    delivery_id: OpaqueId
    recipient_instance_id: OpaqueId
    status: ShortText
    transmission_status: ShortText | None = None
    reply_status: ShortText | None = None


class ProtocolRequestPayload(ContractModel):
    request_id: OpaqueId
    request_type: ShortText
    instance_id: OpaqueId
    status: ShortText


class NativeEventIngestedPayload(ContractModel):
    receipt_id: OpaqueId
    instance_id: OpaqueId
    dedupe_key: ShortText
    event_type: ShortText
    cursor: ShortText | None = None


class NativeApprovalPayload(ContractModel):
    approval_id: OpaqueId
    instance_id: OpaqueId
    native_request_id: ShortText
    status: ShortText


class StageCandidateFrozenPayload(ContractModel):
    stage_id: OpaqueId
    candidate_id: OpaqueId
    manifest_hash: Sha256Hex


class StageReworkRequestedPayload(ContractModel):
    stage_id: OpaqueId
    candidate_id: OpaqueId
    target_role_id: OpaqueId
    reason: ShortText


class StageReleasedPayload(ContractModel):
    stage_id: OpaqueId
    candidate_id: OpaqueId
    manifest_hash: Sha256Hex
    gate_result: GateResult


class IntegrationValidatedPayload(ContractModel):
    integration_version: OpaqueId
    manifest_hash: Sha256Hex
    passed: bool = Field(strict=True)


class DriverWaitRegisteredPayload(ContractModel):
    wait_id: OpaqueId
    reason: ShortText
    thread_id: OpaqueId | None = None


class DriverWakeRecordedPayload(ContractModel):
    wake_signal_id: OpaqueId
    source_kind: ShortText
    source_ref: ShortText


class RunControlPayload(ContractModel):
    operation_id: OpaqueId
    action: ShortText
    status_version: int = Field(ge=1, strict=True)
    reason: ShortText | None = None


class RecoveryLifecyclePayload(ContractModel):
    case_id: OpaqueId
    status: ShortText
    action: ShortText | None = None
    blocker_codes: list[ShortText] = Field(default_factory=list, max_length=16)


class CollaborationReadyPayload(ContractModel):
    handoff_id: OpaqueId
    handoff_hash: Sha256Hex
    plan_version: PlanVersion
    assignment_version: AssignmentVersion


class EventEnvelope(ContractModel):
    """Business or control event. `eventId` is unique; `sequence` orders within a run."""

    schema_version: Literal["1"] = "1"
    event_id: OpaqueId
    run_id: OpaqueId
    sequence: EventSequence
    type: EventType
    timestamp: UtcDateTime
    payload: dict[str, Any]


class SseCursorSemantics(ContractModel):
    """Documented SSE resume rules (not an on-wire event)."""

    last_event_id_maps_to: Literal["eventId"] = "eventId"
    resume_after_sequence_field: Literal["sequence"] = "sequence"
    snapshot_latest_sequence_meaning: str = (
        "latestSequence on a run snapshot is the last committed runSequence "
        "from the same transaction as the snapshot; clients request events with "
        "sequence greater than that value."
    )
    duplicate_delivery: str = "Clients must dedupe by eventId; duplicates must not rewind state."
    out_of_order: str = "Events with sequence less than the latest applied state are ignored."
    cursor_expired: str = (
        "When history is outside the retention window, servers emit type=resync_required "
        "and clients must reload the snapshot before continuing."
    )


_PAYLOAD_BY_TYPE: dict[str, type[ContractModel]] = {
    "routing.decided": RoutingDecidedPayload,
    "plan.proposed": PlanProposedPayload,
    "run.status_changed": RunStatusChangedPayload,
    "task.started": TaskStartedPayload,
    "task.blocked": TaskBlockedPayload,
    "artifact.candidate_created": ArtifactCandidatePayload,
    "validation.completed": ValidationCompletedPayload,
    "artifact.published": ArtifactPublishedPayload,
    "approval.requested": ApprovalRequestedPayload,
    "usage.updated": UsageUpdatedPayload,
    "run.delivery_ready": DeliveryReadyPayload,
    "resync_required": ResyncRequiredPayload,
    "agent.discovery_completed": AgentDiscoveryCompletedPayload,
    "agent.capabilities_validated": AgentCapabilitiesValidatedPayload,
    "assignment.confirmed": AssignmentConfirmedPayload,
    "instance.started": InstanceLifecyclePayload,
    "instance.stop_requested": InstanceLifecyclePayload,
    "instance.stopped": InstanceLifecyclePayload,
    "instance.needs_reconciliation": InstanceLifecyclePayload,
    "message.delivered": MessageDeliveredPayload,
    "message.delivery_unknown": MessageDeliveredPayload,
    "protocol.request_matched": ProtocolRequestPayload,
    "protocol.request_conflict": ProtocolRequestPayload,
    "native_event.ingested": NativeEventIngestedPayload,
    "native_approval.requested": NativeApprovalPayload,
    "native_approval.confirmed": NativeApprovalPayload,
    "stage.candidate_frozen": StageCandidateFrozenPayload,
    "stage.rework_requested": StageReworkRequestedPayload,
    "stage.released": StageReleasedPayload,
    "integration.validated": IntegrationValidatedPayload,
    "driver.wait_registered": DriverWaitRegisteredPayload,
    "driver.wake_recorded": DriverWakeRecordedPayload,
    "run.pause_requested": RunControlPayload,
    "run.paused": RunControlPayload,
    "run.resume_requested": RunControlPayload,
    "run.cancel_requested": RunControlPayload,
    "run.canceled": RunControlPayload,
    "recovery.started": RecoveryLifecyclePayload,
    "recovery.observed": RecoveryLifecyclePayload,
    "recovery.blocked": RecoveryLifecyclePayload,
    "recovery.applied": RecoveryLifecyclePayload,
    "run.collaboration_ready": CollaborationReadyPayload,
}


def parse_event_payload(event_type: EventType | str, payload: dict[str, Any]) -> ContractModel:
    model = _PAYLOAD_BY_TYPE.get(event_type)
    if model is None:
        raise ValueError(f"Unsupported event type: {event_type}")
    return model.model_validate(payload)


def event_payload_json_schema(event_type: EventType) -> dict[str, Any]:
    return _PAYLOAD_BY_TYPE[event_type].model_json_schema(by_alias=True)
