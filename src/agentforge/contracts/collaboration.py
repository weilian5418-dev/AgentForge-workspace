"""D11 collaboration graph contracts: binding, waits, wakes, driver results.

Invariants (not interchangeable):
- Graph does not approve plans or invent model grants.
- Lease expiry does not prove native stop.
- ``unknown`` occupies a write slot; native ``done`` is not a D10 release.
- New-session handoff is not native session resume.
- Final Run ``completed`` belongs to D12 — D11 stops at ready_for_integration.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import (
    AssignmentVersion,
    OpaqueId,
    PlanVersion,
    ResourceRevision,
    Sha256Hex,
    ShortText,
    UtcDateTime,
)

GRAPH_VERSION = "d11.1"
STATE_SCHEMA_VERSION = 1

CollaborationProgress = Literal[
    "idle",
    "running",
    "waiting",
    "ready_for_integration",
    "blocked",
]

WaitReason = Literal[
    "plan_confirmation",
    "workspace_ready",
    "session_ready",
    "task_submission",
    "native_approval",
    "stage_review",
    "user_gate_decision",
    "stop_verification",
    "recovery_evidence",
    "capacity",
    "dependency",
    "driver_yield",
]

WaitStatus = Literal["open", "resolved", "canceled", "superseded"]

WakeSourceKind = Literal[
    "plan_confirmation",
    "instance_operation",
    "delivery",
    "stage_gate",
    "stage_submission",
    "review_decision",
    "user_decision",
    "control",
    "recovery",
    "authorization",
    "timer",
    "sweep",
]

WakeSignalStatus = Literal["pending", "claimed", "completed", "obsolete"]

DriverOutcome = Literal[
    "busy",
    "interrupted",
    "yielded",
    "ready_for_integration",
    "blocked",
    "fence_lost",
    "noop",
]

NextActionKind = Literal[
    "register_wait",
    "ensure_workspaces",
    "ensure_instances",
    "dispatch_ready",
    "accept_submissions",
    "prepare_rework",
    "coordinate_stop",
    "open_recovery",
    "prepare_handoff",
    "refresh",
    "end",
]

SchedulerOperationKind = Literal[
    "workspace_prepare",
    "instance_prepare",
    "task_assign",
    "submission_bridge",
    "rework_prepare",
    "integration_handoff",
    "recovery_inspect",
    "control_coordinate",
]

SchedulerOperationStatus = Literal[
    "accepted",
    "waiting",
    "succeeded",
    "failed",
    "blocked",
    "canceled",
    "needs_reconciliation",
]

DriverThreadStatus = Literal["active", "archived", "superseded", "blocked"]

InstanceAttemptStatus = Literal[
    "scheduled",
    "dispatching",
    "running",
    "submitted",
    "awaiting_stop_verification",
    "stopped",
    "failed",
    "unknown",
    "canceled",
    "superseded",
]

ACTIVE_INSTANCE_ATTEMPT_STATUSES: frozenset[str] = frozenset(
    {
        "scheduled",
        "dispatching",
        "running",
        "submitted",
        "awaiting_stop_verification",
        "unknown",
    }
)

DriverErrorCode = Literal[
    "driver_busy",
    "driver_fence_lost",
    "stale_binding",
    "status_version_conflict",
    "idempotency_conflict",
    "wait_scope_mismatch",
    "checkpoint_incompatible",
    "native_state_unknown",
    "workspace_mismatch",
    "pending_side_effects",
    "pending_stop_verification",
    "recovery_evidence_stale",
    "resume_unsupported",
    "handoff_confirmation_required",
]


class GraphBinding(ContractModel):
    """Immutable coordination identity for one driver thread.

    Do not embed lease tokens or statusVersion — wake must not mint a new thread.
    """

    run_id: OpaqueId
    plan_version: PlanVersion
    assignment_version: AssignmentVersion
    gate_policy_version: int = Field(ge=1, strict=True)
    execution_generation: int = Field(ge=1, strict=True)
    graph_version: ShortText = GRAPH_VERSION
    state_schema_version: int = Field(default=STATE_SCHEMA_VERSION, ge=1, strict=True)
    checkpoint_epoch: int = Field(default=1, ge=1, strict=True)


class LeaseFence(ContractModel):
    """Runtime fencing identity — pass via DriverContext only, never in checkpoint state."""

    run_id: OpaqueId
    owner: ShortText
    token: ShortText
    lease_generation: int = Field(ge=1, strict=True)
    thread_id: OpaqueId | None = None
    binding_hash: Sha256Hex | None = None


class WakeSignal(ContractModel):
    """Durable wake hint — not a business fact; Driver re-reads the ledger."""

    wake_signal_id: OpaqueId
    run_id: OpaqueId
    source_kind: WakeSourceKind
    source_ref: ShortText
    source_sequence: int | None = Field(default=None, ge=0, strict=True)
    binding_json: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=100, ge=0, le=1000, strict=True)
    status: WakeSignalStatus = "pending"
    claim_owner: ShortText | None = None
    claim_expires_at: UtcDateTime | None = None
    created_at: UtcDateTime


class RunWait(ContractModel):
    """Persistent wait barrier. Predicate is reason + versioned scope, not arbitrary SQL."""

    wait_id: OpaqueId
    run_id: OpaqueId
    thread_id: OpaqueId | None = None
    wait_key: ShortText
    reason: WaitReason
    scope_json: dict[str, Any] = Field(default_factory=dict)
    predicate_version: int = Field(ge=1, strict=True)
    after_sequence: int = Field(default=0, ge=0, strict=True)
    status: WaitStatus = "open"
    deadline_at: UtcDateTime | None = None
    resolved_by_ref: OpaqueId | None = None
    created_at: UtcDateTime
    resolved_at: UtcDateTime | None = None


class NextAction(ContractModel):
    """Pure planner output — no I/O."""

    kind: NextActionKind
    stage_id: OpaqueId | None = None
    work_package_id: OpaqueId | None = None
    wait_reason: WaitReason | None = None
    wait_key: ShortText | None = None
    operation_key: ShortText | None = None
    recovery_reason: ShortText | None = None
    detail: ShortText | None = None


class SchedulerOperation(ContractModel):
    """Intent the graph proposed to a domain service — not native success."""

    operation_id: OpaqueId
    run_id: OpaqueId
    thread_id: OpaqueId | None = None
    kind: SchedulerOperationKind
    operation_key: ShortText
    request_hash: Sha256Hex
    binding_json: dict[str, Any] = Field(default_factory=dict)
    status: SchedulerOperationStatus
    result_refs: list[OpaqueId] = Field(default_factory=list, max_length=32)
    error_code: DriverErrorCode | ShortText | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class DriverThread(ContractModel):
    """Public coordination thread view — no lease token."""

    thread_row_id: OpaqueId
    run_id: OpaqueId
    thread_id: OpaqueId
    plan_version: PlanVersion
    assignment_version: AssignmentVersion
    gate_policy_version: int = Field(ge=1, strict=True)
    execution_generation: int = Field(ge=1, strict=True)
    graph_version: ShortText
    state_schema_version: int = Field(ge=1, strict=True)
    checkpoint_epoch: int = Field(ge=1, strict=True)
    binding_hash: Sha256Hex
    status: DriverThreadStatus
    latest_checkpoint_id: ShortText | None = None
    last_observed_sequence: int = Field(default=0, ge=0, strict=True)
    created_at: UtcDateTime
    updated_at: UtcDateTime


class DriverResult(ContractModel):
    """Bounded drive_once outcome."""

    run_id: OpaqueId
    thread_id: OpaqueId | None = None
    outcome: DriverOutcome
    next_action: NextActionKind | None = None
    wait_id: OpaqueId | None = None
    wait_reason: WaitReason | None = None
    operation_ref: OpaqueId | None = None
    recovery_case_ref: OpaqueId | None = None
    handoff_ref: OpaqueId | None = None
    observed_sequence: int | None = Field(default=None, ge=0, strict=True)
    error_code: DriverErrorCode | None = None
    message: ShortText | None = None


class IntegrationHandoff(ContractModel):
    """Immutable integration handoff after all required stages released (D11 end)."""

    handoff_id: OpaqueId
    run_id: OpaqueId
    plan_version: PlanVersion
    assignment_version: AssignmentVersion
    gate_policy_version: int = Field(ge=1, strict=True)
    execution_generation: int = Field(ge=1, strict=True)
    release_record_ids: list[OpaqueId] = Field(min_length=1, max_length=64)
    candidate_set_hashes: list[Sha256Hex] = Field(default_factory=list, max_length=64)
    contract_hash: Sha256Hex
    environment_fingerprint: Sha256Hex
    handoff_hash: Sha256Hex
    created_at: UtcDateTime


class RunControlRequest(ApiInputModel):
    """User run control intent. Does not claim stop verified or native paused."""

    action: Literal["pause", "resume", "cancel", "replan"]
    expected_status_version: ResourceRevision
    idempotency_key: ShortText
    reason: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="before")
    @classmethod
    def reject_trusted_control_flags(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        forbidden = {
            "stopped",
            "verified",
            "stopVerified",
            "stop_verified",
            "stopEvidenceRefs",
            "stop_evidence_refs",
            "nativeStopped",
            "native_stopped",
            "forcePass",
            "force_pass",
            "released",
        }
        for key in data:
            if key in forbidden or str(key).replace("_", "").lower() in {
                "stopverified",
                "forcepass",
            }:
                raise ValueError(
                    f"clients cannot declare trusted control facts ({key}); "
                    "platform quiescence owns stop verification"
                )
        return data


class RunWaitListResponse(ContractModel):
    items: list[RunWait] = Field(default_factory=list, max_length=100)


class DriverStatusResponse(ContractModel):
    """Safe public driver view — no lease token or signing material."""

    run_id: OpaqueId
    collaboration_state: CollaborationProgress
    thread: DriverThread | None = None
    last_observed_sequence: int = Field(default=0, ge=0, strict=True)
    open_wait_count: int = Field(default=0, ge=0, strict=True)
    pending_wake_count: int = Field(default=0, ge=0, strict=True)
    graph_version: ShortText | None = None
    binding_hash: Sha256Hex | None = None


class PlatformAdmission(ContractModel):
    environment_id: OpaqueId
    mode: Literal["open", "draining", "stopped"]
    version: int = Field(ge=1, strict=True)
    reason: ShortText | None = None
    updated_at: UtcDateTime


# Keep RunActionRequest alias for compatibility while enforcing version/idempotency.
class RunActionRequestV2(RunControlRequest):
    """Alias used by D11 API — same fields as RunControlRequest."""


def assert_graph_does_not_approve_plans() -> None:
    """Documentation hook for reviewers — graph nodes must not confirm plans."""
    return None
