"""Artifact bus API (D06) — publish ≠ stage release."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import Engine
from starlette.exceptions import HTTPException

from agentforge.artifacts.service import ArtifactBusError, ArtifactService
from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.artifact_bus import (
    CandidateView,
    ConsumeCheckResponse,
    FreezeCandidateRequest,
    GcRequest,
    GcResult,
    PublicationView,
    PublishRequest,
    ValidationReportView,
    VerifyCandidateRequest,
)
from agentforge.core.config import Settings
from agentforge.runtime.control import admits_new_work, read_admission
from agentforge.storage.uow import unit_of_work_connection

router = APIRouter(prefix="/api/v1", tags=["artifacts"])


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _engine(request: Request, settings: Settings) -> Engine:
    if settings.database_url is None or request.app.state.database is None:
        raise HTTPException(503, "Database is not configured")
    return cast(Engine, request.app.state.database)


def _store(settings: Settings) -> ArtifactStore:
    return ArtifactStore(Path(settings.state_dir) / "artifact_store")


def _map(exc: ArtifactBusError) -> HTTPException:
    status = {
        "candidate_not_found": 404,
        "validator_not_found": 404,
        "idempotency_conflict": 409,
        "not_verified": 409,
        "snapshot_invalid": 409,
        "report_mismatch": 409,
        "untrusted_stop_source": 403,
        "stop_not_terminal": 409,
        "generation_mismatch": 409,
        "stop_workspace_mismatch": 409,
        "workspace_still_writable": 409,
        "writes_not_quiesced": 409,
        "attempt_mismatch": 409,
        "stop_evidence_mismatch": 409,
        "stop_evidence_missing": 400,
    }.get(exc.code, 400)
    return HTTPException(status, f"{exc.code}: {exc.message}")


@router.post("/artifacts/candidates/freeze", response_model=CandidateView)
def freeze_candidate(
    request: Request,
    body: FreezeCandidateRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> CandidateView:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ArtifactService(uow.session, _store(settings)).freeze_candidate(body)
    except ArtifactBusError as exc:
        raise _map(exc) from exc


@router.post("/artifacts/candidates/verify", response_model=ValidationReportView)
def verify_candidate(
    request: Request,
    body: VerifyCandidateRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> ValidationReportView:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ArtifactService(uow.session, _store(settings)).verify(body)
    except ArtifactBusError as exc:
        raise _map(exc) from exc


@router.post("/artifacts/publish", response_model=PublicationView)
def publish(
    request: Request,
    body: PublishRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> PublicationView:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ArtifactService(uow.session, _store(settings)).publish(body)
    except ArtifactBusError as exc:
        raise _map(exc) from exc


@router.get("/artifacts/candidates/{candidate_id}", response_model=CandidateView)
def get_candidate(
    request: Request,
    candidate_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> CandidateView:
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ArtifactService(uow.session, _store(settings)).get_candidate(candidate_id)
    except ArtifactBusError as exc:
        raise _map(exc) from exc


@router.get("/artifacts/candidates/{candidate_id}/content")
def view_candidate_content(
    request: Request,
    candidate_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> Response:
    """Viewable bytes — does not authorize downstream consumption."""
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            data = ArtifactService(uow.session, _store(settings)).read_content_for_view(
                candidate_id
            )
    except ArtifactBusError as exc:
        raise _map(exc) from exc
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment",
            "X-AgentForge-Consumable": "false",
        },
    )


@router.get(
    "/artifacts/{artifact_id}/versions/{version}/consume-check",
    response_model=ConsumeCheckResponse,
)
def consume_check(
    request: Request,
    artifact_id: str,
    version: int,
    settings: Annotated[Settings, Depends(_settings)],
) -> ConsumeCheckResponse:
    engine = _engine(request, settings)
    with unit_of_work_connection(engine) as uow:
        return ArtifactService(uow.session, _store(settings)).can_consume(artifact_id, version)


@router.post("/artifacts/gc", response_model=GcResult)
def run_gc(
    request: Request,
    body: GcRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> GcResult:
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ArtifactService(uow.session, _store(settings)).gc(body)
    except ArtifactBusError as exc:
        raise _map(exc) from exc
