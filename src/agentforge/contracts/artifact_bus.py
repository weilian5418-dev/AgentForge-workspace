"""D06 artifact bus input/output contracts (publish ≠ stage release)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import OpaqueId, RelativePath, Sha256Hex, ShortText, UtcDateTime
from agentforge.contracts.git_workspaces import GitStopEvidence

CandidateState = Literal[
    "frozen",
    "validating",
    "verified",
    "publish_failed",
    "published",
    "rejected",
    "superseded",
]


class StopEvidenceClaim(GitStopEvidence):
    """The same ledger-backed stop proof is required for D06 and D07."""


class FreezeCandidateRequest(ApiInputModel):
    project_id: OpaqueId
    workspace_id: OpaqueId
    workspace_root: ShortText
    relative_path: RelativePath
    contract_id: OpaqueId
    contract_version: int = Field(ge=1, strict=True)
    environment_hash: Sha256Hex
    stop_evidence: StopEvidenceClaim
    kind: ShortText = "code"


class CandidateView(ContractModel):
    candidate_id: OpaqueId
    project_id: OpaqueId
    workspace_id: OpaqueId
    relative_path: RelativePath
    content_hash: Sha256Hex
    manifest_hash: Sha256Hex
    contract_hash: Sha256Hex
    environment_hash: Sha256Hex
    state: CandidateState
    operation_id: OpaqueId
    generation: int = Field(ge=1, strict=True)
    created_at: UtcDateTime
    note: ShortText = "published≠stage_released; consume requires D10 gate"


class VerifyCandidateRequest(ApiInputModel):
    candidate_id: OpaqueId
    validator_id: OpaqueId
    # Agent self-reported status is ignored; registry runs the validator.


class ValidationReportView(ContractModel):
    report_id: OpaqueId
    candidate_id: OpaqueId
    validator_id: OpaqueId
    validator_version: ShortText
    status: Literal["passed", "failed", "not_run"]
    content_hash: Sha256Hex
    contract_hash: Sha256Hex
    environment_hash: Sha256Hex
    evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    message: ShortText
    created_at: UtcDateTime


class PublishRequest(ApiInputModel):
    candidate_id: OpaqueId
    idempotency_key: OpaqueId
    artifact_id: OpaqueId | None = None


class PublicationView(ContractModel):
    publication_id: OpaqueId
    candidate_id: OpaqueId
    artifact_id: OpaqueId
    version: int = Field(ge=1, strict=True)
    content_hash: Sha256Hex
    manifest_hash: Sha256Hex
    state: Literal["published"]
    stage_released: bool = Field(
        default=False,
        strict=True,
        description="Always false until D10; published does not imply stage release.",
    )
    created_at: UtcDateTime


class ConsumeCheckResponse(ContractModel):
    artifact_id: OpaqueId
    version: int = Field(ge=1, strict=True)
    allowed: bool = Field(strict=True)
    reason: ShortText


class GcRequest(ApiInputModel):
    grace_seconds: int = Field(default=3600, ge=0, le=86400 * 30, strict=True)
    dry_run: bool = Field(default=True, strict=True)


class GcResult(ContractModel):
    marked: list[Sha256Hex] = Field(default_factory=list, max_length=1000)
    deleted: list[Sha256Hex] = Field(default_factory=list, max_length=1000)
    skipped_protected: list[Sha256Hex] = Field(default_factory=list, max_length=1000)
    dry_run: bool = Field(strict=True)
