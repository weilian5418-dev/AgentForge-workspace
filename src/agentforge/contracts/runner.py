"""Controlled Runner contracts (D05 / BE-07 + BE-21)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import OpaqueId, Sha256Hex, ShortText, UtcDateTime

RunnerProfileId = Literal[
    "prepare_dir",
    "read_snippet",
    "collect_candidates",
    "run_approved_check",
    "render",
    "close_task",
    "run_in_container",  # always blocked unless Docker evidenced
]

RunnerStatus = Literal[
    "issued",
    "running",
    "succeeded",
    "failed",
    "blocked",
    "expired",
    "needs_reconciliation",
]


class RunnerTicketIssueRequest(ApiInputModel):
    profile_id: RunnerProfileId
    workspace_id: OpaqueId
    workspace_root: ShortText = Field(
        description="Platform-resolved workspace absolute path; runner re-validates confinement."
    )
    node_id: OpaqueId = "local"
    environment_id: OpaqueId | None = None
    instance_id: OpaqueId | None = None
    session_ref: ShortText | None = None
    attempt_id: OpaqueId | None = None
    generation: int = Field(default=1, ge=1, strict=True)
    scope: ShortText
    params: dict[str, Any] = Field(default_factory=dict)
    expires_at: UtcDateTime
    approval_id: OpaqueId | None = None
    require_effective_approval: bool = Field(
        default=False,
        strict=True,
        description="When true, execute requires a native-effective D04 grant.",
    )


class RunnerTicket(ContractModel):
    operation_id: OpaqueId
    profile_id: ShortText
    workspace_id: OpaqueId
    node_id: OpaqueId
    attempt_id: OpaqueId | None = None
    generation: int = Field(ge=1, strict=True)
    action_digest: Sha256Hex
    scope: ShortText
    status: RunnerStatus
    expires_at: UtcDateTime
    approval_id: OpaqueId | None = None
    require_effective_approval: bool = Field(default=False, strict=True)
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    process_ref: ShortText | None = None
    message: ShortText | None = None


class RunnerExecuteRequest(ApiInputModel):
    operation_id: OpaqueId
    action_digest: Sha256Hex


class RunnerStopRequest(ApiInputModel):
    operation_id: OpaqueId
    reason: ShortText | None = None


class RunnerProfileInfo(ContractModel):
    profile_id: ShortText
    host_supported: bool = Field(strict=True)
    docker_required: bool = Field(strict=True)
    description: ShortText


class RunnerProfileListResponse(ContractModel):
    items: list[RunnerProfileInfo] = Field(default_factory=list, max_length=32)
