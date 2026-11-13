"""D11 recovery contracts: observations, recommendations, decisions, handoff.

Invariants:
- Lease/checkpoint age is not native stop proof.
- Unsupported or login-expired is not ``session absent``.
- Handoff creates a new session/workspace; it is not resume_existing.
- Applying a decision re-checks caseVersion/contextHash/statusVersion.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import (
    AssignmentVersion,
    OpaqueId,
    PlanVersion,
    ResourceRevision,
    Sha256Hex,
    ShortText,
    UtcDateTime,
)

RecoveryCaseStatus = Literal[
    "open",
    "observing",
    "awaiting_decision",
    "applying",
    "applied",
    "blocked",
    "superseded",
    "canceled",
]

RecoverySubjectType = Literal[
    "native_session",
    "workspace",
    "instance_operation",
    "delivery",
    "candidate",
    "authorization",
    "budget",
    "driver_projection",
]

ObservationSupport = Literal[
    "supported",
    "unsupported",
    "partial",
    "unknown",
]

RecoveryAction = Literal[
    "observe_existing",
    "continue_stage_processing",
    "resume_existing",
    "handoff_new_session",
    "await_reconciliation",
    "block_or_rework",
    "wait_authorization",
    "await_stop_verification",
    "rebuild_projection",
]

ResumeOutcomeKind = Literal[
    "resumed_existing",
    "handoff_required",
    "unsupported",
    "unknown",
    "failed",
]

RecoveryBlockerCode = Literal[
    "native_state_unknown",
    "workspace_mismatch",
    "pending_side_effects",
    "pending_stop_verification",
    "recovery_evidence_stale",
    "resume_unsupported",
    "handoff_confirmation_required",
    "authorization_invalid",
    "candidate_hash_mismatch",
    "budget_unknown",
    "context_changed",
    "login_expired",
]


class RecoveryObservation(ContractModel):
    """Append-only observation — never overwrite prior rows."""

    observation_id: OpaqueId
    case_id: OpaqueId
    subject_type: RecoverySubjectType
    subject_ref: ShortText
    observed_at: UtcDateTime
    support: ObservationSupport
    result_code: ShortText
    evidence_hash: Sha256Hex
    evidence_ref: OpaqueId | None = None
    binding_json: dict[str, Any] = Field(default_factory=dict)
    # Bounded summary only — full blobs stay in restricted storage.
    summary: ShortText | None = None


class NativeObservation(ContractModel):
    """Normalized native probe result. Missing safety facts => unknown, not False."""

    schema_version: int = Field(default=1, ge=1, strict=True)
    session_ref: ShortText | None = None
    native_session_id: OpaqueId | None = None
    instance_id: OpaqueId
    attempt_id: OpaqueId | None = None
    generation: int | None = Field(default=None, ge=1, strict=True)
    workspace_id: OpaqueId | None = None
    actual_cwd: ShortText | None = None
    running: bool | None = Field(default=None, strict=True)
    children_running: bool | None = Field(default=None, strict=True)
    writes_quiesced: bool | None = Field(default=None, strict=True)
    observed_at: UtcDateTime
    support: ObservationSupport
    evidence_ref: OpaqueId | None = None

    @model_validator(mode="after")
    def missing_safety_is_unknown(self) -> Self:
        critical = (self.running, self.children_running, self.writes_quiesced)
        if any(v is None for v in critical) and self.support == "supported":
            raise ValueError(
                "NativeObservation with support=supported requires running, "
                "childrenRunning, and writesQuiesced; omit defaults that invent False"
            )
        return self


class WorkspaceObservation(ContractModel):
    workspace_id: OpaqueId
    expected_path: ShortText | None = None
    actual_path: ShortText | None = None
    identity_matched: bool | None = Field(default=None, strict=True)
    git_common_dir: ShortText | None = None
    head_ref: ShortText | None = None
    baseline_ref: ShortText | None = None
    support: ObservationSupport
    evidence_ref: OpaqueId | None = None
    observed_at: UtcDateTime


class OperationObservation(ContractModel):
    operation_id: OpaqueId
    kind: ShortText
    ledger_status: ShortText
    native_ref: ShortText | None = None
    possibly_executed: bool = Field(strict=True)
    support: ObservationSupport
    evidence_ref: OpaqueId | None = None
    observed_at: UtcDateTime


class RecoveryRecommendation(ContractModel):
    """Pure policy output — does not mutate Run or call adapters."""

    action: RecoveryAction
    reason_codes: list[RecoveryBlockerCode | ShortText] = Field(default_factory=list, max_length=16)
    required_evidence: list[ShortText] = Field(default_factory=list, max_length=32)
    requires_user_decision: bool = Field(strict=True)
    detail: ShortText | None = None


class RecoveryDecision(ContractModel):
    decision_id: OpaqueId
    case_id: OpaqueId
    case_version: int = Field(ge=1, strict=True)
    decision_key: ShortText
    context_hash: Sha256Hex
    decision: RecoveryAction
    actor: ShortText
    reason: ShortText | None = None
    evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    created_at: UtcDateTime


class RecoveryDecisionRequest(ApiInputModel):
    decision_key: ShortText
    decision: RecoveryAction
    case_version: int = Field(ge=1, strict=True)
    context_hash: Sha256Hex
    expected_status_version: ResourceRevision
    idempotency_key: ShortText
    reason: str | None = Field(default=None, max_length=2048)
    evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def reject_invented_safety(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        forbidden = {
            "stopVerified",
            "stop_verified",
            "nativeStopped",
            "native_stopped",
            "forceResume",
            "force_resume",
            "assumeStopped",
            "assume_stopped",
        }
        for key in data:
            if key in forbidden:
                raise ValueError(
                    f"clients cannot invent safety facts ({key}) on recovery decisions"
                )
        return data


class RecoveryCase(ContractModel):
    case_id: OpaqueId
    run_id: OpaqueId
    request_operation_id: OpaqueId | None = None
    context_hash: Sha256Hex
    case_version: int = Field(ge=1, strict=True)
    status: RecoveryCaseStatus
    proposed_action: RecoveryAction | None = None
    blocker_codes: list[RecoveryBlockerCode | ShortText] = Field(
        default_factory=list, max_length=16
    )
    plan_version: PlanVersion | None = None
    assignment_version: AssignmentVersion | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class RecoveryReport(ContractModel):
    case: RecoveryCase
    observations: list[RecoveryObservation] = Field(default_factory=list, max_length=64)
    recommendation: RecoveryRecommendation | None = None
    applied_decision: RecoveryDecision | None = None


class RecoveryCaseListResponse(ContractModel):
    items: list[RecoveryCase] = Field(default_factory=list, max_length=100)


class ResumeExistingResult(ContractModel):
    kind: Literal["resumed_existing"] = "resumed_existing"
    session_ref: ShortText
    native_session_id: OpaqueId
    evidence_ref: OpaqueId
    attempt_id: OpaqueId | None = None


class HandoffRequiredResult(ContractModel):
    kind: Literal["handoff_required"] = "handoff_required"
    reason_codes: list[RecoveryBlockerCode | ShortText] = Field(default_factory=list, max_length=16)
    detail: ShortText | None = None


class ResumeUnsupportedResult(ContractModel):
    kind: Literal["unsupported"] = "unsupported"
    detail: ShortText | None = None


class ResumeUnknownResult(ContractModel):
    kind: Literal["unknown"] = "unknown"
    detail: ShortText | None = None
    possibly_executed: bool = Field(strict=True)


class ResumeFailedResult(ContractModel):
    kind: Literal["failed"] = "failed"
    error_code: ShortText
    detail: ShortText | None = None


ResumeCoordinatorResult = (
    ResumeExistingResult
    | HandoffRequiredResult
    | ResumeUnsupportedResult
    | ResumeUnknownResult
    | ResumeFailedResult
)


class HandoffManifest(ContractModel):
    """Structured handoff package for a new session — not a restored native session."""

    handoff_id: OpaqueId
    run_id: OpaqueId
    plan_version: PlanVersion
    assignment_version: AssignmentVersion
    stage_id: OpaqueId | None = None
    work_package_id: OpaqueId
    source_attempt_id: OpaqueId
    source_instance_id: OpaqueId
    source_native_session_id: OpaqueId | None = None
    source_workspace_id: OpaqueId | None = None
    stop_evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    completed_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)
    remaining_objective: ShortText | None = None
    input_release_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    input_candidate_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    candidate_hashes: list[Sha256Hex] = Field(default_factory=list, max_length=32)
    pending_review_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    uncertain_side_effect_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    uncertain_budget_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    required_reauth: list[ShortText] = Field(default_factory=list, max_length=16)
    handoff_hash: Sha256Hex
    strategy_version: ShortText = "d11.1"
    evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    created_at: UtcDateTime
