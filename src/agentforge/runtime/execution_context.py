"""ExecutionContext — session ↔ workspace binding for Adapter Host / recovery.

Session recovery success does not imply file recovery; those checks stay separate.
Trusted services resolve real paths and pass a complete context to adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class ExecutionContextError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ExecutionContext:
    """Complete bind for one executable operation — not a bag of optional ids."""

    profile_id: str
    operation_id: str
    instance_id: str
    workspace_id: str
    workspace_root: str
    environment_id: str
    generation: int
    attempt_id: str | None = None
    session_ref: str | None = None
    assignment_version: int | None = None
    baseline_id: str | None = None
    permission_scope_ref: str | None = None
    budget_ref: str | None = None

    def as_adapter_payload(self) -> dict[str, str | int | None]:
        return {
            "profileId": self.profile_id,
            "operationId": self.operation_id,
            "instanceId": self.instance_id,
            "workspaceId": self.workspace_id,
            "workspaceRoot": self.workspace_root,
            "environmentId": self.environment_id,
            "generation": self.generation,
            "attemptId": self.attempt_id,
            "sessionRef": self.session_ref,
            "assignmentVersion": self.assignment_version,
            "baselineId": self.baseline_id,
            "permissionScopeRef": self.permission_scope_ref,
            "budgetRef": self.budget_ref,
        }


RecoveryFacet = Literal["session", "workspace", "files"]


@dataclass(frozen=True)
class RecoveryCheck:
    facet: RecoveryFacet
    ok: bool
    code: str
    detail: str


def bind_execution_context(
    *,
    profile_id: str,
    operation_id: str,
    instance_id: str,
    workspace_id: str,
    workspace_root: str,
    environment_id: str,
    generation: int,
    attempt_id: str | None = None,
    session_ref: str | None = None,
    assignment_version: int | None = None,
    baseline_id: str | None = None,
    permission_scope_ref: str | None = None,
    budget_ref: str | None = None,
    expected_workspace_id: str | None = None,
    expected_environment_id: str | None = None,
    expected_attempt_id: str | None = None,
) -> ExecutionContext:
    """Build a context after verifying ids belong to one allocation."""
    if not profile_id.strip():
        raise ExecutionContextError("missing_profile", "profileId is required")
    if not operation_id.strip():
        raise ExecutionContextError("missing_operation", "operationId is required")
    if not instance_id.strip():
        raise ExecutionContextError("missing_instance", "instanceId is required")
    if not workspace_id.strip():
        raise ExecutionContextError("missing_workspace", "workspaceId is required")
    if not environment_id.strip():
        raise ExecutionContextError("missing_environment", "environmentId is required")
    if generation < 1:
        raise ExecutionContextError("invalid_generation", "generation must be >= 1")

    if expected_workspace_id is not None and expected_workspace_id != workspace_id:
        raise ExecutionContextError(
            "workspace_mismatch",
            "workspaceId does not match the bound allocation",
        )
    if expected_environment_id is not None and expected_environment_id != environment_id:
        raise ExecutionContextError(
            "environment_mismatch",
            "environmentId does not match the bound allocation",
        )
    if expected_attempt_id is not None and attempt_id is not None and expected_attempt_id != attempt_id:
        raise ExecutionContextError(
            "attempt_mismatch",
            "attemptId does not match the bound allocation",
        )

    try:
        resolved = str(Path(workspace_root).resolve())
    except OSError as exc:
        raise ExecutionContextError("invalid_workspace_root", "workspaceRoot could not be resolved") from exc
    if not resolved:
        raise ExecutionContextError("invalid_workspace_root", "workspaceRoot is empty")

    return ExecutionContext(
        profile_id=profile_id,
        operation_id=operation_id,
        instance_id=instance_id,
        workspace_id=workspace_id,
        workspace_root=resolved,
        environment_id=environment_id,
        generation=generation,
        attempt_id=attempt_id,
        session_ref=session_ref,
        assignment_version=assignment_version,
        baseline_id=baseline_id,
        permission_scope_ref=permission_scope_ref,
        budget_ref=budget_ref,
    )


def assert_context_complete(ctx: ExecutionContext, *, require_session: bool = False) -> None:
    """Adapter Host gate: non-null ids are insufficient — context must be coherent."""
    if not ctx.profile_id or not ctx.operation_id or not ctx.instance_id or not ctx.workspace_id:
        raise ExecutionContextError("incomplete_context", "ExecutionContext missing required ids")
    if not ctx.environment_id:
        raise ExecutionContextError("incomplete_context", "environmentId required on ExecutionContext")
    if require_session and not ctx.session_ref:
        raise ExecutionContextError("session_required", "sessionRef required for this operation")


def verify_recovery(
    ctx: ExecutionContext,
    *,
    native_session_ok: bool | None = None,
    workspace_identity_ok: bool | None = None,
    file_version_ok: bool | None = None,
) -> list[RecoveryCheck]:
    """Separately report session / workspace / file recovery — never collapse into one bool."""
    checks: list[RecoveryCheck] = []
    if native_session_ok is not None:
        checks.append(
            RecoveryCheck(
                facet="session",
                ok=native_session_ok,
                code="session_ok" if native_session_ok else "session_unverified",
                detail=(
                    f"Native session {ctx.session_ref or '(none)'} verified"
                    if native_session_ok
                    else "Native session not verified; do not treat files as recovered"
                ),
            )
        )
    if workspace_identity_ok is not None:
        checks.append(
            RecoveryCheck(
                facet="workspace",
                ok=workspace_identity_ok,
                code="workspace_ok" if workspace_identity_ok else "workspace_unverified",
                detail=(
                    f"Workspace {ctx.workspace_id} identity matches allocation"
                    if workspace_identity_ok
                    else "Workspace identity mismatch or unverified"
                ),
            )
        )
    if file_version_ok is not None:
        checks.append(
            RecoveryCheck(
                facet="files",
                ok=file_version_ok,
                code="files_ok" if file_version_ok else "files_unverified",
                detail=(
                    "File versions match expected baseline/snapshot"
                    if file_version_ok
                    else "File versions unverified; session ok does not imply file recovery"
                ),
            )
        )
    return checks


def recovery_all_ok(checks: list[RecoveryCheck]) -> bool:
    return bool(checks) and all(c.ok for c in checks)
