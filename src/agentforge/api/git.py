"""Git worktree / snapshot / integration API (D07)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.git_workspaces import (
    CaptureDirtySnapshotRequest,
    CleanupWorktreeRequest,
    DirtySnapshotView,
    FreezeWorkspaceSnapshotRequest,
    IntegrateRequest,
    IntegrationView,
    PrepareWorktreeRequest,
    WorkspaceSnapshotView,
    WorktreeView,
)
from agentforge.core.config import Settings
from agentforge.git.service import GitWorkspaceError, GitWorkspaceService
from agentforge.runtime.control import admits_new_work, read_admission
from agentforge.storage.uow import unit_of_work_connection

router = APIRouter(prefix="/api/v1", tags=["git"])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _engine(request: Request, settings: Settings):
    if settings.database_url is None or request.app.state.database is None:
        raise HTTPException(503, "Database is not configured")
    return request.app.state.database


def _store(settings: Settings) -> ArtifactStore:
    return ArtifactStore(Path(settings.state_dir) / "artifact_store")


def _svc(request: Request, settings: Settings) -> tuple:
    return _engine(request, settings), _store(settings), Path(settings.state_dir)


def _map(exc: GitWorkspaceError) -> HTTPException:
    status = {
        "project_not_found": 404,
        "baseline_not_found": 404,
        "workspace_not_found": 404,
        "dirty_snapshot_invalid": 404,
        "candidate_not_frozen": 409,
        "idempotency_conflict": 409,
        "invalid_state": 409,
        "refs_active": 409,
        "baseline_blocked": 409,
        "directory_identity_mismatch": 403,
        "source_not_authorized": 403,
        "grant_inactive": 403,
        "refuse_user_root": 403,
    }.get(exc.code, 400)
    return HTTPException(status, f"{exc.code}: {exc.message}")


@router.post("/git/dirty-snapshots", response_model=DirtySnapshotView)
def capture_dirty_snapshot(
    request: Request,
    body: CaptureDirtySnapshotRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> DirtySnapshotView:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine, store, state_dir = _svc(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return GitWorkspaceService(uow.session, store, state_dir).capture_dirty(body)
    except GitWorkspaceError as exc:
        raise _map(exc) from exc


@router.post("/git/worktrees/prepare", response_model=WorktreeView)
def prepare_worktree(
    request: Request,
    body: PrepareWorktreeRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> WorktreeView:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine, store, state_dir = _svc(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return GitWorkspaceService(uow.session, store, state_dir).prepare_worktree(body)
    except GitWorkspaceError as exc:
        raise _map(exc) from exc


@router.post("/git/worktrees/freeze-snapshot", response_model=WorkspaceSnapshotView)
def freeze_workspace_snapshot(
    request: Request,
    body: FreezeWorkspaceSnapshotRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> WorkspaceSnapshotView:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine, store, state_dir = _svc(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return GitWorkspaceService(uow.session, store, state_dir).freeze_snapshot(body)
    except GitWorkspaceError as exc:
        raise _map(exc) from exc


@router.post("/git/integrations", response_model=IntegrationView)
def integrate_workspaces(
    request: Request,
    body: IntegrateRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> IntegrationView:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine, store, state_dir = _svc(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return GitWorkspaceService(uow.session, store, state_dir).integrate(body)
    except GitWorkspaceError as exc:
        raise _map(exc) from exc


@router.post("/git/worktrees/cleanup", response_model=WorktreeView)
def cleanup_worktree(
    request: Request,
    body: CleanupWorktreeRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> WorktreeView:
    engine, store, state_dir = _svc(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return GitWorkspaceService(uow.session, store, state_dir).cleanup(body)
    except GitWorkspaceError as exc:
        raise _map(exc) from exc
