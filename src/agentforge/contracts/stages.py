"""Stage gate, review, candidate set, and release contracts (v1.4 / D10)."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import (
    AssignmentVersion,
    OpaqueId,
    PlanVersion,
    Sha256Hex,
    ShortText,
    UtcDateTime,
)

GatePolicy = Literal["stage_atomic", "dependency_branch"]

# Lifecycle path — waiting reasons live outside status (do not overload blocked).
StageStatus = Literal[
    "pending",
    "ready",
    "executing",
    "submission_requested",
    "stopping",
    "frozen",
    "reviewing",
    "released",
    "rework",
    "blocked",
]

StageWaitingReason = Literal[
    "awaiting_stop_verification",
    "awaiting_validation",
    "awaiting_review",
    "awaiting_user_decision",
    "awaiting_permission",
    "budget_exhausted",
    "none",
]

# Committed GateDecision results (immutable record).
GateResult = Literal["released", "rework", "blocked"]

# Pure GateEvaluator outcome — waiting ≠ release; not interchangeable.
GateEvaluationOutcome = Literal["waiting", "rework", "blocked", "release"]

ReviewVerdict = Literal["approve", "request_changes", "needs_input"]
ReviewCycleStatus = Literal[
    "open",
    "awaiting_validation",
    "awaiting_review",
    "awaiting_user_decision",
    "decided",
    "superseded",
    "cancelled",
]
ReworkOrderStatus = Literal["open", "accepted", "in_progress", "completed", "cancelled", "blocked"]
ReleaseScopeKind = Literal["stage_atomic", "dependency_branch"]


class StageGatePolicy(ContractModel):
    policy: GatePolicy = "stage_atomic"
    version: int = Field(default=1, ge=1, strict=True)
    required_review_roles: list[OpaqueId] = Field(default_factory=list, max_length=16)
    require_user_decision: bool = Field(default=False, strict=True)
    max_repair_rounds: int = Field(default=3, ge=0, le=10, strict=True)
    conflict_resolution: Literal["escalate_user", "unanimous", "majority"] = "escalate_user"


class WorkPackageCandidateRef(ContractModel):
    """One work package → candidate → artifact version binding inside a CandidateSet."""

    work_package_id: OpaqueId
    candidate_id: OpaqueId
    artifact_version: int = Field(ge=1, strict=True)
    content_hash: Sha256Hex | None = None
    required: bool = Field(default=True, strict=True)


class CandidateSet(ContractModel):
    """Fixed candidate collection for one review cycle — not a single current_candidate_id."""

    candidate_set_id: OpaqueId
    stage_id: OpaqueId
    submission_id: OpaqueId | None = None
    members: list[WorkPackageCandidateRef] = Field(min_length=1, max_length=64)
    required_work_packages_complete: bool = Field(strict=True)
    upstream_input_versions: list[OpaqueId] = Field(default_factory=list, max_length=64)
    baseline_or_integration_ref: OpaqueId | None = None
    stop_evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    contract_hash: Sha256Hex
    environment_fingerprint: Sha256Hex
    manifest_hash: Sha256Hex
    created_at: UtcDateTime

    @model_validator(mode="after")
    def required_members_when_complete(self) -> Self:
        required = [m for m in self.members if m.required]
        if self.required_work_packages_complete and not required:
            raise ValueError("complete candidate set must include required members")
        return self


class ReviewCycle(ContractModel):
    """One audit cycle bound to a fixed candidate set fingerprint."""

    review_cycle_id: OpaqueId
    stage_id: OpaqueId
    submission_id: OpaqueId
    candidate_set_id: OpaqueId
    candidate_set_hash: Sha256Hex
    contract_hash: Sha256Hex
    gate_policy_version: int = Field(ge=1, strict=True)
    environment_fingerprint: Sha256Hex
    status: ReviewCycleStatus
    repair_round: int = Field(default=0, ge=0, le=10, strict=True)
    opened_at: UtcDateTime
    closed_at: UtcDateTime | None = None


class ValidationRequirement(ContractModel):
    """Mandatory check definition from the approved plan — callers cannot swap for weak checks."""

    requirement_id: OpaqueId
    criterion_id: OpaqueId
    validator_id: OpaqueId
    validator_version: ShortText
    work_package_ids: list[OpaqueId] = Field(default_factory=list, max_length=64)
    environment_ref: OpaqueId | None = None
    required: bool = Field(default=True, strict=True)
    parameters: dict[str, object] = Field(default_factory=dict)


class ReviewRequest(ContractModel):
    """Audit request — reports must bind to this request and cycle, not only candidateId."""

    review_request_id: OpaqueId
    review_cycle_id: OpaqueId
    stage_id: OpaqueId
    candidate_set_id: OpaqueId
    review_role_id: OpaqueId
    legal_responder_refs: list[ShortText] = Field(min_length=1, max_length=16)
    scope: ShortText
    status: Literal["pending", "answered", "timed_out", "cancelled"] = "pending"
    deadline_at: UtcDateTime | None = None
    created_at: UtcDateTime


class StageUserDecision(ContractModel):
    """User stage decision — distinct from D08 plan confirmation."""

    decision_id: OpaqueId
    request_id: OpaqueId
    stage_id: OpaqueId
    review_cycle_id: OpaqueId
    candidate_set_hash: Sha256Hex
    contract_hash: Sha256Hex
    gate_policy_version: int = Field(ge=1, strict=True)
    decision: Literal["approve", "reject", "needs_revision"]
    note: ShortText | None = None
    decided_by: ShortText
    decided_at: UtcDateTime


class StageUserDecisionRequest(ApiInputModel):
    request_id: OpaqueId
    review_cycle_id: OpaqueId
    candidate_set_hash: Sha256Hex
    contract_hash: Sha256Hex
    gate_policy_version: int = Field(ge=1, strict=True)
    decision: Literal["approve", "reject", "needs_revision"]
    note: str | None = Field(default=None, max_length=2048)


class Stage(ContractModel):
    """Runtime stage instance. Plan-time definitions live in PlanStageDefinition."""

    stage_id: OpaqueId
    run_id: OpaqueId | None = None
    plan_version: PlanVersion
    assignment_version: AssignmentVersion | None = None
    gate_policy_version: int | None = Field(default=None, ge=1, strict=True)
    dependencies: list[OpaqueId] = Field(default_factory=list, max_length=32)
    required_work_packages: list[OpaqueId] = Field(default_factory=list, max_length=64)
    gate_policy: StageGatePolicy
    status: StageStatus
    waiting_reason: StageWaitingReason = "none"
    concurrency_version: int = Field(default=1, ge=1, strict=True)
    repair_round: int = Field(default=0, ge=0, le=10, strict=True)
    current_review_cycle_id: OpaqueId | None = None
    current_candidate_set_id: OpaqueId | None = None
    # Legacy single-candidate field — prefer CandidateSet; kept for migration readers.
    current_candidate_id: OpaqueId | None = None

    @model_validator(mode="after")
    def waiting_reason_coherence(self) -> Self:
        if self.waiting_reason != "none" and self.status not in (
            "blocked",
            "stopping",
            "reviewing",
            "frozen",
            "submission_requested",
        ):
            raise ValueError(
                "waitingReason requires a status that can await "
                f"(got status={self.status}, waitingReason={self.waiting_reason})"
            )
        return self


class StageSubmission(ContractModel):
    """Submission intent record — not a trusted freeze/release result."""

    submission_id: OpaqueId
    stage_id: OpaqueId
    submitter_ref: ShortText
    work_package_ids: list[OpaqueId] = Field(min_length=1, max_length=64)
    attempt_refs: list[OpaqueId] = Field(min_length=1, max_length=32)
    output_location_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    # Optional candidate refs for verification only — not client-declared freeze.
    candidate_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    note: ShortText | None = None
    operation_id: OpaqueId | None = None
    plan_version: PlanVersion | None = None
    assignment_version: AssignmentVersion | None = None
    generation: int | None = Field(default=None, ge=1, strict=True)
    submitted_at: UtcDateTime
    # Legacy fields during migration — stop evidence is filled by platform quiescence.
    stop_evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    candidate_id: OpaqueId | None = None
    manifest_hash: Sha256Hex | None = None


class StageSubmissionRequest(ApiInputModel):
    """Client submission intent. Cannot declare stopped/verified/released."""

    work_package_ids: list[OpaqueId] = Field(min_length=1, max_length=64)
    attempt_refs: list[OpaqueId] = Field(min_length=1, max_length=32)
    output_location_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    candidate_ref: OpaqueId | None = None
    note: str | None = Field(default=None, max_length=2048)
    idempotency_key: ShortText | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_trusted_flags(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        forbidden = ("stopped", "verified", "released", "frozen", "passed")
        for key in list(data.keys()):
            normalized = str(key).replace("_", "").lower()
            if normalized in forbidden or normalized.endswith("=true"):
                raise ValueError(
                    f"clients cannot declare trusted stage flags ({key}); submission is intent only"
                )
            # Explicit camelCase / snake flags
            if key in (
                "stopped",
                "verified",
                "released",
                "frozen",
                "passed",
                "stopEvidenceRefs",
                "stop_evidence_refs",
                "manifestHash",
                "manifest_hash",
            ):
                raise ValueError(
                    f"clients cannot set {key} on StageSubmissionRequest; "
                    "platform quiescence and freeze own those fields"
                )
        return data


class ReviewFinding(ContractModel):
    path: ShortText | None = None
    location: ShortText | None = None
    reason: ShortText
    evidence: ShortText | None = None
    suggestion: ShortText | None = None


class ReviewReport(ContractModel):
    """Structured review opinion. Not a gate decision. Must bind request + cycle."""

    id: OpaqueId
    review_request_id: OpaqueId
    review_cycle_id: OpaqueId
    reviewer_instance_id: OpaqueId
    stage_id: OpaqueId
    candidate_set_id: OpaqueId | None = None
    candidate_id: OpaqueId | None = None
    manifest_hash: Sha256Hex
    contract_hash: Sha256Hex
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=128)
    verdict: ReviewVerdict
    created_at: UtcDateTime


class ReviewReportRequest(ApiInputModel):
    review_request_id: OpaqueId
    review_cycle_id: OpaqueId
    candidate_set_id: OpaqueId | None = None
    candidate_id: OpaqueId | None = None
    manifest_hash: Sha256Hex
    contract_hash: Sha256Hex
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=128)
    verdict: ReviewVerdict


class GateEvaluationInput(ContractModel):
    """Fixed facts for pure GateEvaluator — no I/O."""

    stage_id: OpaqueId
    plan_version: PlanVersion
    assignment_version: AssignmentVersion
    gate_policy: StageGatePolicy
    candidate_set: CandidateSet
    contract_hash: Sha256Hex
    validation_report_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)
    validation_passed_required: bool = Field(strict=True)
    validation_has_required_failure: bool = Field(default=False, strict=True)
    review_request_ids: list[OpaqueId] = Field(default_factory=list, max_length=32)
    answered_review_request_ids: list[OpaqueId] = Field(default_factory=list, max_length=32)
    review_verdicts: list[ReviewVerdict] = Field(default_factory=list, max_length=32)
    user_decision: StageUserDecision | None = None
    user_decision_required: bool = Field(default=False, strict=True)
    repair_round: int = Field(default=0, ge=0, le=10, strict=True)
    budget_exhausted: bool = Field(default=False, strict=True)
    stop_verified: bool = Field(default=False, strict=True)
    blocking_facts: list[ShortText] = Field(default_factory=list, max_length=32)


class GateEvaluationResult(ContractModel):
    """Pure evaluator output — waiting/rework/blocked/release are not interchangeable."""

    outcome: GateEvaluationOutcome
    reason: ShortText
    missing_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)
    rework_problem_digest: Sha256Hex | None = None
    allowed_release_work_package_ids: list[OpaqueId] = Field(default_factory=list, max_length=64)


class GateDecision(ContractModel):
    """Immutable StageController decision; not writable by review agents or clients."""

    decision_id: OpaqueId
    stage_id: OpaqueId
    review_cycle_id: OpaqueId | None = None
    candidate_set_id: OpaqueId | None = None
    candidate_id: OpaqueId | None = None
    manifest_hash: Sha256Hex
    contract_hash: Sha256Hex
    gate_policy_version: int = Field(ge=1, strict=True)
    validation_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)
    review_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)
    user_decision_ref: OpaqueId | None = None
    result: GateResult
    allowed_consume_work_package_ids: list[OpaqueId] = Field(default_factory=list, max_length=64)
    reason: ShortText
    decided_at: UtcDateTime


class ReworkOrder(ContractModel):
    """Targeted rework ticket — one valid order per problem set in a cycle."""

    rework_order_id: OpaqueId
    stage_id: OpaqueId
    source_review_cycle_id: OpaqueId
    source_candidate_set_id: OpaqueId
    target_work_package_id: OpaqueId
    target_role_id: OpaqueId
    problem_digest: Sha256Hex
    problems: list[ReviewFinding] = Field(min_length=1, max_length=128)
    allowed_modify_scopes: list[ShortText] = Field(default_factory=list, max_length=64)
    checks_to_rerun: list[OpaqueId] = Field(default_factory=list, max_length=64)
    remaining_rounds: int = Field(ge=0, le=10, strict=True)
    involves_plan_or_interface_change: bool = Field(default=False, strict=True)
    status: ReworkOrderStatus
    created_at: UtcDateTime


class ReleaseRecord(ContractModel):
    """Concrete release scope — branch release must not mark whole stage released."""

    release_id: OpaqueId
    stage_id: OpaqueId
    review_cycle_id: OpaqueId
    gate_decision_id: OpaqueId
    scope_kind: ReleaseScopeKind
    work_package_ids: list[OpaqueId] = Field(min_length=1, max_length=64)
    candidate_versions: list[WorkPackageCandidateRef] = Field(min_length=1, max_length=64)
    dependency_satisfied_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)
    shared_interface_still_blocked: bool = Field(default=False, strict=True)
    created_at: UtcDateTime


class IntegrationVersion(ContractModel):
    id: OpaqueId
    input_candidate_ids: list[OpaqueId] = Field(min_length=1, max_length=64)
    commit_or_tree: ShortText | None = None
    content_hash: Sha256Hex
    environment_ref: OpaqueId
    validation_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)
    review_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)


class StageListResponse(ContractModel):
    items: list[Stage] = Field(default_factory=list, max_length=64)


class GateDecisionListResponse(ContractModel):
    items: list[GateDecision] = Field(default_factory=list, max_length=64)


class ReviewCycleListResponse(ContractModel):
    items: list[ReviewCycle] = Field(default_factory=list, max_length=64)


class ReworkOrderListResponse(ContractModel):
    items: list[ReworkOrder] = Field(default_factory=list, max_length=64)
