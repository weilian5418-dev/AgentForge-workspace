"""Bounded log reads by id (D12.10)."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request
from starlette.exceptions import HTTPException

from agentforge.core.config import Settings
from agentforge.queries.authorization import AuthenticatedPrincipal, api_principal
from agentforge.queries.logs import LogQueryError, read_log_slice

router = APIRouter(prefix="/api/v1", tags=["logs"])


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _principal(request: Request) -> AuthenticatedPrincipal:
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, AuthenticatedPrincipal):
        return principal
    return api_principal()


@router.get("/logs/{log_id}")
def get_log_slice(
    request: Request,
    log_id: str,
    settings: Annotated[Settings, Depends(_settings)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=65536)] = 4096,
) -> dict[str, object]:
    try:
        return read_log_slice(
            log_root=settings.state_dir / "checks",
            log_id=log_id,
            offset=offset,
            limit=limit,
            principal=_principal(request),
        )
    except LogQueryError as exc:
        status = {"not_found": 404, "unauthorized": 401, "forbidden": 403}.get(
            exc.code, 400
        )
        raise HTTPException(status, f"{exc.code}: {exc.message}") from exc
