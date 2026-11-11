"""Validation report contracts."""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from agentforge.contracts.artifacts import ArtifactRef
from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import (
    CheckStatus,
    FailureClass,
    OpaqueId,
    ShortText,
    UtcDateTime,
)


class CriterionResult(ContractModel):
    criterion_id: OpaqueId
    status: CheckStatus
    evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)
    message: ShortText


class ValidationReport(ContractModel):
    id: OpaqueId
    artifact: ArtifactRef
    contract_id: OpaqueId
    contract_version: int = Field(ge=1, strict=True)
    validator_id: OpaqueId
    validator_version: ShortText
    status: CheckStatus
    criterion_results: list[CriterionResult] = Field(min_length=1, max_length=64)
    failure_class: FailureClass | None = None
    environment_ref: OpaqueId
    started_at: UtcDateTime
    finished_at: UtcDateTime
    evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def consistent_report(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finishedAt must not be earlier than startedAt")
        if self.status == "passed":
            # Contract-level consistency only; evidence authenticity is a domain concern.
            if any(item.status != "passed" for item in self.criterion_results):
                raise ValueError("passed reports require all criteria to be passed")
            if not self.evidence_refs and not any(
                item.evidence_refs for item in self.criterion_results
            ):
                raise ValueError("passed reports require evidence references")
        if self.status == "failed" and self.failure_class is None:
            raise ValueError("failed reports require failureClass")
        return self


class ChecksRerunRequest(ApiInputModel):
    artifact_ids: list[OpaqueId] = Field(min_length=1, max_length=32)
    validator_ids: list[OpaqueId] = Field(default_factory=list, max_length=32)
