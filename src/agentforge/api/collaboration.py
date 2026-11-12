"""Collaboration control / waits / recovery / driver status API (D11.11)."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, Response
from starlette.exceptions import HTTPException

from agentforge.contracts.collaboration import (
    DriverStatusResponse,
    RunWaitListResponse,
)
from pydantic import Field

from agentforge.contracts.base import ApiInputModel
from agentforge.contracts.common import ShortText
from agentforge.contracts.recovery import (
    RecoveryCaseListResponse,
    RecoveryDecisionRequest,
    RecoveryReport,
)
from agentforge.contracts.runs import OperationAccepted, RunActionRequest
from agentforge.core.config import Settings
from agentforge.driver.control import RunControlError, RunControlService
from agentforge.driver.queries import get_driver_status, list_run_waits
from agentforge.queue.operations import OperationError, get_operation
from agentforge.recovery.errors import RecoveryError
from agentforge.recovery.service import RecoveryService
from agentforge.storage.database import create_database_engine
from agentforge.storage.uow import unit_of_work_connection

router = APIRouter(prefix="/api/v1", tags=["collaboration"])


class RecoveryCheckRequest(ApiInputModel):
    idempotency_key: ShortText
    reason: str | None = Field(default=None, max_length=1024)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _require_engine(request: Request, settings: Settings) -> Any:
    engine = getattr(request.app.state, "database", None) or create_database_engine(settings)
    if engine is None:
        raise HTTPException(503, "database_unavailable: Database is not configured")
    return engine


def _map_control(exc: RunControlError) -> HTTPException:
    status = {
        "run_not_found": 404,
        "status_version_conflict": 409,
        "idempotency_conflict": 409,
        "illegal_status": 409,
        "pending_side_effects": 409,
        "case_not_found": 404,
        "recovery_evidence_stale": 409,
        "unsupported_action": 400,
    }.get(exc.code, 400)
    return HTTPException(status, f"{exc.code}: {exc.message}")


def _map_recovery(exc: RecoveryError) -> HTTPException:
    status = {
        "run_not_found": 404,
        "case_not_found": 404,
        "status_version_conflict": 409,
        "idempotency_conflict": 409,
        "recovery_evidence_stale": 409,
        "context_changed": 409,
        "illegal_status": 409,
        "handoff_confirmation_required": 409,
        "decision_mismatch": 409,
    }.get(exc.code, 400)
    return HTTPException(status, f"{exc.code}: {exc.message}")


@router.post(
    "/runs/{run_id}/actions",
    response_model=OperationAccepted,
    status_code=202,
)
def run_action(
    request: Request,
    run_id: str,
    body: RunActionRequest,
    settings: Annotated[Settings, Depends(_settings)],
    response: Response,
) -> OperationAccepted:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            accepted = RunControlService(uow.session).request_action(
                run_id, body, actor="user:api"
            )
    except RunControlError as exc:
        raise _map_control(exc) from exc
    response.status_code = 202
    return accepted


@router.get("/runs/{run_id}/waits", response_model=RunWaitListResponse)
def get_run_waits(
    request: Request,
    run_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> RunWaitListResponse:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return list_run_waits(uow.session, run_id)
    except RunControlError as exc:
        raise _map_control(exc) from exc


@router.get("/runs/{run_id}/driver", response_model=DriverStatusResponse)
def get_run_driver(
    request: Request,
    run_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> DriverStatusResponse:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return get_driver_status(uow.session, run_id)
    except RunControlError as exc:
        raise _map_control(exc) from exc


@router.post(
    "/runs/{run_id}/recovery-checks",
    response_model=OperationAccepted,
    status_code=202,
)
def open_recovery_check(
    request: Request,
    run_id: str,
    body: RecoveryCheckRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> OperationAccepted:
    """Open a recovery case for evidence collection — does not invent stop."""
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            case = RecoveryService(uow.session).open_case(
                run_id,
                reason=body.reason or "api_recovery_check",
                request_operation_id=body.idempotency_key,
                emit_outbox=True,
            )
            return OperationAccepted(
                operation_id=case.request_operation_id or case.case_id,
                status="accepted",
                run_id=run_id,
            )
    except RecoveryError as exc:
        raise _map_recovery(exc) from exc


@router.get("/runs/{run_id}/recovery-cases", response_model=RecoveryCaseListResponse)
def list_recovery_cases(
    request: Request,
    run_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> RecoveryCaseListResponse:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            items = RecoveryService(uow.session).list_cases(run_id)
            return RecoveryCaseListResponse(items=items)
    except RecoveryError as exc:
        raise _map_recovery(exc) from exc


@router.get("/recovery-cases/{case_id}", response_model=RecoveryReport)
def get_recovery_case(
    request: Request,
    case_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> RecoveryReport:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return RecoveryService(uow.session).get_report(case_id)
    except RecoveryError as exc:
        raise _map_recovery(exc) from exc


@router.post(
    "/recovery-cases/{case_id}/decisions",
    response_model=OperationAccepted,
    status_code=202,
)
def decide_recovery_case(
    request: Request,
    case_id: str,
    body: RecoveryDecisionRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> OperationAccepted:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return RecoveryService(uow.session).apply_decision(
                case_id, body, actor="user:api"
            )
    except RecoveryError as exc:
        raise _map_recovery(exc) from exc


@router.get("/collaboration/operations/{operation_id}")
def get_collaboration_operation(
    request: Request,
    operation_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> Any:
    """Alias covering control/recovery ops; primary GET /operations/{id} remains."""
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return get_operation(uow.session, operation_id)
    except OperationError as exc:
        raise HTTPException(404, f"{exc.code}: {exc.message}") from exc
