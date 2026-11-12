"""Integration selection / build API (D12)."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException

from agentforge.contracts.integrations import IntegrationSelection, IntegrationSelectionRequest
from agentforge.core.config import Settings
from agentforge.integration.selection import SelectionError, create_selection
from agentforge.queries.authorization import AuthenticatedPrincipal, api_principal
from agentforge.storage.database import create_database_engine
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


def _map(exc: SelectionError) -> HTTPException:
    status = {
        "run_not_found": 404,
        "unauthorized": 401,
        "forbidden": 403,
        "selection_stale": 409,
        "release_scope_mismatch": 409,
        "integration_conflict": 409,
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
        raise _map(exc) from exc
