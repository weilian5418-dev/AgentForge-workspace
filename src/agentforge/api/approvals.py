"""Action authorization / native approval API (D04)."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Engine
from starlette.exceptions import HTTPException

from agentforge.contracts.plans import (
    ApprovalConfirmRequest,
    ApprovalDecisionRequest,
    ApprovalListResponse,
    ApprovalSummary,
    NativeApprovalRecordRequest,
)
from agentforge.core.config import Settings
from agentforge.permissions.service import (
    ApprovalError,
    confirm_native,
    decide,
    get_approval,
    list_approvals,
    record_native_request,
    retry_forward,
)
from agentforge.runtime.control import admits_new_work, read_admission
from agentforge.storage.uow import unit_of_work_connection

router = APIRouter(prefix="/api/v1", tags=["approvals"])


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _engine(request: Request, settings: Settings) -> Engine:
    if settings.database_url is None or request.app.state.database is None:
        raise HTTPException(503, "Database is not configured")
    return cast(Engine, request.app.state.database)


def _map(exc: ApprovalError) -> HTTPException:
    status = {
        "approval_not_found": 404,
        "adapter_not_found": 404,
        "action_digest_mismatch": 409,
        "native_id_session_conflict": 409,
        "decision_conflict": 409,
        "already_terminal": 409,
        "expired": 409,
        "retry_not_allowed": 409,
        "native_request_mismatch": 409,
        "already_expired": 400,
        "no_user_decision": 400,
        "invalid_state": 400,
        "confirm_not_allowed": 409,
        "trusted_confirm_required": 403,
        "reconcile_required": 409,
        "grant_not_effective": 403,
        "session_mismatch": 403,
        "attempt_mismatch": 403,
        "instance_mismatch": 403,
        "stage_mismatch": 403,
    }.get(exc.code, 400)
    return HTTPException(status, f"{exc.code}: {exc.message}")


@router.get("/approvals", response_model=ApprovalListResponse)
def get_approvals(
    request: Request,
    settings: Annotated[Settings, Depends(_settings)],
    status: Annotated[str | None, Query()] = None,
) -> ApprovalListResponse:
    engine = _engine(request, settings)
    with unit_of_work_connection(engine) as uow:
        items = list_approvals(uow.session, status=status)
    return ApprovalListResponse(items=items)


@router.post("/approvals", response_model=ApprovalSummary)
def post_approval(
    request: Request,
    body: NativeApprovalRecordRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> ApprovalSummary:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return record_native_request(uow.session, body)
    except ApprovalError as exc:
        raise _map(exc) from exc


@router.get("/approvals/{approval_id}", response_model=ApprovalSummary)
def get_one_approval(
    request: Request,
    approval_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> ApprovalSummary:
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return get_approval(uow.session, approval_id)
    except ApprovalError as exc:
        raise _map(exc) from exc


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalSummary)
def post_decision(
    request: Request,
    approval_id: str,
    body: ApprovalDecisionRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> ApprovalSummary:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            summary = decide(
                uow.session,
                approval_id=approval_id,
                decision=body.decision,
                action_digest=body.action_digest,
                note=body.note,
                forward=body.forward,
            )
        return summary
    except ApprovalError as exc:
        raise _map(exc) from exc


@router.post("/approvals/{approval_id}/confirm", response_model=ApprovalSummary)
def post_confirm(
    request: Request,
    approval_id: str,
    body: ApprovalConfirmRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> ApprovalSummary:
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return confirm_native(
                uow.session,
                approval_id=approval_id,
                native_request_id=body.native_request_id,
                confirmation_ref=body.confirmation_ref,
            )
    except ApprovalError as exc:
        raise _map(exc) from exc


@router.post("/approvals/{approval_id}/retry-forward", response_model=ApprovalSummary)
def post_retry_forward(
    request: Request,
    approval_id: str,
    settings: Annotated[Settings, Depends(_settings)],
    reconcile_token: str | None = None,
) -> ApprovalSummary:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            summary = retry_forward(
                uow.session, approval_id=approval_id, reconcile_token=reconcile_token
            )
        return summary
    except ApprovalError as exc:
        raise _map(exc) from exc
