"""Integration selection / version / check / final-review contracts (D12)."""

from __future__ import annotations

from typing import Any, Literal

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
from agentforge.contracts.snapshots import TechnicalStatus

CheckOutcome = Literal[
    "passed",
    "failed",
    "blocked",
    "not_run",
    "unknown",
    "not_applicable",
    "skipped",
    "timeout",
]
ReviewVerdict = Literal["approve", "reject", "needs_rework", "abstain"]
BuildStatus = Literal[
    "accepted",
    "running",
    "succeeded",
    "conflicted",
    "failed",
    "unknown",
    "needs_reconciliation",
]
CycleStatus = Literal[
    "open",
    "checking",
    "awaiting_review",
    "passed",
    "failed",
    "blocked",
    "superseded",
]
EvaluationStatus = Literal["passed", "failed", "blocked", "waiting"]


class IntegrationSourceBinding(ContractModel):
    release_id: OpaqueId
    candidate_id: OpaqueId | None = None
    snapshot_id: OpaqueId
    work_package_id: OpaqueId | None = None
    stage_id: OpaqueId | None = None
    content_hash: Sha256Hex
    tree_hash: Sha256Hex | None = None


class IntegrationSelectionRequest(ApiInputModel):
    """Fix released handoff inputs — clients cannot invent arbitrary snapshotIds."""

    handoff_ref: OpaqueId
    handoff_hash: Sha256Hex
    idempotency_key: ShortText
    merge_policy_ref: ShortText | None = None
    note: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="before")
    @classmethod
    def reject_untrusted_inputs(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        forbidden = {
            "passed",
            "technicalPassed",
            "technical_passed",
            "sourceBindings",
            "source_bindings",
            "extraSnapshotIds",
            "extra_snapshot_ids",
            "mutableHead",
            "mutable_head",
            "forceCleanBaseline",
            "force_clean_baseline",
        }
        for key in data:
            if key in forbidden:
                raise ValueError(
                    f"clients cannot declare integration selection authority ({key})"
                )
        return data


class IntegrationSelection(ContractModel):
    selection_id: OpaqueId
    run_id: OpaqueId
    handoff_ref: OpaqueId
    handoff_hash: Sha256Hex
    baseline_ref: OpaqueId | None = None
    baseline_tree_hash: Sha256Hex
    source_bindings: list[IntegrationSourceBinding] = Field(min_length=1, max_length=64)
    merge_policy_version: ShortText
    selection_hash: Sha256Hex
    created_by: ShortText
    created_at: UtcDateTime


class IntegrationBuildRequest(ApiInputModel):
    selection_id: OpaqueId
    selection_hash: Sha256Hex
    idempotency_key: ShortText

    @model_validator(mode="before")
    @classmethod
    def reject_outcome_claims(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        forbidden = {
            "passed",
            "status",
            "treeHash",
            "tree_hash",
            "contentHash",
            "content_hash",
            "resultVersionId",
            "result_version_id",
        }
        for key in data:
            if key in forbidden:
                raise ValueError(f"clients cannot declare build outcomes ({key})")
        return data


class IntegrationBuildView(ContractModel):
    build_id: OpaqueId
    run_id: OpaqueId
    selection_id: OpaqueId
    operation_id: OpaqueId
    status: BuildStatus
    workspace_ref: OpaqueId | None = None
    result_version_id: OpaqueId | None = None
    error_code: ShortText | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class IntegrationVersionView(ContractModel):
    version_id: OpaqueId
    run_id: OpaqueId
    version_number: int = Field(ge=1, strict=True)
    selection_id: OpaqueId
    selection_hash: Sha256Hex
    git_integration_id: OpaqueId | None = None
    tree_manifest_hash: Sha256Hex
    content_hash: Sha256Hex
    baseline_hash: Sha256Hex
    contract_hash: Sha256Hex
    input_versions_hash: Sha256Hex
    build_environment_ref: ShortText
    parent_version_id: OpaqueId | None = None
    technical_status: TechnicalStatus
    created_at: UtcDateTime


class CheckPolicyItem(ContractModel):
    check_id: OpaqueId
    check_version: ShortText
    required: bool = Field(strict=True)
    config_hash: Sha256Hex
    not_applicable_reason: ShortText | None = None


class CheckPolicy(ContractModel):
    policy_ref: ShortText
    policy_hash: Sha256Hex
    required_checks: list[CheckPolicyItem] = Field(default_factory=list, max_length=64)
    required_review_roles: list[ShortText] = Field(default_factory=list, max_length=16)
    environment_constraints: dict[str, Any] = Field(default_factory=dict, max_length=32)


class ValidationCycleRequest(ApiInputModel):
    policy_ref: ShortText
    idempotency_key: ShortText

    @model_validator(mode="before")
    @classmethod
    def reject_client_passed(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        for key in data:
            if str(key).lower() in {"passed", "technicalpassed", "technical_passed", "outcome"}:
                raise ValueError(f"clients cannot declare validation outcomes ({key})")
        return data


class ValidationCycleView(ContractModel):
    cycle_id: OpaqueId
    version_id: OpaqueId
    policy_hash: Sha256Hex
    environment_fingerprint: Sha256Hex
    status: CycleStatus
    required_checks: list[CheckPolicyItem] = Field(default_factory=list, max_length=64)
    required_review_roles: list[ShortText] = Field(default_factory=list, max_length=16)
    created_at: UtcDateTime


class CheckRunView(ContractModel):
    check_run_id: OpaqueId
    cycle_id: OpaqueId
    check_id: OpaqueId
    check_version: ShortText
    config_hash: Sha256Hex
    attempt_no: int = Field(ge=1, strict=True)
    status: CheckOutcome
    operation_ref: OpaqueId | None = None
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None


class CheckReportView(ContractModel):
    report_id: OpaqueId
    check_run_id: OpaqueId
    version_id: OpaqueId
    tree_hash: Sha256Hex
    policy_hash: Sha256Hex
    environment_hash: Sha256Hex
    outcome: CheckOutcome
    structured_result: dict[str, Any] = Field(default_factory=dict, max_length=64)
    evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    report_hash: Sha256Hex
    created_at: UtcDateTime


class FinalReviewRequestView(ContractModel):
    request_id: OpaqueId
    cycle_id: OpaqueId
    version_id: OpaqueId
    tree_hash: Sha256Hex
    selection_hash: Sha256Hex
    contract_hash: Sha256Hex
    policy_hash: Sha256Hex
    reviewer_binding: ShortText
    expires_at: UtcDateTime
    status: ShortText


class FinalReviewResponseRequest(ApiInputModel):
    """Trusted reviewer response — ordinary user bearer cannot use this path."""

    verdict: ReviewVerdict
    evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    note: str | None = Field(default=None, max_length=4096)
    response_key: ShortText

    @model_validator(mode="before")
    @classmethod
    def reject_authority_claims(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        forbidden = {
            "passed",
            "technicalPassed",
            "technical_passed",
            "versionId",
            "version_id",
            "treeHash",
            "tree_hash",
            "actorId",
            "actor_id",
            "forcePass",
            "force_pass",
        }
        for key in data:
            if key in forbidden:
                raise ValueError(
                    f"review responses cannot declare platform authority ({key})"
                )
        return data


class FinalReviewReportView(ContractModel):
    report_id: OpaqueId
    request_id: OpaqueId
    cycle_id: OpaqueId
    version_id: OpaqueId
    tree_hash: Sha256Hex
    verdict: ReviewVerdict
    reviewer_identity_ref: ShortText
    evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    created_at: UtcDateTime


class IntegrationEvaluation(ContractModel):
    cycle_id: OpaqueId
    version_id: OpaqueId
    status: EvaluationStatus
    blocker_codes: list[ShortText] = Field(default_factory=list, max_length=32)
    required_checks_total: int = Field(ge=0, strict=True)
    required_checks_satisfied: int = Field(ge=0, strict=True)
    required_reviews_total: int = Field(ge=0, strict=True)
    required_reviews_satisfied: int = Field(ge=0, strict=True)
    note: ShortText | None = None

    @model_validator(mode="after")
    def empty_required_cannot_pass(self) -> IntegrationEvaluation:
        if self.status == "passed" and self.required_checks_total == 0:
            raise ValueError("empty required checks cannot evaluate to passed")
        return self


class IntegrationListResponse(ContractModel):
    items: list[IntegrationVersionView] = Field(default_factory=list, max_length=100)
