"""D10 API: stages, submissions, reviews, user decisions, gates, rework."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.stages import (
    GateDecisionListResponse,
    ReviewCycleListResponse,
    ReviewReportRequest,
    ReworkOrderListResponse,
    StageListResponse,
    StageSubmissionRequest,
    StageUserDecisionRequest,
)
from agentforge.core.config import Settings
from agentforge.core.service_identity import verify_ticket
from agentforge.stages.errors import StageError
from agentforge.stages.service import StageService
from agentforge.storage.database import create_database_engine
from agentforge.storage.uow import unit_of_work_connection

router = APIRouter(prefix="/api/v1", tags=["stages"])


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _require_engine(request: Request, settings: Settings) -> Any:
    engine = getattr(request.app.state, "database", None) or create_database_engine(settings)
    if engine is None:
        raise HTTPException(503, "database_unavailable: Database is not configured")
    return engine


def _map_error(exc: StageError) -> HTTPException:
    status = {
        "stage_not_found": 404,
        "submission_not_found": 404,
        "review_cycle_not_found": 404,
        "review_request_not_found": 404,
        "rework_not_found": 404,
        "illegal_reviewer": 403,
        "idempotency_conflict": 409,
        "conflicting_release": 409,
        "candidate_version_mismatch": 409,
        "decision_version_mismatch": 409,
        "illegal_status": 409,
        "not_releasable": 409,
        "repair_rounds_exhausted": 409,
        "stop_evidence_missing": 409,
    }.get(exc.code, 400)
    return HTTPException(status, f"{exc.code}: {exc.message}")


@router.get("/runs/{run_id}/stages", response_model=StageListResponse)
def list_stages(
    request: Request,
    run_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> StageListResponse:
    engine = _require_engine(request, settings)
    with unit_of_work_connection(engine) as uow:
        return StageListResponse(
            items=StageService(
                uow.session, store=ArtifactStore(settings.state_dir / "artifact_store")
            ).list_stages(run_id)
        )


@router.get("/stages/{stage_id}")
def get_stage(
    request: Request,
    stage_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, Any]:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            view = StageService(
                uow.session, store=ArtifactStore(settings.state_dir / "artifact_store")
            ).get_stage(stage_id)
            return {
                "stage": view["stage"].model_dump(mode="json", by_alias=True),
                "reviewCycle": None
                if view["reviewCycle"] is None
                else view["reviewCycle"].model_dump(mode="json", by_alias=True),
                "candidateSet": None
                if view["candidateSet"] is None
                else view["candidateSet"].model_dump(mode="json", by_alias=True),
                "writeAccessClosed": view["writeAccessClosed"],
            }
    except StageError as exc:
        raise _map_error(exc) from exc


@router.post("/stages/{stage_id}/submissions", status_code=202)
def create_submission(
    request: Request,
    stage_id: str,
    body: StageSubmissionRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, Any]:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            accepted = StageService(
                uow.session, store=ArtifactStore(settings.state_dir / "artifact_store")
            ).submit(stage_id, body, submitter_ref="user:api")
            return accepted.model_dump(mode="json", by_alias=True)
    except StageError as exc:
        raise _map_error(exc) from exc


@router.get("/stages/{stage_id}/submissions")
def list_submissions(
    request: Request,
    stage_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, Any]:
    engine = _require_engine(request, settings)
    with unit_of_work_connection(engine) as uow:
        items = StageService(
            uow.session, store=ArtifactStore(settings.state_dir / "artifact_store")
        ).list_submissions(stage_id)
        return {"items": [i.model_dump(mode="json", by_alias=True) for i in items]}


@router.get("/stages/{stage_id}/review-cycles", response_model=ReviewCycleListResponse)
def list_cycles(
    request: Request,
    stage_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> ReviewCycleListResponse:
    engine = _require_engine(request, settings)
    with unit_of_work_connection(engine) as uow:
        return ReviewCycleListResponse(
            items=StageService(
                uow.session, store=ArtifactStore(settings.state_dir / "artifact_store")
            ).list_review_cycles(stage_id)
        )


@router.post("/stages/{stage_id}/reviews")
def submit_review(
    request: Request,
    stage_id: str,
    body: ReviewReportRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, Any]:
    try:
        reviewer = verify_ticket(
            settings,
            request.headers.get("X-Agent-Ticket", ""),
            audience="stage-review",
            request_id=body.review_request_id,
        )
    except (ValueError, TypeError, KeyError):
        raise HTTPException(
            403, "trusted_reviewer_required: Signed instance ticket required"
        ) from None
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            report_id = StageService(
                uow.session, store=ArtifactStore(settings.state_dir / "artifact_store")
            ).submit_review(stage_id, body, reviewer_instance_id=reviewer, trusted=True)
            return {"reportId": report_id}
    except StageError as exc:
        raise _map_error(exc) from exc


@router.post("/stages/{stage_id}/user-decisions")
def user_decision(
    request: Request,
    stage_id: str,
    body: StageUserDecisionRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, Any]:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            decision = StageService(
                uow.session, store=ArtifactStore(settings.state_dir / "artifact_store")
            ).submit_user_decision(stage_id, body, decided_by="user:api")
            return dict(decision.model_dump(mode="json", by_alias=True))
    except StageError as exc:
        raise _map_error(exc) from exc


@router.get("/stages/{stage_id}/gate-decisions", response_model=GateDecisionListResponse)
def list_gates(
    request: Request,
    stage_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> GateDecisionListResponse:
    engine = _require_engine(request, settings)
    with unit_of_work_connection(engine) as uow:
        return GateDecisionListResponse(
            items=StageService(
                uow.session, store=ArtifactStore(settings.state_dir / "artifact_store")
            ).list_gate_decisions(stage_id)
        )


@router.get("/stages/{stage_id}/rework-orders", response_model=ReworkOrderListResponse)
def list_rework(
    request: Request,
    stage_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> ReworkOrderListResponse:
    engine = _require_engine(request, settings)
    with unit_of_work_connection(engine) as uow:
        return ReworkOrderListResponse(
            items=StageService(
                uow.session, store=ArtifactStore(settings.state_dir / "artifact_store")
            ).list_rework_orders(stage_id)
        )


@router.post("/rework-orders/{order_id}/accept")
def accept_rework(
    order_id: str,
    request: Request,
    body: dict[str, str],
    settings: Annotated[Settings, Depends(_settings)],
) -> Any:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            StageService(
                uow.session, store=ArtifactStore(settings.state_dir / "artifact_store")
            ).accept_rework(order_id, workspace_id=body.get("workspaceId", ""))
        return {"status": "accepted"}
    except StageError as exc:
        raise _map_error(exc) from exc
