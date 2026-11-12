"""SSE event stream API (D12.6)."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from starlette.exceptions import HTTPException

from agentforge.core.config import Settings
from agentforge.queries.authorization import AuthenticatedPrincipal, api_principal
from agentforge.streaming.cursor import CursorError, resolve_event_cursor
from agentforge.streaming.service import (
    EventStreamService,
    StreamLimitError,
    StreamSlotRegistry,
)
from agentforge.storage.database import create_database_engine
from agentforge.storage.uow import unit_of_work_connection

router = APIRouter(prefix="/api/v1", tags=["events"])


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


def _slots(request: Request) -> StreamSlotRegistry:
    slots = getattr(request.app.state, "stream_slots", None)
    if slots is None:
        slots = StreamSlotRegistry()
        request.app.state.stream_slots = slots
    return slots


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    request: Request,
    run_id: str,
    settings: Annotated[Settings, Depends(_settings)],
    after_sequence: Annotated[int | None, Query(alias="afterSequence")] = None,
    max_batch: Annotated[int, Query(alias="maxBatch", ge=1, le=100)] = 100,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """SSE stream — does not start the Run. Requires snapshot cursor on first connect."""
    engine = _require_engine(request, settings)
    principal = _principal(request)
    bearer = getattr(request.state, "bearer_token", None)

    try:
        with unit_of_work_connection(engine) as uow:
            cursor = resolve_event_cursor(
                uow.session,
                principal=principal,
                run_id=run_id,
                last_event_id=last_event_id,
                after_sequence=after_sequence,
            )
    except CursorError as exc:
        status = {
            "cursor_ahead": 409,
            "cursor_conflict": 409,
            "cursor_scope_mismatch": 409,
            "cursor_invalid": 400,
            "run_not_found": 404,
            "forbidden": 403,
            "unauthorized": 401,
        }.get(exc.code, 400)
        raise HTTPException(status, f"{exc.code}: {exc.message}") from exc

    if cursor.resync_required and cursor.reason == "snapshot_required":
        raise HTTPException(
            409,
            "snapshot_required: GET /api/v1/runs/{id}/snapshot then afterSequence",
        )
    if cursor.resync_required:
        # Pre-stream resync (expired cursor) — JSON rather than opening a stream
        raise HTTPException(
            409,
            f"{cursor.reason or 'resync_required'}: reload snapshot before streaming",
        )

    verifier = request.app.state.verifier
    slots = _slots(request)
    try:
        await slots.acquire(principal.credential_id)
    except StreamLimitError as exc:
        raise HTTPException(429, f"{exc.code}: {exc.message}") from exc

    service = EventStreamService(
        engine, settings, verifier=verifier, slots=slots
    )

    async def frames():
        try:
            async for frame in service.iter_frames(
                principal=principal,
                run_id=run_id,
                after_sequence=int(cursor.after_sequence),
                bearer_token=bearer if isinstance(bearer, str) else None,
                is_disconnected=request.is_disconnected,
                max_batch=max_batch,
                manage_slot=False,
            ):
                yield frame
        finally:
            await slots.release(principal.credential_id)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
