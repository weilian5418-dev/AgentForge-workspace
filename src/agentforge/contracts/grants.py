"""Directory grant and execution-environment contracts (BE-04.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import OpaqueId, ShortText, UtcDateTime

GrantOperation = Literal["read_metadata", "read_content", "baseline_inspect"]
GrantStatus = Literal["active", "revoked", "superseded"]
EnvironmentType = Literal["windows_native", "wsl", "container", "other"]
EnvironmentReachability = Literal["reachable", "unreachable", "unsupported", "unverified"]
DirtyPolicy = Literal[
    "committed_only",
    "controlled_dirty_snapshot",
    "user_must_clean",
]
BaselineStatus = Literal["ready", "partial", "blocked", "unstable"]
InputRegistrationStatus = Literal["staging", "available", "failed", "orphaned"]
BindingStatus = Literal["verified", "unverified", "unsupported", "expired", "revoked"]


class TrustPolicy(ContractModel):
    """Server-defined typed trust rules — callers cannot invent privilege escalations."""

    allow_follow_symlinks: bool = Field(default=False, strict=True)
    max_file_bytes: int = Field(default=52_428_800, ge=1, le=104_857_600, strict=True)
    max_total_read_bytes: int = Field(default=209_715_200, ge=1, le=1_073_741_824, strict=True)
    max_files: int = Field(default=10_000, ge=1, le=100_000, strict=True)
    max_depth: int = Field(default=32, ge=1, le=64, strict=True)
    allow_zero_byte_inputs: bool = Field(default=True, strict=True)


class ExecutionEnvironment(ContractModel):
    environment_id: OpaqueId
    type: EnvironmentType
    node_id: ShortText
    wsl_distro: ShortText | None = None
    connection_status: EnvironmentReachability
    last_checked_at: UtcDateTime | None = None


class DirectoryGrant(ContractModel):
    grant_id: OpaqueId
    authorized_user: ShortText
    environment_id: OpaqueId
    normalized_root: ShortText
    directory_identity: ShortText
    allowed_operations: list[GrantOperation] = Field(min_length=1, max_length=8)
    grant_version: int = Field(ge=1, strict=True)
    status: GrantStatus
    created_at: UtcDateTime
    revoked_at: UtcDateTime | None = None


class DirectoryGrantCreateRequest(ApiInputModel):
    """Local CLI only — never accept arbitrary host paths via public project create."""

    root_path: ShortText
    environment_id: OpaqueId | None = None
    display_label: str | None = Field(default=None, max_length=256)


class DirectoryGrantRevokeRequest(ApiInputModel):
    grant_id: OpaqueId
    note: str | None = Field(default=None, max_length=1024)


class EnvironmentPathBinding(ContractModel):
    binding_id: OpaqueId
    source_environment_id: OpaqueId
    target_environment_id: OpaqueId
    source_path: ShortText
    target_path: ShortText
    correspondence_evidence: ShortText
    binding_version: int = Field(ge=1, strict=True)
    status: BindingStatus
    checked_at: UtcDateTime
    expires_at: UtcDateTime | None = None


class ProjectBaseline(ContractModel):
    baseline_id: OpaqueId
    project_id: OpaqueId
    environment_id: OpaqueId
    dirty_policy: DirtyPolicy
    status: BaselineStatus
    is_git: bool = Field(strict=True)
    git_commit: ShortText | None = None
    git_dirty_summary: dict[str, int] = Field(default_factory=dict)
    manifest_hash: ShortText | None = None
    scope_strategy: ShortText
    checked_at: UtcDateTime
    notes: list[ShortText] = Field(default_factory=list, max_length=32)


class ProjectInputRecord(ContractModel):
    input_id: OpaqueId
    project_id: OpaqueId
    input_version: int = Field(ge=1, strict=True)
    original_name: ShortText
    size_bytes: int = Field(ge=0, strict=True)
    sha256: ShortText
    storage_ref: ShortText
    status: InputRegistrationStatus
    created_at: UtcDateTime
