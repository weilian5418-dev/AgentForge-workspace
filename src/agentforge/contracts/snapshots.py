"""Consistent run snapshot / pagination contracts (D12)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.collaboration import CollaborationProgress
from agentforge.contracts.common import (
    ArtifactKind,
    AssignmentVersion,
    BudgetLimits,
    ExecutionProfile,
    FailurePolicy,
    OpaqueId,
    PlanVersion,
    ResourceRevision,
    RunMode,
    RunSequence,
    RunStatus,
    Sha256Hex,
    ShortText,
    UtcDateTime,
)

SNAPSHOT_SCHEMA_VERSION = "1"

TechnicalStatus = Literal[
    "not_started",
    "building",
    "conflicted",
    "checking",
    "waiting_review",
    "passed",
    "failed",
    "blocked",
    "unknown",
]
AcceptanceStatus = Literal[
    "none",
    "awaiting_acceptance",
    "accepted",
    "rejected",
    "partially_accepted",
    "invalidated",
]


class RunOverview(ContractModel):
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
    execution_profile: ExecutionProfile
    failure_policy: FailurePolicy
    budget: BudgetLimits
    planning_provider_profile_id: OpaqueId | None = None
    baseline_id: OpaqueId | None = None
    input_refs: list[OpaqueId] = Field(default_factory=list, max_length=64)
    outputs: list[ArtifactKind] = Field(default_factory=list, max_length=8)
    cancel_requested_at: UtcDateTime | None = None
    collaboration_state: CollaborationProgress | None = None
    collaboration_ready_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class ExecutionBindingOverview(ContractModel):
    plan_version: PlanVersion | None = None
    assignment_version: AssignmentVersion | None = None
    gate_policy_version: int | None = Field(default=None, ge=1, strict=True)
    active_generation: int = Field(ge=1, strict=True)
    handoff_ref: OpaqueId | None = None
    handoff_hash: Sha256Hex | None = None
    graph_version: ShortText | None = None
    binding_hash: Sha256Hex | None = None


class AgentCounts(ContractModel):
    """Three inventory counts — never collapse products/profiles/instances."""

    environment_id: OpaqueId
    discovered_product_count: int = Field(ge=0, strict=True)
    schedulable_profile_count: int = Field(ge=0, strict=True)
    active_instance_count: int = Field(ge=0, strict=True)
    manual_assisted_profile_count: int = Field(default=0, ge=0, strict=True)
    limited_auto_profile_count: int = Field(default=0, ge=0, strict=True)
    verified_but_busy_profile_count: int = Field(default=0, ge=0, strict=True)
    unknown_profile_count: int = Field(default=0, ge=0, strict=True)
    busy_instance_count: int = Field(default=0, ge=0, strict=True)
    waiting_approval_instance_count: int = Field(default=0, ge=0, strict=True)
    stopped_instance_count: int = Field(default=0, ge=0, strict=True)
    unknown_instance_count: int = Field(default=0, ge=0, strict=True)
    needs_reconciliation_instance_count: int = Field(default=0, ge=0, strict=True)
    inventory_revision: int = Field(ge=1, strict=True)
    as_of: UtcDateTime
    scope_note: ShortText | None = None


class StageSummary(ContractModel):
    total: int = Field(ge=0, strict=True)
    pending: int = Field(default=0, ge=0, strict=True)
    executing: int = Field(default=0, ge=0, strict=True)
    reviewing: int = Field(default=0, ge=0, strict=True)
    rework: int = Field(default=0, ge=0, strict=True)
    released: int = Field(default=0, ge=0, strict=True)
    blocked: int = Field(default=0, ge=0, strict=True)
    failed: int = Field(default=0, ge=0, strict=True)


class ProgressSummary(ContractModel):
    """Explicit counters — not a misleading single percentage of Run completion."""

    work_packages_total: int = Field(ge=0, strict=True)
    work_packages_ready: int = Field(default=0, ge=0, strict=True)
    work_packages_running: int = Field(default=0, ge=0, strict=True)
    work_packages_submitted: int = Field(default=0, ge=0, strict=True)
    work_packages_released: int = Field(default=0, ge=0, strict=True)
    work_packages_blocked: int = Field(default=0, ge=0, strict=True)
    work_packages_failed: int = Field(default=0, ge=0, strict=True)
    required_packages_total: int = Field(default=0, ge=0, strict=True)
    required_packages_released: int = Field(default=0, ge=0, strict=True)
    final_checks_required: int = Field(default=0, ge=0, strict=True)
    final_checks_passed: int = Field(default=0, ge=0, strict=True)
    final_checks_failed: int = Field(default=0, ge=0, strict=True)
    final_checks_unknown: int = Field(default=0, ge=0, strict=True)
    final_checks_not_run: int = Field(default=0, ge=0, strict=True)
    final_reviews_required: int = Field(default=0, ge=0, strict=True)
    final_reviews_passed: int = Field(default=0, ge=0, strict=True)
    final_reviews_pending: int = Field(default=0, ge=0, strict=True)
    acceptance_status: AcceptanceStatus = "none"
    collaboration_progress_ratio: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="releasedRequiredPackages/totalRequiredPackages only; 1.0 ≠ Run completed",
    )


class DeliveryOverview(ContractModel):
    current_integration_version: OpaqueId | None = None
    current_manifest_id: OpaqueId | None = None
    manifest_hash: Sha256Hex | None = None
    manifest_revision: int | None = Field(default=None, ge=1, strict=True)
    technical_status: TechnicalStatus = "not_started"
    acceptance_status: AcceptanceStatus = "none"
    delivery_revision: int = Field(ge=1, strict=True)
    invalidated_reason: ShortText | None = None


class WaitSummary(ContractModel):
    open_count: int = Field(ge=0, strict=True)
    by_reason: dict[str, int] = Field(default_factory=dict, max_length=32)


class RunSnapshot(ContractModel):
    """Single-transaction bounded snapshot — not a dump of ORM graphs."""

    schema_version: Literal["1"] = "1"
    run: RunOverview
    binding: ExecutionBindingOverview
    agent_counts: AgentCounts
    stage_summary: StageSummary
    progress: ProgressSummary
    current_delivery: DeliveryOverview | None = None
    wait_summary: WaitSummary
    latest_sequence: RunSequence
    latest_event_id: OpaqueId | None = None
    replay_floor_sequence: RunSequence = 0
    snapshot_token: ShortText
    inventory_revision: int | None = Field(default=None, ge=1, strict=True)


class SnapshotTokenClaims(ContractModel):
    """Signed claims for pagination / resync binding — not an auth grant."""

    run_id: OpaqueId
    latest_sequence: RunSequence
    schema_version: ShortText
    query_scope_hash: Sha256Hex
    expires_at: UtcDateTime
    status_version: ResourceRevision | None = None
    delivery_revision: int | None = Field(default=None, ge=1, strict=True)


class PageCursorRequest(ApiInputModel):
    snapshot_token: ShortText | None = None
    cursor: ShortText | None = None
    limit: int = Field(default=50, ge=1, le=100, strict=True)


class PageCursorResponse(ContractModel):
    next_cursor: ShortText | None = None
    page_as_of_sequence: RunSequence
    binding_revision: int | None = Field(default=None, ge=1, strict=True)
    exact_snapshot_match: bool = Field(strict=True)
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class AgentSummaryResponse(ContractModel):
    counts: AgentCounts
    required_capabilities: list[ShortText] = Field(default_factory=list, max_length=64)
