"""Agent product, profile, role assignment, and instance contracts (v1.4 / D09)."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import (
    AssignmentVersion,
    OpaqueId,
    PlanVersion,
    RelativePath,
    ResourceRevision,
    Sha256Hex,
    ShortText,
    UtcDateTime,
)

CapabilitySupport = Literal["supported", "unsupported", "unknown", "failed"]
AccessLevel = Literal["controlled_auto", "limited_auto", "manual_assisted"]
AuthStatus = Literal["authenticated", "unauthenticated", "unknown", "expired"]

# Lifecycle answers "session / instance existence"; activity answers "working now".
# These dimensions must not substitute for each other or for D10 stage release.
InstanceLifecycleStatus = Literal[
    "created",
    "starting",
    "active",
    "stop_requested",
    "stopped",
    "unknown",
    "failed",
    "closed",
    "needs_reconciliation",
]
InstanceActivityStatus = Literal[
    "idle",
    "busy",
    "waiting_input",
    "waiting_approval",
]

# Legacy flat status — readable during migration; prefer lifecycle + activity.
AgentInstanceStatus = Literal[
    "created",
    "starting",
    "running",
    "waiting_input",
    "waiting_approval",
    "stop_requested",
    "stopped",
    "unknown",
    "reconciling",
    "blocked",
    "failed",
    "closed",
]

InstanceOperationKind = Literal[
    "create_session",
    "submit_task",
    "deliver_message",
    "request_stop",
    "resume",
    "close",
    "reconcile",
]
InstanceOperationStatus = Literal[
    "prepared",
    "claimed",
    "native_invoked",
    "succeeded",
    "failed",
    "unknown",
    "needs_reconciliation",
]
NativeSessionBindingStatus = Literal[
    "pending",
    "active",
    "needs_reconciliation",
    "closed",
    "superseded",
]


class AgentProduct(ContractModel):
    product_id: OpaqueId
    name: ShortText


class AgentInstallation(ContractModel):
    installation_id: OpaqueId
    product_id: OpaqueId
    product_version: ShortText
    environment_id: OpaqueId
    location_ref: ShortText
    discovered_at: UtcDateTime


class CapabilityResult(ContractModel):
    capability: ShortText
    support: CapabilitySupport
    limits: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)


class CapabilityCheck(ContractModel):
    check_id: OpaqueId
    profile_id: OpaqueId
    product_version: ShortText
    adapter_id: OpaqueId
    adapter_version: ShortText
    environment_id: OpaqueId
    checked_at: UtcDateTime
    valid_until: UtcDateTime | None = None
    results: list[CapabilityResult] = Field(min_length=1, max_length=64)
    evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)


class AgentProfile(ContractModel):
    profile_id: OpaqueId
    installation_id: OpaqueId
    adapter_id: OpaqueId
    adapter_version: ShortText
    config_version: ShortText
    config_hash: Sha256Hex
    auth_status: AuthStatus
    access_level: AccessLevel
    last_validation_id: OpaqueId | None = None
    secret_ref: ShortText | None = Field(
        default=None,
        description="Secure reference only; never a copied native long-lived secret.",
    )


class RoleSpec(ContractModel):
    role_id: OpaqueId
    responsibilities: ShortText
    required_capabilities: list[ShortText] = Field(min_length=1, max_length=32)
    read_scopes: list[RelativePath] = Field(default_factory=list, max_length=64)
    write_scopes: list[RelativePath] = Field(default_factory=list, max_length=64)
    budget: dict[str, Any] = Field(default_factory=dict)
    independent_review: bool = Field(strict=True)
    require_user_decision_on_gate: bool = Field(default=False, strict=True)


class RoleAssignment(ContractModel):
    assignment_id: OpaqueId
    run_id: OpaqueId
    plan_version: PlanVersion
    assignment_version: AssignmentVersion
    role_id: OpaqueId
    agent_profile_id: OpaqueId
    confirmed_at: UtcDateTime | None = None
    confirmed_by: ShortText | None = None


class RoleAssignmentPutItem(ApiInputModel):
    role_id: OpaqueId
    agent_profile_id: OpaqueId


class RoleAssignmentsPutRequest(ApiInputModel):
    plan_version: PlanVersion
    assignments: list[RoleAssignmentPutItem] = Field(min_length=1, max_length=32)


class RoleAssignmentsResponse(ContractModel):
    run_id: OpaqueId
    plan_version: PlanVersion
    assignment_version: AssignmentVersion
    items: list[RoleAssignment] = Field(default_factory=list, max_length=32)


class CurrentExecutionRef(ContractModel):
    """What the instance is bound to right now — not lifecycle and not idle/busy."""

    work_package_id: OpaqueId | None = None
    stage_id: OpaqueId | None = None
    attempt_id: OpaqueId | None = None
    generation: int | None = Field(default=None, ge=1, strict=True)


class AgentInstance(ContractModel):
    """Platform-managed role execution instance (not the API process instance).

    One instance may have many historical native sessions; only one active binding
    is valid at a time. Do not overwrite old sessionRef and lose history.
    """

    instance_id: OpaqueId
    assignment_id: OpaqueId
    run_id: OpaqueId
    role_id: OpaqueId
    agent_profile_id: OpaqueId
    assignment_version: AssignmentVersion | None = None
    session_ref: ShortText | None = None
    native_session_id: OpaqueId | None = None
    workspace_id: OpaqueId | None = None
    stage_id: OpaqueId | None = None
    lifecycle: InstanceLifecycleStatus | None = None
    activity: InstanceActivityStatus | None = None
    current_execution: CurrentExecutionRef | None = None
    # Legacy flat status — keep for readers; must agree with lifecycle/activity.
    status: AgentInstanceStatus | None = None
    execution_environment: ShortText
    current_attempt_id: OpaqueId | None = None
    permission_scope_ref: OpaqueId | None = None
    budget_ref: OpaqueId | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_flat_status(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        from agentforge.instances.status import split_legacy_instance_status

        status = data.get("status")
        lifecycle = data.get("lifecycle")
        activity = data.get("activity")
        if status is not None and lifecycle is None and activity is None:
            facets = split_legacy_instance_status(status)
            data = {**data, "lifecycle": facets.lifecycle, "activity": facets.activity}
        return data

    @model_validator(mode="after")
    def sync_legacy_status_and_execution(self) -> Self:
        from agentforge.instances.status import (
            combine_instance_status_facets,
            split_legacy_instance_status,
        )

        if self.lifecycle is None and self.status is None:
            raise ValueError("lifecycle or legacy status is required")
        if self.lifecycle is None and self.status is not None:
            facets = split_legacy_instance_status(self.status)
            object.__setattr__(self, "lifecycle", facets.lifecycle)
            object.__setattr__(self, "activity", facets.activity)
        assert self.lifecycle is not None
        if self.activity is None:
            object.__setattr__(self, "activity", "idle")

        if self.status is not None:
            facets = split_legacy_instance_status(self.status)
            if self.lifecycle != facets.lifecycle or self.activity != facets.activity:
                expected = combine_instance_status_facets(self.lifecycle, self.activity)
                if self.status != expected and not (
                    self.status == "blocked" and self.lifecycle == "active"
                ):
                    raise ValueError(
                        "legacy status must agree with lifecycle/activity facets "
                        f"(got status={self.status}, lifecycle={self.lifecycle}, "
                        f"activity={self.activity})"
                    )
        else:
            object.__setattr__(
                self,
                "status",
                combine_instance_status_facets(self.lifecycle, self.activity),
            )

        if self.current_attempt_id and self.current_execution is None:
            object.__setattr__(
                self,
                "current_execution",
                CurrentExecutionRef(
                    stage_id=self.stage_id,
                    attempt_id=self.current_attempt_id,
                ),
            )
        elif self.current_execution and self.current_execution.attempt_id:
            if self.current_attempt_id is None:
                object.__setattr__(
                    self, "current_attempt_id", self.current_execution.attempt_id
                )
            elif self.current_attempt_id != self.current_execution.attempt_id:
                raise ValueError("currentAttemptId must match currentExecution.attemptId")
        return self


class NativeSession(ContractModel):
    """Native product session — distinct from AgentInstance and from attempts."""

    native_session_id: OpaqueId
    instance_id: OpaqueId
    product_id: OpaqueId
    profile_id: OpaqueId
    config_version: ShortText
    environment_id: OpaqueId
    session_ref: ShortText | None = None
    create_operation_id: OpaqueId
    binding_status: NativeSessionBindingStatus
    workspace_id: OpaqueId | None = None
    created_at: UtcDateTime
    closed_at: UtcDateTime | None = None
    evidence_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)


class InstanceAttempt(ContractModel):
    """One actual execution under the current context (task/stage/generation/workspace)."""

    attempt_id: OpaqueId
    instance_id: OpaqueId
    run_id: OpaqueId
    work_package_id: OpaqueId
    stage_id: OpaqueId | None = None
    generation: int = Field(ge=1, strict=True)
    workspace_id: OpaqueId
    native_session_id: OpaqueId | None = None
    created_at: UtcDateTime


class InstanceOperation(ContractModel):
    """Intent and result for create/submit/stop/resume/close — not task completion."""

    operation_id: OpaqueId
    instance_id: OpaqueId
    kind: InstanceOperationKind
    status: InstanceOperationStatus
    idempotency_key: ShortText | None = None
    native_ref: ShortText | None = None
    attempt_id: OpaqueId | None = None
    prepared_at: UtcDateTime
    completed_at: UtcDateTime | None = None
    error_code: ShortText | None = None


class ExecutionEligibility(ContractModel):
    """Unified admission check before any stage task dispatch (D09↔D10 boundary)."""

    run_id: OpaqueId
    allowed_work_package_ids: list[OpaqueId] = Field(default_factory=list, max_length=64)
    allowed_stage_ids: list[OpaqueId] = Field(default_factory=list, max_length=32)
    plan_version: PlanVersion
    assignment_version: AssignmentVersion
    authorization_version: ResourceRevision | None = None
    required_candidate_ids: list[OpaqueId] = Field(default_factory=list, max_length=32)
    gate_decision_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    valid_until: UtcDateTime | None = None
    revalidate_on: list[ShortText] = Field(default_factory=list, max_length=16)
    blocked_reason: ShortText | None = None


class AgentInstanceActionRequest(ApiInputModel):
    action: Literal["request_stop", "resume", "close"]
    reason: str | None = Field(default=None, max_length=1024)


class AgentInstanceListResponse(ContractModel):
    items: list[AgentInstance] = Field(default_factory=list, max_length=100)


class ExecutionRequest(ApiInputModel):
    """Idempotent request to prepare confirmed role instances. Does not approve a plan.

    Binding must include planHash + planVersion + assignmentVersion + gatePolicyVersion.
    Does not start arbitrary stage tasks; D10 release still required for later stages.
    """

    plan_hash: Sha256Hex
    plan_version: PlanVersion
    assignment_version: AssignmentVersion
    gate_policy_version: int = Field(ge=1, strict=True)


class DiscoveryRunRequest(ApiInputModel):
    environments: list[ShortText] = Field(default_factory=lambda: ["windows_native"], max_length=8)
    adapter_ids: list[OpaqueId] | None = Field(default=None, max_length=16)
    persist: bool = Field(default=True, strict=True)


class DiscoveredInstallationView(ContractModel):
    product_id: OpaqueId
    product_version: ShortText
    environment_id: OpaqueId
    location_ref: ShortText
    auth_status: AuthStatus = "unknown"
    adapter_id: OpaqueId | None = None
    notes: list[ShortText] = Field(default_factory=list, max_length=32)
    installation_id: OpaqueId | None = None


class DiscoveryRunResponse(ContractModel):
    items: list[DiscoveredInstallationView] = Field(default_factory=list, max_length=200)
    note: ShortText = "Discovery is not schedulability; accessLevel requires capability evidence"


class ManualInstallationRegisterRequest(ApiInputModel):
    product_id: OpaqueId
    product_version: ShortText = "unknown"
    environment_id: OpaqueId = "windows_native"
    location_ref: ShortText
    adapter_id: OpaqueId
    auth_status: AuthStatus = "unknown"


class ProfileCreateRequest(ApiInputModel):
    installation_id: OpaqueId
    adapter_id: OpaqueId


class ProfileValidateResponse(ContractModel):
    profile: AgentProfile
    capability_check: CapabilityCheck
    access_level: AccessLevel
    note: ShortText = (
        "Product capability ≠ role readiness; approvalRoundtrip stays "
        "unsupported until product ack is evidenced"
    )
