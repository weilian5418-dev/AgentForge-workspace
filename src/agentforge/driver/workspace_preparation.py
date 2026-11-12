"""Engine-level workspace prepare: intent → Git I/O → claim result (D11.6)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.collaboration import LeaseFence
from agentforge.contracts.git_workspaces import PrepareWorktreeRequest
from agentforge.driver.binding import build_operation_key, sha256_hex
from agentforge.driver.fencing import assert_driver_fence
from agentforge.driver.repository import DriverRepository
from agentforge.driver.signals import record_wake_signal
from agentforge.git.service import GitWorkspaceError, GitWorkspaceService
from agentforge.storage.models import (
    DirectoryGrantRow,
    Outbox,
    Project,
    ProjectBaselineRow,
    Run,
    SchedulerOperationRow,
)
from agentforge.storage.uow import db_now, unit_of_work_connection


def _now(session: Session | None = None) -> datetime:
    if session is not None:
        try:
            value = db_now(session)
            if getattr(value, "tzinfo", None) is None:
                return value.replace(tzinfo=UTC)  # type: ignore[union-attr]
            return value  # type: ignore[return-value]
        except Exception:
            pass
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def expected_worktree_path(state_dir: Path, project_id: str, workspace_id: str) -> Path:
    return (state_dir / "git_worktrees" / project_id / workspace_id).resolve()


def ensure_workspace_operation(
    session: Session,
    fence: LeaseFence,
    *,
    role_id: str,
    strategy: str = "commit_only",
    dirty_snapshot_id: str | None = None,
    attempt_id: str | None = None,
    state_dir: Path,
) -> dict[str, Any]:
    """Phase 1: fenced intent with fixed workspace identity + outbox (no Git I/O)."""
    assert_driver_fence(session, fence, require_active_thread=fence.thread_id is not None)
    run = session.get(Run, fence.run_id, with_for_update=True)
    if run is None:
        raise ValueError(f"run {fence.run_id} missing")
    if run.baseline_id is None or run.plan_version is None or run.assignment_version is None:
        raise ValueError("run missing baseline/plan/assignment for workspace prepare")

    project = session.get(Project, run.project_id)
    if project is None:
        raise ValueError("project missing")
    grant = session.get(DirectoryGrantRow, project.directory_grant_id)
    if grant is None or grant.status != "active":
        raise ValueError("directory grant inactive")
    baseline = session.get(ProjectBaselineRow, run.baseline_id)
    if baseline is None:
        raise ValueError("baseline missing")

    generation = int(run.active_generation)
    op_key = build_operation_key(
        kind="workspace_prepare",
        run_id=run.id,
        plan_version=int(run.plan_version),
        assignment_version=int(run.assignment_version),
        generation=generation,
        work_package_id=role_id,
    )
    repo = DriverRepository(session)
    existing = repo.find_operation(run.id, op_key)
    if existing is not None:
        return {
            "operationId": existing.id,
            "operationKey": op_key,
            "status": existing.status,
            "resultRefs": list(existing.result_refs or []),
            "binding": existing.binding_json,
        }

    workspace_id = f"gwt_{sha256_hex({'run': run.id, 'role': role_id, 'g': generation})[:16]}"
    path = expected_worktree_path(state_dir, project.id, workspace_id)
    request_body = {
        "projectId": project.id,
        "baselineId": baseline.id,
        "strategy": strategy,
        "sourceRoot": grant.normalized_root,
        "directoryIdentity": grant.directory_identity,
        "dirtySnapshotId": dirty_snapshot_id,
        "attemptId": attempt_id,
        "roleId": role_id,
        "workspaceId": workspace_id,
        "worktreePath": str(path),
    }
    digest = sha256_hex(request_body)
    op_id = _id("sop")
    row = SchedulerOperationRow(
        id=op_id,
        run_id=run.id,
        thread_id=fence.thread_id,
        kind="workspace_prepare",
        operation_key=op_key,
        request_hash=digest,
        binding_json=request_body,
        status="waiting",
        result_refs=[],
        created_at=_now(session),
        updated_at=_now(session),
    )
    repo.add_operation(row)
    session.add(
        Outbox(
            id=_id("obx"),
            run_id=run.id,
            event_id=None,
            intent_type="workspace.prepare",
            target="workspace_coordinator",
            payload={
                "operationId": op_id,
                "runId": run.id,
                "workspaceId": workspace_id,
                "roleId": role_id,
                "taskVersion": 1,
            },
            idempotency_key=f"workspace.prepare:{op_id}",
            delivery_status="pending",
            attempt_count=0,
            created_at=_now(session),
            updated_at=_now(session),
        )
    )
    session.flush()
    return {
        "operationId": op_id,
        "operationKey": op_key,
        "status": "waiting",
        "resultRefs": [],
        "binding": request_body,
    }


def process_workspace_preparation(
    engine: Engine,
    payload: dict[str, Any],
    *,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Phase 2–3: load intent, Git outside long locks, then claim + wake."""
    from agentforge.core.config import Settings

    cfg = settings or Settings()
    operation_id = str(payload.get("operationId") or "")
    if not operation_id:
        raise ValueError("workspace.prepare missing operationId")

    with unit_of_work_connection(engine) as uow:
        op = uow.session.get(SchedulerOperationRow, operation_id)
        if op is None:
            raise ValueError(f"scheduler operation {operation_id} missing")
        if op.status == "succeeded" and op.result_refs:
            return {"status": "succeeded", "workspaceId": op.result_refs[0]}
        binding = dict(op.binding_json or {})
        body = PrepareWorktreeRequest.model_validate(
            {
                "projectId": binding["projectId"],
                "baselineId": binding["baselineId"],
                "strategy": binding["strategy"],
                "sourceRoot": binding["sourceRoot"],
                "directoryIdentity": binding["directoryIdentity"],
                "dirtySnapshotId": binding.get("dirtySnapshotId"),
                "attemptId": binding.get("attemptId"),
                "roleId": binding.get("roleId"),
            }
        )
        workspace_id = str(binding["workspaceId"])
        path = Path(str(binding["worktreePath"]))
        store = ArtifactStore(cfg.state_dir / "artifact_store")
        # Keep Git I/O inside this short UOW for reclaim correctness; coordinator
        # already finished the intent transaction before outbox dispatch.
        try:
            view = GitWorkspaceService(uow.session, store, cfg.state_dir).prepare_worktree(
                body,
                fixed_workspace_id=workspace_id,
                fixed_worktree_path=path,
            )
        except GitWorkspaceError as exc:
            op.status = "blocked"
            op.error_code = exc.code
            op.updated_at = _now(uow.session)
            record_wake_signal(
                uow.session,
                run_id=op.run_id,
                source_kind="control",
                source_ref=f"workspace.prepare.failed:{op.id}",
                binding={"operationId": op.id, "error": exc.code},
                priority=40,
            )
            return {"status": "blocked", "errorCode": exc.code, "message": exc.message}

        op.status = "succeeded"
        op.result_refs = [view.workspace_id]
        op.updated_at = _now(uow.session)
        record_wake_signal(
            uow.session,
            run_id=op.run_id,
            source_kind="control",
            source_ref=f"workspace.ready:{view.workspace_id}",
            binding={
                "operationId": op.id,
                "workspaceId": view.workspace_id,
                "roleId": binding.get("roleId"),
            },
            priority=50,
        )
        return {"status": "succeeded", "workspaceId": view.workspace_id}


