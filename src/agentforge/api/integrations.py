"""Integration selection / build API (D12)."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from starlette.exceptions import HTTPException

from agentforge.contracts.integrations import (
    IntegrationBuildRequest,
    IntegrationBuildView,
    IntegrationSelection,
    IntegrationSelectionRequest,
    ValidationCycleRequest,
    ValidationCycleView,
)
from agentforge.core.config import Settings
from agentforge.integration.check_worker import (
    CheckWorkerError,
    start_validation_for_version,
)
from agentforge.integration.coordinator import (
    IntegrationError,
    process_integration_build,
    reconcile_integration_build,
    request_integration_build,
)
from agentforge.integration.selection import SelectionError, create_selection
from agentforge.queries.authorization import AuthenticatedPrincipal, api_principal
from agentforge.storage.database import create_database_engine
from agentforge.storage.models import IntegrationBuildRow
from agentforge.storage.uow import unit_of_work_connection

router = APIRouter(prefix="/api/v1", tags=["integrations"])


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _require_engine(request: Request, settings: Settings) -> Any:
    engine = getattr(request.app.state, "database", None) or create_database_engine(settings)
    if engine is None:
        raise HTTPException(503, "database_unavailable: Database is not configured")
    return engine


def _principal(request: Request) -> AuthenticatedPrincipal:
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, AuthenticatedPrincipal):
        return principal
    return api_principal()


def _map_selection(exc: SelectionError) -> HTTPException:
    status = {
        "run_not_found": 404,
        "unauthorized": 401,
        "forbidden": 403,
        "selection_stale": 409,
        "release_scope_mismatch": 409,
        "integration_conflict": 409,
    }.get(exc.code, 400)
    return HTTPException(status, f"{exc.code}: {exc.message}")


def _map_build(exc: IntegrationError) -> HTTPException:
    status = {
        "run_not_found": 404,
        "build_not_found": 404,
        "unauthorized": 401,
        "selection_stale": 409,
        "integration_conflict": 409,
        "invalid_state": 409,
    }.get(exc.code, 400)
    return HTTPException(status, f"{exc.code}: {exc.message}")


@router.post(
    "/runs/{run_id}/integration-selections",
    response_model=IntegrationSelection,
    status_code=201,
)
def post_integration_selection(
    request: Request,
    run_id: str,
    body: IntegrationSelectionRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> IntegrationSelection:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return create_selection(
                uow.session,
                principal=_principal(request),
                run_id=run_id,
                request=body,
            )
    except SelectionError as exc:
        raise _map_selection(exc) from exc


@router.post(
    "/runs/{run_id}/integrations",
    response_model=IntegrationBuildView,
    status_code=202,
)
def post_integration_build(
    request: Request,
    run_id: str,
    body: IntegrationBuildRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> IntegrationBuildView:
    """Accept integration build intent — does not claim already integrated/passed."""
    engine = _require_engine(request, settings)
    try:
        view = request_integration_build(
            engine,
            principal=_principal(request),
            run_id=run_id,
            body=body,
            settings=settings,
        )
        # Local single-tenant: process immediately after accept (outbox also recorded).
        return process_integration_build(engine, settings, build_id=view.build_id)
    except IntegrationError as exc:
        raise _map_build(exc) from exc


@router.get("/runs/{run_id}/integrations", response_model=list[IntegrationBuildView])
def list_integration_builds(
    request: Request,
    run_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> list[IntegrationBuildView]:
    engine = _require_engine(request, settings)
    with unit_of_work_connection(engine) as uow:
        rows = list(
            uow.session.scalars(
                select(IntegrationBuildRow)
                .where(IntegrationBuildRow.run_id == run_id)
                .order_by(IntegrationBuildRow.created_at.asc())
            ).all()
        )
        from agentforge.integration.coordinator import _to_view

        return [_to_view(r) for r in rows]


@router.post(
    "/integration-builds/{build_id}/reconcile",
    response_model=IntegrationBuildView,
)
def reconcile_build(
    request: Request,
    build_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> IntegrationBuildView:
    engine = _require_engine(request, settings)
    try:
        return reconcile_integration_build(engine, settings, build_id=build_id)
    except IntegrationError as exc:
        raise _map_build(exc) from exc


@router.post(
    "/integration-versions/{version_id}/validation-cycles",
    response_model=ValidationCycleView,
    status_code=201,
)
def open_validation_cycle_api(
    request: Request,
    version_id: str,
    body: ValidationCycleRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> ValidationCycleView:
    """Open a validation cycle from a confirmed policy — clients cannot pass outcomes."""
    engine = _require_engine(request, settings)
    # Default first check: python_pytest_v1 with tests/ — policy_ref selects package.
    check_specs = [
        {
            "checkId": "python_pytest",
            "checkVersion": "v1",
            "config": {"testPaths": ["tests"], "minimumCollectedTests": 1},
        }
    ]
    try:
        return start_validation_for_version(
            engine,
            settings,
            principal=_principal(request),
            version_id=version_id,
            body=body,
            check_specs=check_specs,
        )
    except CheckWorkerError as exc:
        status = {
            "version_not_found": 404,
            "run_not_found": 404,
            "unauthorized": 401,
            "invalid_config": 400,
            "check_not_registered": 400,
        }.get(exc.code, 400)
        raise HTTPException(status, f"{exc.code}: {exc.message}") from exc
