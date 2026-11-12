"""Controlled Runner API (D05)."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from sqlalchemy import Engine
from starlette.exceptions import HTTPException

from agentforge.contracts.runner import (
    RunnerExecuteRequest,
    RunnerProfileInfo,
    RunnerProfileListResponse,
    RunnerStopRequest,
    RunnerTicket,
    RunnerTicketIssueRequest,
)
from agentforge.core.config import Settings
from agentforge.runner.executor import execute_runner
from agentforge.runner.profiles import list_profiles
from agentforge.runner.service import RunnerError, RunnerService
from agentforge.runtime.control import admits_new_work, read_admission
from agentforge.storage.uow import unit_of_work_connection

router = APIRouter(prefix="/api/v1", tags=["runner"])


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _engine(request: Request, settings: Settings) -> Engine:
    if settings.database_url is None or request.app.state.database is None:
        raise HTTPException(503, "Database is not configured")
    return cast(Engine, request.app.state.database)


def _map(exc: RunnerError) -> HTTPException:
    status = {
        "operation_not_found": 404,
        "unknown_profile": 404,
        "action_digest_mismatch": 409,
        "expired": 409,
        "already_expired": 400,
        "needs_reconciliation": 409,
        "approval_required": 403,
        "approval_not_effective": 403,
        "workspace_missing": 400,
        "path_escape": 400,
        "symlink_escape": 400,
    }.get(exc.code, 400)
    return HTTPException(status, f"{exc.code}: {exc.message}")


@router.get("/runner/profiles", response_model=RunnerProfileListResponse)
def get_profiles() -> RunnerProfileListResponse:
    items = [
        RunnerProfileInfo(
            profile_id=p.profile_id,
            host_supported=p.host_supported,
            docker_required=p.docker_required,
            description=p.description,
        )
        for p in list_profiles()
    ]
    return RunnerProfileListResponse(items=items)


@router.post("/runner/tickets", response_model=RunnerTicket)
def issue_ticket(
    request: Request,
    body: RunnerTicketIssueRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> RunnerTicket:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return RunnerService(uow.session).issue(body)
    except RunnerError as exc:
        raise _map(exc) from exc


@router.get("/runner/operations/{operation_id}", response_model=RunnerTicket)
def get_operation(
    request: Request,
    operation_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> RunnerTicket:
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return RunnerService(uow.session).get(operation_id)
    except RunnerError as exc:
        raise _map(exc) from exc


@router.post("/runner/execute", response_model=RunnerTicket)
def execute(
    request: Request,
    body: RunnerExecuteRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> RunnerTicket:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _engine(request, settings)
    try:
        return execute_runner(engine, body)
    except RunnerError as exc:
        raise _map(exc) from exc


@router.post("/runner/operations/{operation_id}/stop-evidence", response_model=RunnerTicket)
def stop_evidence(
    request: Request,
    operation_id: str,
    body: RunnerStopRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> RunnerTicket:
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return RunnerService(uow.session).record_stop_evidence(operation_id, reason=body.reason)
    except RunnerError as exc:
        raise _map(exc) from exc