def reconcile_existing_workspace(
    session: Session,
    *,
    workspace_id: str,
    expected_path: Path,
    expected_baseline_commit: str | None,
) -> dict[str, Any]:
    """Read-only identity check used by recovery — does not delete directories."""
    from agentforge.storage.models import GitWorkspaceRow

    row = session.get(GitWorkspaceRow, workspace_id)
    path_exists = expected_path.exists()
    if row is None and not path_exists:
        return {"status": "missing", "workspaceId": workspace_id}
    if row is None and path_exists:
        return {
            "status": "disk_without_row",
            "workspaceId": workspace_id,
            "path": str(expected_path),
            "action": "reclaim_or_recovery_case",
        }
    assert row is not None
    if Path(row.worktree_path).resolve() != expected_path.resolve():
        return {
            "status": "path_mismatch",
            "workspaceId": workspace_id,
            "recordedPath": row.worktree_path,
            "expectedPath": str(expected_path),
        }
    if (
        expected_baseline_commit
        and row.head_commit
        and row.head_commit != expected_baseline_commit
    ):
        return {
            "status": "head_mismatch",
            "workspaceId": workspace_id,
            "head": row.head_commit,
            "expected": expected_baseline_commit,
        }
    return {"status": "matched", "workspaceId": workspace_id, "path": row.worktree_path}
