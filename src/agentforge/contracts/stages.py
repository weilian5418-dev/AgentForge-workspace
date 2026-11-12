"""Stage gate, review, and integration contracts (v1.4)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import (
    OpaqueId,
    PlanVersion,
    Sha256Hex,
    ShortText,
    UtcDateTime,
)

GatePolicy = Literal["stage_atomic", "dependency_branch"]
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
GateResult = Literal["released", "rework", "blocked"]
ReviewVerdict = Literal["approve", "request_changes", "needs_input"]


class StageGatePolicy(ContractModel):
    policy: GatePolicy = "stage_atomic"
    version: int = Field(ge=1, strict=True)
    required_review_roles: list[OpaqueId] = Field(default_factory=list, max_length=16)
    require_user_decision: bool = Field(default=False, strict=True)
    max_repair_rounds: int = Field(default=3, ge=0, le=10, strict=True)


class Stage(ContractModel):
    """Runtime stage instance (D09+). Plan-time definitions live in PlanStageDefinition."""

    stage_id: OpaqueId
    plan_version: PlanVersion
    dependencies: list[OpaqueId] = Field(default_factory=list, max_length=32)
    required_work_packages: list[OpaqueId] = Field(default_factory=list, max_length=64)
    gate_policy: StageGatePolicy
    status: StageStatus
    repair_round: int = Field(default=0, ge=0, le=10, strict=True)
    current_candidate_id: OpaqueId | None = None


class StageSubmission(ContractModel):
    submission_id: OpaqueId
    stage_id: OpaqueId
    attempt_refs: list[OpaqueId] = Field(min_length=1, max_length=32)
    stop_evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    candidate_id: OpaqueId
    manifest_hash: Sha256Hex
    submitted_at: UtcDateTime


class StageSubmissionRequest(ApiInputModel):
    attempt_refs: list[OpaqueId] = Field(min_length=1, max_length=32)
    candidate_ref: OpaqueId
    note: str | None = Field(default=None, max_length=2048)


class ReviewFinding(ContractModel):
    path: ShortText | None = None
    location: ShortText | None = None
    reason: ShortText
    evidence: ShortText | None = None
    suggestion: ShortText | None = None


class ReviewReport(ContractModel):
    """Structured review opinion. Not a gate decision."""

    id: OpaqueId
    reviewer_instance_id: OpaqueId
    stage_id: OpaqueId
    candidate_id: OpaqueId
    manifest_hash: Sha256Hex
    contract_hash: Sha256Hex
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=128)
    verdict: ReviewVerdict
    created_at: UtcDateTime


class ReviewReportRequest(ApiInputModel):
    candidate_id: OpaqueId
    manifest_hash: Sha256Hex
    contract_hash: Sha256Hex
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=128)
    verdict: ReviewVerdict


class GateDecision(ContractModel):
    """Platform StageController decision; not writable by review agents."""

    decision_id: OpaqueId
    stage_id: OpaqueId
    candidate_id: OpaqueId
    manifest_hash: Sha256Hex
    contract_hash: Sha256Hex
    gate_policy_version: int = Field(ge=1, strict=True)
    validation_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)
    review_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)
    user_decision_ref: OpaqueId | None = None
    result: GateResult
    reason: ShortText
    decided_at: UtcDateTime


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
