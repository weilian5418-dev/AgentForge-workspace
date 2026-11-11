"""Plan, routing, and approval contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agentforge.contracts.agents import RoleAssignment, RoleSpec
from agentforge.contracts.artifacts import WorkPackage
from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import (
    AssignmentVersion,
    OpaqueId,
    PlanStatus,
    PlanVersion,
    RoutingModeDecision,
    Sha256Hex,
    ShortText,
    UtcDateTime,
)
from agentforge.contracts.stages import Stage, StageGatePolicy

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


class RoutingDecision(ContractModel):
    """Suggestion only. Never authorizes execution by itself."""

    mode: RoutingModeDecision
    score: int | None = Field(default=None, ge=0, le=10, strict=True)
    confidence: float | None = Field(default=None, ge=0, le=1, strict=True)
    reason: ShortText
    rule_version: ShortText
    model_review_count: int = Field(ge=0, le=1, strict=True)


class PlanResponse(ContractModel):
    run_id: OpaqueId
    plan_version: PlanVersion
    contract_hash: Sha256Hex
    status: PlanStatus
    summary: str = Field(default="", max_length=4096)
    routing: RoutingDecision | None = None
    roles: list[RoleSpec] = Field(default_factory=list, max_length=32)
    stages: list[Stage] = Field(default_factory=list, max_length=32)
    gate_policy: StageGatePolicy | None = None
    work_packages: list[WorkPackage] = Field(default_factory=list, max_length=64)
    assignments: list[RoleAssignment] = Field(default_factory=list, max_length=32)
    created_at: UtcDateTime


class PlanProposalRequest(ApiInputModel):
    """Request a read-only plan proposal. Does not start development."""

    note: str | None = Field(default=None, max_length=2048)


class PlanRevisionRequest(ApiInputModel):
    """User-driven plan revision → new planVersion; re-validates deps and write scopes."""

    summary: str | None = Field(default=None, max_length=4096)
    role_ids_to_add: list[OpaqueId] = Field(default_factory=list, max_length=16)
    role_ids_to_remove: list[OpaqueId] = Field(default_factory=list, max_length=16)
    note: str | None = Field(default=None, max_length=2048)


class PlanDecisionRequest(ApiInputModel):
    """Confirm or reject a concrete plan + assignment + gate policy binding."""

    decision: Literal["approve", "reject"]
    plan_version: PlanVersion
    plan_hash: Sha256Hex
    assignment_version: AssignmentVersion
    gate_policy_version: int = Field(ge=1, strict=True)
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

