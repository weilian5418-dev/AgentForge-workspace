"""Run create / plan propose / revise / assign / decide API (D08)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException

from agentforge.contracts.agents import RoleAssignmentsPutRequest, RoleAssignmentsResponse
from agentforge.contracts.plans import (
    PlanDecisionRequest,
    PlanProposalRequest,
    PlanResponse,
    PlanRevisionRequest,
)
from agentforge.contracts.runs import RunAcceptedResponse, RunCreateRequest, RunSnapshotResponse
from agentforge.core.config import Settings
from agentforge.planning.service import PlanningError, PlanningService
from agentforge.runtime.control import admits_new_work, read_admission
from agentforge.storage.database import create_database_engine
from agentforge.storage.uow import unit_of_work_connection

router = APIRouter(prefix="/api/v1", tags=["runs"])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _require_engine(request: Request, settings: Settings):
    engine = getattr(request.app.state, "database", None) or create_database_engine(settings)
    if engine is None:
        raise HTTPException(503, "database_unavailable: Database is not configured")
    return engine


def _map_error(exc: PlanningError) -> HTTPException:
    status = {
        "project_not_found": 404,
        "run_not_found": 404,
        "plan_not_found": 404,
        "confirmation_not_found": 404,
        "grant_revoked": 403,
        "grant_inactive": 403,
        "directory_replaced": 409,
        "status_version_conflict": 409,
        "plan_version_conflict": 409,
        "decision_conflict": 409,
        "confirmation_scope_mismatch": 409,
        "confirmation_not_pending": 409,
        "plan_already_approved": 409,
        "invalid_state": 409,
    }.get(exc.code, 400)
    return HTTPException(status, f"{exc.code}: {exc.message}")


@router.post("/runs", response_model=RunAcceptedResponse)
def create_run(
    request: Request,
    body: RunCreateRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> RunAcceptedResponse:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "admission_blocked: Platform is not admitting new work")
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return PlanningService(uow.session).create_run(body)
    except PlanningError as exc:
        raise _map_error(exc) from exc


@router.get("/runs/{run_id}", response_model=RunSnapshotResponse)
def get_run(
    request: Request,
    run_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> RunSnapshotResponse:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return PlanningService(uow.session).get_run(run_id)
    except PlanningError as exc:
        raise _map_error(exc) from exc


@router.post("/runs/{run_id}/plan-proposals", response_model=PlanResponse)
def propose_plan(
    request: Request,
    run_id: str,
    body: PlanProposalRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> PlanResponse:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return PlanningService(uow.session).propose(run_id, body)
    except PlanningError as exc:
        raise _map_error(exc) from exc


@router.get("/runs/{run_id}/plan", response_model=PlanResponse)
def get_plan(
    request: Request,
    run_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> PlanResponse:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return PlanningService(uow.session).get_plan(run_id)
    except PlanningError as exc:
        raise _map_error(exc) from exc


@router.post("/runs/{run_id}/plan-revisions", response_model=PlanResponse)
def revise_plan(
    request: Request,
    run_id: str,
    body: PlanRevisionRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> PlanResponse:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return PlanningService(uow.session).revise(run_id, body)
    except PlanningError as exc:
        raise _map_error(exc) from exc


@router.put("/runs/{run_id}/assignments", response_model=RoleAssignmentsResponse)
def put_assignments(
    request: Request,
    run_id: str,
    body: RoleAssignmentsPutRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> RoleAssignmentsResponse:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            items = PlanningService(uow.session).put_assignments(
                run_id,
                plan_version=body.plan_version,
                items=list(body.assignments),
            )
            run = PlanningService(uow.session).get_run(run_id)
            return RoleAssignmentsResponse(
                run_id=run_id,
                plan_version=body.plan_version,
                assignment_version=run.assignment_version or 1,
                items=items,
            )
    except PlanningError as exc:
        raise _map_error(exc) from exc


@router.post("/runs/{run_id}/plan-decisions", response_model=PlanResponse)
def decide_plan(
    request: Request,
    run_id: str,
    body: PlanDecisionRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> PlanResponse:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return PlanningService(uow.session).decide(run_id, body, actor="api")
    except PlanningError as exc:
        raise _map_error(exc) from exc
