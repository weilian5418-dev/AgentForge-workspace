"""Immutable delivery manifest and user acceptance contracts (D12)."""

from __future__ import annotations

from typing import Literal

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
from agentforge.contracts.snapshots import AcceptanceStatus, TechnicalStatus

AcceptanceDecisionKind = Literal["accept", "reject", "partial_accept"]
MemberKind = Literal["code_tree", "markdown", "pptx", "other_artifact", "diagnostic"]
RiskKind = Literal[
    "known_failure",
    "unsupported",
    "not_run",
    "unknown_cost",
    "manual_step",
    "other",
]


class ManifestMember(ContractModel):
    member_id: OpaqueId
    kind: MemberKind
    required: bool = Field(strict=True)
    safe_relative_path: ShortText
    object_hash: Sha256Hex
    source_candidate_ref: OpaqueId | None = None
    source_release_ref: OpaqueId | None = None
    depends_on_member_ids: list[OpaqueId] = Field(default_factory=list, max_length=32)
    applicable_check_ids: list[OpaqueId] = Field(default_factory=list, max_length=32)
    applicable_review_request_ids: list[OpaqueId] = Field(default_factory=list, max_length=16)
    member_technical_status: TechnicalStatus


class ManifestCheckEntry(ContractModel):
    check_id: OpaqueId
    check_version: ShortText
    config_hash: Sha256Hex
    check_run_id: OpaqueId | None = None
    report_id: OpaqueId | None = None
    report_hash: Sha256Hex | None = None
    outcome: Literal[
        "passed",
        "failed",
        "blocked",
        "not_run",
        "unknown",
        "not_applicable",
        "skipped",
        "timeout",
    ]
    environment_hash: Sha256Hex | None = None
    input_fingerprint: Sha256Hex | None = None


class ManifestReviewEntry(ContractModel):
    request_id: OpaqueId
    report_id: OpaqueId | None = None
    reviewer_identity_ref: ShortText | None = None
    verdict: Literal["approve", "reject", "needs_rework", "abstain", "pending"]
    binding_fingerprint: Sha256Hex | None = None


class ManifestRisk(ContractModel):
    kind: RiskKind
    detail: ShortText
    member_ids: list[OpaqueId] = Field(default_factory=list, max_length=32)


class DeliveryManifestPayload(ContractModel):
    """Immutable hashed subject — manifestHash itself is not part of the hash input."""

    schema_version: Literal["1"] = "1"
    run_id: OpaqueId
    integration_version: OpaqueId
    validation_cycle_id: OpaqueId
    manifest_revision: int = Field(ge=1, strict=True)
    plan_version: PlanVersion
    assignment_version: AssignmentVersion
    gate_policy_version: int = Field(ge=1, strict=True)
    selection_hash: Sha256Hex
    baseline_hash: Sha256Hex
    tree_manifest_hash: Sha256Hex
    contract_hash: Sha256Hex
    integration_policy_hash: Sha256Hex
    environment_fingerprint: Sha256Hex
    members: list[ManifestMember] = Field(default_factory=list, max_length=128)
    checks: list[ManifestCheckEntry] = Field(default_factory=list, max_length=64)
    reviews: list[ManifestReviewEntry] = Field(default_factory=list, max_length=32)
    known_risks: list[ManifestRisk] = Field(default_factory=list, max_length=32)
    technical_status: TechnicalStatus

    @model_validator(mode="after")
    def empty_blocked_cannot_look_ready(self) -> DeliveryManifestPayload:
        if self.technical_status == "passed" and not self.members:
            raise ValueError("passed manifest requires at least one member")
        if self.technical_status == "passed" and not self.checks:
            raise ValueError("passed software delivery requires recorded checks")
        return self


class DeliveryManifestView(ContractModel):
    manifest_id: OpaqueId
    run_id: OpaqueId
    version_id: OpaqueId
    cycle_id: OpaqueId
    revision: int = Field(ge=1, strict=True)
    manifest_hash: Sha256Hex
    payload: DeliveryManifestPayload
    technical_status: TechnicalStatus
    created_at: UtcDateTime
    etag: ShortText


class DeliveryPointerView(ContractModel):
    run_id: OpaqueId
    current_version_id: OpaqueId | None = None
    current_manifest_id: OpaqueId | None = None
    manifest_hash: Sha256Hex | None = None
    delivery_revision: int = Field(ge=1, strict=True)
    technical_status: TechnicalStatus
    acceptance_status: AcceptanceStatus
    invalidated_reason: ShortText | None = None
    etag: ShortText


class AcceptanceDecisionRequest(ApiInputModel):
    """User acceptance of a fixed manifest — never declares technicalPassed."""

    integration_version: OpaqueId
    manifest_hash: Sha256Hex
    manifest_revision: int = Field(ge=1, strict=True)
    expected_status_version: ResourceRevision
    decision: AcceptanceDecisionKind
    accepted_member_ids: list[OpaqueId] = Field(default_factory=list, max_length=128)
    rejected_member_ids: list[OpaqueId] = Field(default_factory=list, max_length=128)
    note: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="before")
    @classmethod
    def reject_forged_authority(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        forbidden = {
            "actorId",
            "actor_id",
            "technicalPassed",
            "technical_passed",
            "passed",
            "checkReports",
            "check_reports",
            "allowedCandidateIds",
            "allowed_candidate_ids",
            "forceComplete",
            "force_complete",
            "push",
            "deploy",
        }
        for key in data:
            if key in forbidden:
                raise ValueError(f"acceptance request cannot declare platform authority ({key})")
        return data

    @model_validator(mode="after")
    def validate_member_sets(self) -> AcceptanceDecisionRequest:
        accepted = set(self.accepted_member_ids)
        rejected = set(self.rejected_member_ids)
        if accepted & rejected:
            raise ValueError("acceptedMemberIds and rejectedMemberIds must not overlap")
        if self.decision == "partial_accept":
            if not self.accepted_member_ids:
                raise ValueError("partial_accept requires a non-empty acceptedMemberIds")
            if accepted == rejected:
                raise ValueError("partial_accept must be a true subset decision")
        if self.decision == "accept" and self.rejected_member_ids:
            raise ValueError("full accept cannot include rejectedMemberIds")
        if self.decision == "reject" and self.accepted_member_ids:
            raise ValueError("reject cannot include acceptedMemberIds")
        return self


class AcceptanceResult(ContractModel):
    decision_id: OpaqueId
    run_id: OpaqueId
    decision: AcceptanceDecisionKind
    integration_version: OpaqueId
    manifest_hash: Sha256Hex
    delivery_revision: int = Field(ge=1, strict=True)
    acceptance_status: AcceptanceStatus
    technical_status: TechnicalStatus
    is_current: bool = Field(strict=True)
    run_status: ShortText | None = None
    accepted_member_ids: list[OpaqueId] = Field(default_factory=list, max_length=128)
    rejected_member_ids: list[OpaqueId] = Field(default_factory=list, max_length=128)
    undecided_member_ids: list[OpaqueId] = Field(default_factory=list, max_length=128)
    created_at: UtcDateTime


class AcceptanceDecisionListResponse(ContractModel):
    items: list[AcceptanceResult] = Field(default_factory=list, max_length=100)
