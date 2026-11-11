"""Run, operation, and action contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import (
    ArtifactKind,
    AssignmentVersion,
    BudgetLimits,
    BudgetLimitsInput,
    ExecutionProfile,
    FailurePolicy,
    OpaqueId,
    PageInfo,
    PlanVersion,
    ResourceRevision,
    RunMode,
    RunSequence,
    RunStatus,
    ShortText,
    UtcDateTime,
)


class RunCreateRequest(ApiInputModel):
    """Create a goal and enter preflight/planning. Does not dispatch development.

    ``mode=auto`` means the platform may auto-propose a plan; it does **not** mean
    the platform may confirm assignments or start executors without user decisions.
    ``planningProviderProfileId`` configures the platform planning model only.
    """

    project_id: OpaqueId
    goal: ShortText
    mode: RunMode = "auto"
    input_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)
    outputs: list[ArtifactKind] = Field(min_length=1, max_length=8)
    execution_profile: ExecutionProfile = "container"
    failure_policy: FailurePolicy = "dependency-aware"
    budget: BudgetLimitsInput
    planning_provider_profile_id: OpaqueId = Field(
        validation_alias=AliasChoices(
            "planningProviderProfileId",
            "providerProfileId",  # deprecated alias during migration
        ),
    )


class RunAcceptedResponse(ContractModel):
    run_id: OpaqueId
    status: RunStatus
    status_version: ResourceRevision
    events_url: ShortText


class RunSummary(ContractModel):
    id: OpaqueId
    project_id: OpaqueId
    goal: ShortText
    status: RunStatus
    status_version: ResourceRevision
    plan_version: PlanVersion | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class RunListResponse(ContractModel):
    items: list[RunSummary] = Field(default_factory=list, max_length=100)
    page: PageInfo


class RunSnapshotResponse(ContractModel):
    id: OpaqueId
    project_id: OpaqueId
    goal: ShortText
    mode: RunMode
    status: RunStatus
    status_version: ResourceRevision
    plan_version: PlanVersion | None = None
    assignment_version: AssignmentVersion | None = None
    gate_policy_version: int | None = Field(default=None, ge=1, strict=True)
    active_generation: int = Field(ge=1, strict=True)
    latest_sequence: RunSequence
    execution_profile: ExecutionProfile
    failure_policy: FailurePolicy
    budget: BudgetLimits
    planning_provider_profile_id: OpaqueId
    input_refs: list[OpaqueId] = Field(default_factory=list)
    outputs: list[ArtifactKind]
    cancel_requested_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class RunActionRequest(ApiInputModel):
    action: Literal["pause", "resume", "cancel", "replan"]
    reason: str | None = Field(default=None, max_length=1024)


class OperationAccepted(ContractModel):
    operation_id: OpaqueId
    status: Literal[
        "accepted",
        "running",
        "waiting_user",
        "succeeded",
        "failed",
        "blocked",
        "needs_reconciliation",
    ] = "accepted"
    run_id: OpaqueId | None = None


class OperationStatusResponse(ContractModel):
    operation_id: OpaqueId
    operation_type: ShortText
    status: Literal[
        "accepted",
        "running",
        "waiting_user",
        "succeeded",
        "failed",
        "blocked",
        "canceled",
        "needs_reconciliation",
    ]
    run_id: OpaqueId | None = None
    result_ref: OpaqueId | None = None
    uncertain_side_effects: bool = Field(strict=True)
    message: ShortText | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
