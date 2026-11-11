"""Agent product, profile, role assignment, and instance contracts (v1.4)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import (
    AssignmentVersion,
    OpaqueId,
    PlanVersion,
    RelativePath,
    Sha256Hex,
    ShortText,
    UtcDateTime,
)

CapabilitySupport = Literal["supported", "unsupported", "unknown", "failed"]
AccessLevel = Literal["controlled_auto", "limited_auto", "manual_assisted"]
AuthStatus = Literal["authenticated", "unauthenticated", "unknown", "expired"]
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


class AgentInstance(ContractModel):
    """Platform-managed role execution instance (not the API process instance)."""

    instance_id: OpaqueId
    assignment_id: OpaqueId
    run_id: OpaqueId
    role_id: OpaqueId
    agent_profile_id: OpaqueId
    session_ref: ShortText | None = None
    workspace_id: OpaqueId | None = None
    stage_id: OpaqueId | None = None
    status: AgentInstanceStatus
    execution_environment: ShortText
    current_attempt_id: OpaqueId | None = None
    permission_scope_ref: OpaqueId | None = None
    budget_ref: OpaqueId | None = None


class AgentInstanceActionRequest(ApiInputModel):
    action: Literal["request_stop", "resume", "close"]
    reason: str | None = Field(default=None, max_length=1024)


class AgentInstanceListResponse(ContractModel):
    items: list[AgentInstance] = Field(default_factory=list, max_length=100)


class ExecutionRequest(ApiInputModel):
    """Idempotent request to create/start confirmed role instances. Does not approve a plan."""

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
