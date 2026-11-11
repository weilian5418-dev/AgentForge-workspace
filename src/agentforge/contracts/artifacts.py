"""Artifact and work-package contracts, independent of HTTP and storage."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import (
    ArtifactKind,
    ArtifactState,
    ArtifactVersion,
    OpaqueId,
    RelativePath,
    Sha256Hex,
    ShortText,
    UtcDateTime,
)


class ArtifactRef(ContractModel):
    artifact_id: OpaqueId
    version: ArtifactVersion
    sha256: Sha256Hex


class Criterion(ContractModel):
    id: OpaqueId
    description: ShortText
    required: bool = Field(strict=True)


class ValidatorSpec(ContractModel):
    id: OpaqueId
    version: ShortText
    criterion_ids: list[OpaqueId] = Field(min_length=1, max_length=64)
    required: bool = Field(strict=True)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("config")
    @classmethod
    def bounded_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 32:
            raise ValueError("Validator config may contain at most 32 keys")
        return value


class ArtifactContract(ContractModel):
    id: OpaqueId
    kind: ArtifactKind
    relative_path: RelativePath
    required: bool = Field(strict=True)
    criteria: list[Criterion] = Field(min_length=1, max_length=64)
    validators: list[ValidatorSpec] = Field(min_length=1, max_length=32)
    independent_review: bool = Field(strict=True)

    @model_validator(mode="after")
    def valid_criteria(self) -> Self:
        ids = {criterion.id for criterion in self.criteria}
        if len(ids) != len(self.criteria):
            raise ValueError("Criterion IDs must be unique")
        for validator in self.validators:
            if not set(validator.criterion_ids) <= ids:
                raise ValueError("Validator references unknown criteria")
        return self


class WorkPackage(ContractModel):
    id: OpaqueId
    plan_version: int = Field(ge=1, strict=True)
    stage_id: OpaqueId
    objective: ShortText
    owner_role_id: OpaqueId
    depends_on: list[OpaqueId] = Field(default_factory=list, max_length=64)
    inputs: list[ArtifactRef] = Field(default_factory=list, max_length=64)
    deliverables: list[ArtifactContract] = Field(default_factory=list, max_length=32)
    read_scopes: list[RelativePath] = Field(default_factory=list, max_length=64)
    write_scopes: list[RelativePath] = Field(default_factory=list, max_length=64)
    tool_policy_id: OpaqueId
    max_repair_rounds: int = Field(default=3, ge=0, le=10, strict=True)

    @model_validator(mode="after")
    def valid_dependencies(self) -> Self:
        if self.id in self.depends_on or len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("Dependencies must be unique and cannot include this work package")
        return self


class ArtifactVersionInfo(ContractModel):
    artifact_id: OpaqueId
    version: ArtifactVersion
    state: ArtifactState
    sha256: Sha256Hex
    relative_path: RelativePath
    created_at: UtcDateTime


class ArtifactListResponse(ContractModel):
    items: list[ArtifactVersionInfo] = Field(default_factory=list, max_length=200)


class ManifestEntry(ContractModel):
    artifact_id: OpaqueId
    version: ArtifactVersion
    sha256: Sha256Hex
    kind: ArtifactKind
    relative_path: RelativePath
    required: bool = Field(strict=True)


class DeliveryManifest(ContractModel):
    manifest_hash: Sha256Hex
    run_id: OpaqueId
    entries: list[ManifestEntry] = Field(min_length=1, max_length=128)
    known_risks: list[ShortText] = Field(default_factory=list, max_length=32)


class AcceptanceRequest(ApiInputModel):
    decision: str = Field(pattern=r"^(accept|reject|partial)$")
    manifest_hash: Sha256Hex
    integration_version: OpaqueId
    accepted_artifact_ids: list[OpaqueId] = Field(default_factory=list, max_length=128)
    risk_acknowledged: bool = Field(strict=True)
    note: str | None = Field(default=None, max_length=2048)


class AcceptanceResponse(ContractModel):
    run_id: OpaqueId
    decision: str
    manifest_hash: Sha256Hex
    integration_version: OpaqueId
    fully_verified: bool = Field(strict=True)
