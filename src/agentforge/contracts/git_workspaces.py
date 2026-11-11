"""D07 git worktree / snapshot / integration contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import OpaqueId, RelativePath, Sha256Hex, ShortText, UtcDateTime

BaselineStrategy = Literal["commit_only", "managed_dirty_snapshot", "blocked"]
WorkspaceStatus = Literal[
    "active",
    "blocked",
    "restricted",
    "snapshot_frozen",
    "conflicted",
    "closed",
]
IntegrationStatus = Literal[
    "pending",
    "merging",
    "conflicted",
    "ready",
    "failed",
]


class CaptureDirtySnapshotRequest(ApiInputModel):
    project_id: OpaqueId
    baseline_id: OpaqueId
    source_root: ShortText
    selected_paths: list[RelativePath] = Field(min_length=1, max_length=500)


class DirtySnapshotView(ContractModel):
    snapshot_id: OpaqueId
    project_id: OpaqueId
    baseline_id: OpaqueId
    content_hash: Sha256Hex
    manifest_hash: Sha256Hex
    path_count: int = Field(ge=0, strict=True)
    created_at: UtcDateTime


class PrepareWorktreeRequest(ApiInputModel):
    project_id: OpaqueId
    baseline_id: OpaqueId
    strategy: BaselineStrategy
    source_root: ShortText
    directory_identity: ShortText
    dirty_snapshot_id: OpaqueId | None = None
    attempt_id: OpaqueId | None = None
    role_id: OpaqueId | None = None


class WorktreeView(ContractModel):
    workspace_id: OpaqueId
    project_id: OpaqueId
    baseline_id: OpaqueId
    strategy: BaselineStrategy
    status: WorkspaceStatus
    worktree_path: ShortText
    head_commit: ShortText | None = None
    restrictions: list[ShortText] = Field(default_factory=list, max_length=32)
    dirty_included: bool = Field(strict=True)
    message: ShortText | None = None
    created_at: UtcDateTime


class FreezeWorkspaceSnapshotRequest(ApiInputModel):
    workspace_id: OpaqueId
    stop_operation_id: OpaqueId = Field(
        description="Trusted Runner operation that must be terminal for this workspace"
    )


class WorkspaceSnapshotView(ContractModel):
    snapshot_id: OpaqueId
    workspace_id: OpaqueId
    content_hash: Sha256Hex
    manifest_hash: Sha256Hex
    head_commit: ShortText | None = None
    created_at: UtcDateTime
    note: ShortText = "Recoverable via D06 store; audit must not use mutable worktree"


class CandidateSnapshotRef(ApiInputModel):
    """Locks a specific frozen snapshot — integrate must not follow 'latest'."""

    workspace_id: OpaqueId
    snapshot_id: OpaqueId


class IntegrateRequest(ApiInputModel):
    project_id: OpaqueId
    baseline_id: OpaqueId
    source_root: ShortText
    directory_identity: ShortText
    candidate_snapshots: list[CandidateSnapshotRef] = Field(
        min_length=1,
        max_length=16,
        description="Ordered locked snapshots; merge uses these ids only",
    )
    candidate_workspace_ids: list[OpaqueId] | None = Field(
        default=None,
        max_length=16,
        description="Deprecated alias; prefer candidateSnapshots",
    )
    merge_order: list[OpaqueId] | None = Field(
        default=None,
        max_length=16,
        description="Optional workspace id order; must match candidateSnapshots set",
    )


class IntegrationView(ContractModel):
    integration_id: OpaqueId
    project_id: OpaqueId
    baseline_id: OpaqueId
    status: IntegrationStatus
    worktree_path: ShortText | None = None
    conflict_paths: list[ShortText] = Field(default_factory=list, max_length=500)
    content_hash: Sha256Hex | None = None
    manifest_hash: Sha256Hex | None = None
    input_workspace_ids: list[OpaqueId] = Field(default_factory=list, max_length=16)
    message: ShortText | None = None
    stage_released: bool = Field(
        default=False,
        strict=True,
        description="Always false until D10",
    )
    created_at: UtcDateTime


class CleanupWorktreeRequest(ApiInputModel):
    workspace_id: OpaqueId
    force: bool = Field(
        default=False,
        strict=True,
        description="Still refuses if active refs/processes; never deletes user source root",
    )
