"""YAML template import/export contracts."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import (
    ExecutionProfile,
    FailurePolicy,
    OpaqueId,
    RelativePath,
    RunMode,
    ShortText,
)
from agentforge.contracts.stages import GatePolicy


class TemplateParameterSpec(ContractModel):
    type: Literal["string", "integer", "boolean"]
    required: bool = Field(strict=True)
    description: str | None = Field(default=None, max_length=512)


class TemplateRoleSpec(ContractModel):
    id: OpaqueId
    responsibilities: ShortText | None = None
    required_capabilities: list[ShortText] = Field(min_length=1, max_length=32)
    read_scopes: list[RelativePath] = Field(default_factory=list, max_length=32)
    write_scopes: list[RelativePath] = Field(default_factory=list, max_length=32)
    independent_review: bool = Field(default=False, strict=True)


class TemplateStageSpec(ContractModel):
    id: OpaqueId
    gate_policy: GatePolicy = "stage_atomic"
    required_review_roles: list[OpaqueId] = Field(default_factory=list, max_length=16)
    max_repair_rounds: int = Field(default=3, ge=0, le=10, strict=True)
    depends_on: list[OpaqueId] = Field(default_factory=list, max_length=16)


class TemplateWorkPackageBrief(ContractModel):
    id: OpaqueId
    stage_id: OpaqueId
    owner_role_id: OpaqueId
    objective: ShortText
    depends_on: list[OpaqueId] = Field(default_factory=list, max_length=32)
    output_kinds: list[str] = Field(default_factory=list, max_length=8)


class TemplateDocument(ContractModel):
    """schemaVersion 2: roles, stages, gates required. Legacy v1 must migrate explicitly."""

    schema_version: Literal["2"] = "2"
    name: ShortText
    parameters: dict[str, TemplateParameterSpec] = Field(default_factory=dict)
    routing_mode: RunMode = "auto"
    execution_profile: ExecutionProfile = "container"
    failure_policy: FailurePolicy = "dependency-aware"
    max_repair_rounds: int = Field(default=3, ge=0, le=10, strict=True)
    roles: list[TemplateRoleSpec] = Field(min_length=1, max_length=32)
    stages: list[TemplateStageSpec] = Field(min_length=1, max_length=32)
    work_packages: list[TemplateWorkPackageBrief] = Field(min_length=1, max_length=32)
    assignments_required: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def consistent_template(self) -> Self:
        role_ids = {item.id for item in self.roles}
        if len(role_ids) != len(self.roles):
            raise ValueError("Template role IDs must be unique")
        stage_ids = {item.id for item in self.stages}
        if len(stage_ids) != len(self.stages):
            raise ValueError("Template stage IDs must be unique")
        for stage in self.stages:
            if stage.id in stage.depends_on:
                raise ValueError("Template stage cannot depend on itself")
            if not set(stage.depends_on) <= stage_ids:
                raise ValueError("Template stage dependsOn references unknown stages")
            if not set(stage.required_review_roles) <= role_ids:
                raise ValueError("Template stage review roles must exist in roles")
        wp_ids = {item.id for item in self.work_packages}
        if len(wp_ids) != len(self.work_packages):
            raise ValueError("Template work package IDs must be unique")
        for item in self.work_packages:
            if item.id in item.depends_on:
                raise ValueError("Template work package cannot depend on itself")
            if not set(item.depends_on) <= wp_ids:
                raise ValueError("Template dependsOn references unknown work packages")
            if item.stage_id not in stage_ids:
                raise ValueError("Template work package stageId must reference a stage")
            if item.owner_role_id not in role_ids:
                raise ValueError("Template work package ownerRoleId must reference a role")
        return self


class TemplateDocumentV1Legacy(ContractModel):
    """Read-only legacy shape. Import must explicitly migrate to schemaVersion 2 proposals."""

    schema_version: Literal["1"] = "1"
    name: ShortText
    parameters: dict[str, TemplateParameterSpec] = Field(default_factory=dict)
    routing_mode: RunMode = "auto"
    execution_profile: ExecutionProfile = "container"
    failure_policy: FailurePolicy = "dependency-aware"
    max_repair_rounds: int = Field(default=3, ge=0, le=10, strict=True)
    work_packages: list[dict[str, Any]] = Field(min_length=1, max_length=32)


class TemplateSummary(ContractModel):
    id: OpaqueId
    name: ShortText
    schema_version: Literal["1", "2"] = "2"
    trust_state: Literal["untrusted", "reviewed", "rejected"]
    required_permissions: list[ShortText] = Field(default_factory=list, max_length=32)
    migration_required: bool = Field(
        default=False,
        strict=True,
        description="True when a v1 template needs explicit migration before use.",
    )


class TemplateListResponse(ContractModel):
    items: list[TemplateSummary] = Field(default_factory=list, max_length=100)


class TemplateImportRequest(ApiInputModel):
    yaml_text: str = Field(min_length=1, max_length=200_000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    migrate_from_v1: bool = Field(
        default=False,
        strict=True,
        description="When true, attempt explicit v1→v2 proposal migration (still needs user assign).",
    )

    @model_validator(mode="after")
    def bounded_parameters(self) -> Self:
        if len(self.parameters) > 64:
            raise ValueError("At most 64 template parameters are allowed")
        return self


class TemplateImportResponse(ContractModel):
    template_id: OpaqueId | None = None
    normalized: TemplateDocument | None = None
    errors: list[ShortText] = Field(default_factory=list, max_length=32)
    requires_permission_review: bool = Field(strict=True)
    requires_role_assignment: bool = Field(default=True, strict=True)
    migrated_from_v1: bool = Field(default=False, strict=True)


class TemplateExportResponse(ContractModel):
    yaml_text: str = Field(min_length=1, max_length=200_000)
    redactions: list[ShortText] = Field(default_factory=list, max_length=32)
