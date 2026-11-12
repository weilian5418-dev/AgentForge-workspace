"""Project registration, query, and input upload API (BE-04)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from starlette.exceptions import HTTPException

from agentforge.contracts.grants import ProjectInputRecord, TrustPolicy
from agentforge.contracts.projects import (
    ProjectCreateRequest,
    ProjectInputListResponse,
    ProjectInputResponse,
    ProjectResponse,
)
from agentforge.core.config import Settings
from agentforge.projects.grants import GrantError
from agentforge.projects.inputs import InputError, register_streamed_input
from agentforge.projects.service import ProjectError, ProjectService
from agentforge.runtime.control import admits_new_work, read_admission
from agentforge.storage.models import ProjectInputRow
from agentforge.storage.uow import unit_of_work_connection

router = APIRouter(prefix="/api/v1", tags=["projects"])


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _map_error(exc: ProjectError | GrantError | InputError) -> HTTPException:
    status = {
        "grant_not_found": 404,
        "project_not_found": 404,
        "grant_revoked": 403,
        "grant_inactive": 403,
        "directory_replaced": 409,
        "duplicate_content": 409,
        "hash_mismatch": 422,
        "file_too_large": 413,
        "project_quota": 413,
        "empty_file_denied": 422,
        "invalid_file_name": 422,
    }.get(exc.code, 400)
    return HTTPException(status, f"{exc.code}: {exc.message}")


def _require_engine(request: Request, settings: Settings) -> Any:
    if settings.database_url is None or request.app.state.database is None:
        raise HTTPException(503, "Database is not configured")
    return request.app.state.database


@router.post("/projects", response_model=ProjectResponse)
def create_project(
    request: Request,
    body: ProjectCreateRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> ProjectResponse:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ProjectService(uow.session, settings).register(body)
    except ProjectError as exc:
        raise _map_error(exc) from exc


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(
    request: Request,
    project_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> ProjectResponse:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ProjectService(uow.session, settings).get(project_id, include_precheck=True)
    except ProjectError as exc:
        raise _map_error(exc) from exc


@router.post("/projects/{project_id}/inputs", response_model=ProjectInputResponse)
async def upload_input(
    request: Request,
    project_id: str,
    settings: Annotated[Settings, Depends(_settings)],
    file: Annotated[UploadFile, File()],
    file_name: str | None = Form(default=None),
    client_sha256: str | None = Form(default=None),
) -> ProjectInputResponse:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _require_engine(request, settings)
    display_name = file_name or (file.filename or "upload.bin")

    # Spool to a temp file under state dir while hashing — do not trust Content-Length.
    spool_dir = settings.state_dir.resolve() / "upload-spool"
    spool_dir.mkdir(parents=True, exist_ok=True)
    spool_path = spool_dir / f"spool_{project_id}_{id(file)}.part"
    digest = hashlib.sha256()
    size = 0
    max_bytes = TrustPolicy().max_file_bytes

    try:
        with spool_path.open("wb") as handle:
            while True:
                piece = await file.read(1024 * 1024)
                if not piece:
                    break
                size += len(piece)
                if size > max_bytes:
                    raise InputError("file_too_large", "Upload exceeds max_file_bytes")
                digest.update(piece)
                handle.write(piece)

        def replay() -> Iterator[bytes]:
            with spool_path.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk

        with unit_of_work_connection(engine) as uow:
            service = ProjectService(uow.session, settings)
            project = service.assert_grant_allows_work(project_id)
            trust = TrustPolicy.model_validate(project.trust_policy or {})
            if size > trust.max_file_bytes:
                raise InputError("file_too_large", "Upload exceeds max_file_bytes")
            return register_streamed_input(
                uow.session,
                project_id=project_id,
                file_name=display_name,
                chunks=replay(),
                staging_root=service.input_staging_dir(project_id),
                trust_policy=trust,
                content_type=file.content_type,
                client_sha256=client_sha256,
            )
    except (ProjectError, InputError) as exc:
        raise _map_error(exc) from exc
    finally:
        await file.close()
        Path(spool_path).unlink(missing_ok=True)


@router.get("/projects/{project_id}/inputs", response_model=ProjectInputListResponse)
def list_inputs(
    request: Request,
    project_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> ProjectInputListResponse:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            rows = ProjectService(uow.session, settings).list_available_inputs(project_id)
            return ProjectInputListResponse(items=[_input_record(row) for row in rows])
    except ProjectError as exc:
        raise _map_error(exc) from exc


def _input_record(row: ProjectInputRow) -> ProjectInputRecord:
    return ProjectInputRecord(
        input_id=row.id,
        project_id=row.project_id,
        input_version=row.input_version,
        original_name=row.original_name,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        storage_ref=row.storage_ref,
        status=row.status,  # type: ignore[arg-type]
        created_at=row.created_at,
    )
