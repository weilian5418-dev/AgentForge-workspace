"""BE-04 contract shape: typed trust policy, grant-only project create."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentforge.contracts.grants import DirectoryGrantCreateRequest, TrustPolicy
from agentforge.contracts.projects import ProjectCreateRequest, ProjectInputCreateRequest


def test_project_create_rejects_client_workspace_fields() -> None:
    with pytest.raises(ValidationError):
        ProjectCreateRequest.model_validate(
            {
                "directoryGrantId": "grant_abc",
                "rootPath": "D:/code",
                "workspaceRoot": "D:/ws",
                "trustPolicy": {"allowShell": True},
            }
        )


def test_project_create_accepts_grant_only() -> None:
    req = ProjectCreateRequest.model_validate(
        {"directoryGrantId": "grant_abc", "dirtyPolicy": "committed_only"}
    )
    assert req.directory_grant_id == "grant_abc"
    dumped = req.model_dump(by_alias=True)
    assert "rootPath" not in dumped
    assert "workspaceRoot" not in dumped


def test_trust_policy_is_typed_not_free_dict() -> None:
    policy = TrustPolicy()
    assert policy.allow_follow_symlinks is False
    with pytest.raises(ValidationError):
        TrustPolicy.model_validate({"allow_follow_symlinks": "yes"})


def test_grant_create_requires_root() -> None:
    with pytest.raises(ValidationError):
        DirectoryGrantCreateRequest.model_validate({})


def test_input_allows_zero_byte_declaration() -> None:
    req = ProjectInputCreateRequest.model_validate(
        {"fileName": "empty.txt", "declaredSizeBytes": 0}
    )
    assert req.declared_size_bytes == 0
