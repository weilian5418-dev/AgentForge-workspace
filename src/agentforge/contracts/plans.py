"""Plan, routing, confirmation, and approval contracts (D08 + D04)."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from agentforge.contracts.agents import RoleAssignment, RoleSpec
from agentforge.contracts.artifacts import ArtifactContract, ArtifactRef, WorkPackage
from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import (
    AssignmentVersion,
    OpaqueId,
    PlanStatus,
    PlanVersion,
    RelativePath,
    ResourceRevision,
    RoutingModeDecision,
    Sha256Hex,
    ShortText,
    UtcDateTime,
)
from agentforge.contracts.stages import StageGatePolicy

ApprovalStatus = Literal[
    "pending",
    "user_decided",
    "forwarding",
    "confirmed",
    "approved",  # legacy user decision only — not native-effective
    "rejected",
    "expired",
    "consumed",  # legacy
    "mapping_unsupported",
    "manual_required",
    "delivery_unknown",
]

CompilationStatus = Literal["valid", "needs_input", "blocked", "invalid"]
DiagnosticSeverity = Literal["error", "warning", "info"]
ProposalSource = Literal["rules", "model", "auto"]
ConfirmationRequestStatus = Literal[
    "pending",
    "approved",
    "rejected",
    "superseded",
    "expired",
]
PlanConfirmationState = Literal[
    "none",
    "awaiting_confirmation",
    "approved",
    "rejected",
]


class RoutingDecision(ContractModel):
    """Suggestion only. Never authorizes execution by itself."""

    mode: RoutingModeDecision
    score: int | None = Field(default=None, ge=0, le=10, strict=True)
    confidence: float | None = Field(default=None, ge=0, le=1, strict=True)
    reason: ShortText
    rule_version: ShortText
    model_review_count: int = Field(ge=0, le=1, strict=True)


class CompilationDiagnostic(ContractModel):
    code: ShortText
    severity: DiagnosticSeverity
    entity_type: ShortText | None = None
    entity_id: OpaqueId | None = None
    field_path: ShortText | None = None
    message: ShortText
    suggested_resolution: ShortText | None = None


class CompilationReport(ContractModel):
    """Compiler output — not a RunStatus."""

    status: CompilationStatus
    compiler_version: ShortText = "plan-compiler-v1"
    issues: list[CompilationDiagnostic] = Field(default_factory=list, max_length=256)
    warnings: list[CompilationDiagnostic] = Field(default_factory=list, max_length=256)
    capability_evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)


class PlanningContext(ContractModel):
    """Versioned authorized facts used for one planning attempt."""

    context_id: OpaqueId
    project_id: OpaqueId
    run_id: OpaqueId
    baseline_id: OpaqueId
    input_ids: list[OpaqueId] = Field(default_factory=list, max_length=64)
    read_scopes: list[RelativePath] = Field(default_factory=list, max_length=64)
    summary: ShortText
    content_hash: Sha256Hex
    context_manifest_id: OpaqueId | None = Field(
        default=None,
        description="Set only when a real ContextManifest was built for model egress; never fake.",
    )
    created_at: UtcDateTime


class PlanWorkPackageSpec(ContractModel):
    """Plan-time work package definition (no runtime repair/status fields)."""

    id: OpaqueId
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
    shared_write_coordination: ShortText | None = Field(
        default=None,
        description="Required when write scopes overlap with another package.",
    )

    @model_validator(mode="after")
    def valid_dependencies(self) -> Self:
        if self.id in self.depends_on or len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("Dependencies must be unique and cannot include this work package")
        return self


class PlanStageDefinition(ContractModel):
    """Stage definition inside a plan — distinct from execution Stage instances (D09+)."""

    stage_id: OpaqueId
    dependencies: list[OpaqueId] = Field(default_factory=list, max_length=32)
    required_work_packages: list[OpaqueId] = Field(default_factory=list, max_length=64)
    gate_policy: StageGatePolicy

    @model_validator(mode="after")
    def no_self_dependency(self) -> Self:
        if self.stage_id in self.dependencies:
            raise ValueError("Stage cannot depend on itself")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("Stage dependencies must be unique")
        return self


class PlanResourceEstimate(ContractModel):
    """Estimate only — does not reserve execution budget."""

    max_tokens: int | None = Field(default=None, ge=1, strict=True)
    max_wall_time_seconds: int | None = Field(default=None, ge=1, strict=True)
    estimated_cost: str | None = Field(default=None, max_length=32)
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    basis: ShortText
    uncertainties: list[ShortText] = Field(default_factory=list, max_length=16)


class IntegrationPolicySpec(ContractModel):
    policy_ref: ShortText
    checks: list[dict[str, Any]] = Field(min_length=1, max_length=32)
    required_review_roles: list[OpaqueId] = Field(default_factory=list, max_length=16)
    execution_mode: Literal["container", "host_authorized"] = "container"


class PlanSpec(ContractModel):
    integration_policy: IntegrationPolicySpec | None = None
    """Immutable complete plan definition for one planVersion."""

    goal: ShortText
    constraints: list[ShortText] = Field(default_factory=list, max_length=32)
    baseline_id: OpaqueId
    input_ids: list[OpaqueId] = Field(default_factory=list, max_length=64)
    routing: RoutingDecision
    roles: list[RoleSpec] = Field(min_length=1, max_length=32)
    work_packages: list[PlanWorkPackageSpec] = Field(min_length=1, max_length=64)
    stages: list[PlanStageDefinition] = Field(min_length=1, max_length=32)
    gate_policy_version: int = Field(ge=1, strict=True)
    resource_estimate: PlanResourceEstimate | None = None
    hash_algo_version: ShortText = "plan-hash-v1"

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        role_ids = [r.role_id for r in self.roles]
        if len(set(role_ids)) != len(role_ids):
            raise ValueError("Role IDs must be unique")
        wp_ids = [w.id for w in self.work_packages]
        if len(set(wp_ids)) != len(wp_ids):
            raise ValueError("Work package IDs must be unique")
        stage_ids = [s.stage_id for s in self.stages]
        if len(set(stage_ids)) != len(stage_ids):
            raise ValueError("Stage IDs must be unique")
        return self


class PlanProposal(ContractModel):
    """Uncompiled candidate from rules or model — not an effective plan."""

    proposal_id: OpaqueId
    run_id: OpaqueId
    context_id: OpaqueId
    source: ProposalSource
    model_call_id: OpaqueId | None = None
    draft: PlanSpec
    diagnostics: list[CompilationDiagnostic] = Field(default_factory=list, max_length=64)
    created_at: UtcDateTime


class PlanRevision(ContractModel):
    """Immutable stored plan revision (full replacement)."""

    run_id: OpaqueId
    plan_version: PlanVersion
    plan_hash: Sha256Hex
    contract_hash: Sha256Hex
    status: PlanStatus
    spec: PlanSpec
    compilation: CompilationReport
    context_id: OpaqueId
    supersedes_version: PlanVersion | None = None
    created_at: UtcDateTime


class AssignmentSet(ContractModel):
    """Role→profile mapping for one planVersion + assignmentVersion."""

    run_id: OpaqueId
    plan_version: PlanVersion
    assignment_version: AssignmentVersion
    items: list[RoleAssignment] = Field(min_length=1, max_length=32)
    capability_check_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)
    profile_config_versions: dict[str, str] = Field(default_factory=dict)
    created_at: UtcDateTime


class ConfirmationRequest(ContractModel):
    """Exact confirmation scope awaiting a user decision."""

    request_id: OpaqueId
    run_id: OpaqueId
    plan_version: PlanVersion
    plan_hash: Sha256Hex
    assignment_version: AssignmentVersion
    gate_policy_version: int = Field(ge=1, strict=True)
    status_version: ResourceRevision
    compilation_report_ref: OpaqueId | None = None
    capability_check_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)
    status: ConfirmationRequestStatus = "pending"
    failure_reason: ShortText | None = None
    created_at: UtcDateTime
    expires_at: UtcDateTime | None = None


class PlanDecisionRecord(ContractModel):
    """Persisted user decision against one ConfirmationRequest."""

    decision_id: OpaqueId
    request_id: OpaqueId
    run_id: OpaqueId
    actor: ShortText
    decision: Literal["approve", "reject"]
    note: ShortText | None = None
    decided_at: UtcDateTime


class PlanResponse(ContractModel):
    """Current plan view for a run (definition + compile + assignment + confirm)."""

    run_id: OpaqueId
    plan_version: PlanVersion
    plan_hash: Sha256Hex
    contract_hash: Sha256Hex
    status: PlanStatus
    summary: str = Field(default="", max_length=4096)
    baseline_id: OpaqueId
    input_ids: list[OpaqueId] = Field(default_factory=list, max_length=64)
    context_id: OpaqueId | None = None
    routing: RoutingDecision | None = None
    roles: list[RoleSpec] = Field(default_factory=list, max_length=32)
    stages: list[PlanStageDefinition] = Field(default_factory=list, max_length=32)
    gate_policy: StageGatePolicy | None = None
    work_packages: list[PlanWorkPackageSpec] = Field(default_factory=list, max_length=64)
    # Legacy mirror for older clients still expecting WorkPackage with planVersion.
    legacy_work_packages: list[WorkPackage] = Field(default_factory=list, max_length=64)
    assignments: list[RoleAssignment] = Field(default_factory=list, max_length=32)
    assignment_version: AssignmentVersion | None = None
    gate_policy_version: int | None = Field(default=None, ge=1, strict=True)
    compilation: CompilationReport | None = None
    confirmation: ConfirmationRequest | None = None
    confirmation_state: PlanConfirmationState = "none"
    created_at: UtcDateTime


class PlanProposalRequest(ApiInputModel):
    """Request a read-only plan proposal. Does not start development."""

    include_input_content: bool = Field(default=False, strict=True)
    max_input_bytes: int = Field(default=65536, ge=1, le=131072, strict=True)
    grant_id: OpaqueId | None = None
    idempotency_key: OpaqueId | None = None
    source: ProposalSource = "auto"
    note: str | None = Field(default=None, max_length=2048)


class PlanRevisionRequest(ApiInputModel):
    """Full-replacement revision of the current plan → new planVersion."""

    expected_plan_version: PlanVersion
    expected_status_version: ResourceRevision
    spec: PlanSpec
    note: str | None = Field(default=None, max_length=2048)


class PlanDecisionRequest(ApiInputModel):
    """Confirm or reject a concrete confirmation request binding."""

    decision: Literal["approve", "reject"]
    confirmation_request_id: OpaqueId
    plan_version: PlanVersion
    plan_hash: Sha256Hex
    assignment_version: AssignmentVersion
    gate_policy_version: int = Field(ge=1, strict=True)
    status_version: ResourceRevision
    note: str | None = Field(default=None, max_length=2048)


class NativeApprovalMapping(ContractModel):
    product_id: OpaqueId
    profile_id: OpaqueId
    session_ref: ShortText
    native_scope: ShortText
    platform_scope: ShortText
    mapping_version: ShortText
    decision_id: OpaqueId | None = None
    forward_state: Literal[
        "not_started",
        "forwarding",
        "confirmed",
        "rejected",
        "mapping_unsupported",
        "delivery_unknown",
    ] = "not_started"
    native_confirmation_ref: ShortText | None = None


class ApprovalSummary(ContractModel):
    """User decision plus native forward/confirm state.

    ``approved`` / user decide alone is **not** proof the external agent accepted the grant.
    Only ``confirmed`` (with matching native request) is effective for native actions.
    """

    id: OpaqueId
    run_id: OpaqueId | None = None
    instance_id: OpaqueId | None = None
    role_id: OpaqueId | None = None
    stage_id: OpaqueId | None = None
    attempt_id: OpaqueId | None = None
    action: ShortText
    target_summary: ShortText
    parameter_summary: ShortText
    action_digest: Sha256Hex
    requested_scope: ShortText | None = None
    native_request_id: ShortText | None = None
    mapping: NativeApprovalMapping | None = None
    expires_at: UtcDateTime
    status: ApprovalStatus
    user_decision: Literal["approve", "reject"] | None = None
    native_confirmed: bool = Field(
        default=False,
        strict=True,
        description="True only after native confirmation for the same request/attempt.",
    )


class ApprovalListResponse(ContractModel):
    items: list[ApprovalSummary] = Field(default_factory=list, max_length=100)


class ApprovalDecisionRequest(ApiInputModel):
    decision: Literal["approve", "reject"]
    action_digest: Sha256Hex
    note: str | None = Field(default=None, max_length=2048)
    forward: bool = Field(
        default=True,
        strict=True,
        description="When true, attempt native forward after recording the user decision.",
    )


class NativeApprovalRecordRequest(ApiInputModel):
    """Inbound native approval request (adapter/host → platform ledger)."""

    action: ShortText
    target_summary: ShortText
    parameter_summary: ShortText
    requested_scope: ShortText
    platform_scope: ShortText
    native_scope: ShortText
    product_id: OpaqueId
    profile_id: OpaqueId
    session_ref: ShortText
    native_request_id: ShortText
    adapter_id: OpaqueId
    operation_type: ShortText
    actor: ShortText = "external_agent"
    semantic: Literal["once", "stage", "session"] = "once"
    expires_at: UtcDateTime
    action_digest: Sha256Hex | None = Field(
        default=None,
        description="Optional client digest; server recomputes ActionDescriptor and rejects mismatches.",
    )
    native_options: list[ShortText] = Field(
        default_factory=lambda: ["allow", "deny"],
        max_length=16,
    )
    # Typed final-action bind (shared with Runner). When set, digest uses these fields.
    workspace_id: OpaqueId | None = None
    workspace_root: ShortText | None = None
    params: dict[str, Any] | None = None
    environment_id: OpaqueId | None = None
    generation: int | None = Field(default=None, ge=1, strict=True)
    run_id: OpaqueId | None = None
    instance_id: OpaqueId | None = None
    role_id: OpaqueId | None = None
    stage_id: OpaqueId | None = None
    attempt_id: OpaqueId | None = None


class ApprovalConfirmRequest(ApiInputModel):
    """Manual/native ack when adapter reports confirmation asynchronously."""

    native_request_id: ShortText
    confirmation_ref: ShortText
