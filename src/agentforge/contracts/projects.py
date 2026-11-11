"""Project registration, baseline, and input contracts (BE-04)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import (
    OpaqueId,
    ResourceRevision,
    Sha256Hex,
    ShortText,
    UtcDateTime,
)
from agentforge.contracts.grants import (
    DirtyPolicy,
    ProjectBaseline,
    ProjectInputRecord,
    TrustPolicy,
)


class ProjectCreateRequest(ApiInputModel):
    """Consumes a valid DirectoryGrant — does not mint grants from client paths."""

    directory_grant_id: OpaqueId
    display_name: str | None = Field(default=None, max_length=256)
    dirty_policy: DirtyPolicy = "committed_only"
    # Optional client hint only; server resolves workspace under platform state.
    note: str | None = Field(default=None, max_length=1024)


class ProjectPrecheckSummary(ContractModel):
    accessible: bool = Field(strict=True)
    is_git: bool = Field(strict=True)
    git_commit: ShortText | None = None
    dirty: bool = Field(strict=True)
    dirty_policy: DirtyPolicy
    build_manifests: list[ShortText] = Field(default_factory=list, max_length=32)
    blockers: list[ShortText] = Field(default_factory=list, max_length=32)
    workspace_hints: list[ShortText] = Field(default_factory=list, max_length=32)
    status: Literal["ready", "partial", "blocked", "unstable"]


class ProjectResponse(ContractModel):
    id: OpaqueId
    directory_grant_id: OpaqueId
    display_name: str | None = None
    root_path: ShortText
    workspace_root: ShortText
    trust_policy: TrustPolicy
    revision: ResourceRevision
    current_baseline_id: OpaqueId | None = None
    grant_status: ShortText
    environment_id: OpaqueId
    precheck: ProjectPrecheckSummary | None = None
    baseline: ProjectBaseline | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class ProjectInputCreateRequest(ApiInputModel):
    """Metadata accompanying multipart upload (stream is the authority)."""

    file_name: ShortText
    content_type: ShortText | None = None
    client_sha256: Sha256Hex | None = None
    # Declared size is a hint only; server counts bytes while streaming.
    declared_size_bytes: int | None = Field(default=None, ge=0, le=52_428_800, strict=True)


class ProjectInputResponse(ContractModel):
    id: OpaqueId
    project_id: OpaqueId
    input_version: int = Field(ge=1, strict=True)
    file_name: ShortText
    content_type: ShortText | None = None
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0, strict=True)
    status: ShortText
    storage_ref: ShortText
    created_at: UtcDateTime


class ProjectInputListResponse(ContractModel):
    items: list[ProjectInputRecord] = Field(default_factory=list, max_length=200)
