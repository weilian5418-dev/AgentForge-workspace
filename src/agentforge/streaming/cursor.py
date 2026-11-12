"""Resolve Last-Event-ID / afterSequence into a replay cursor (D12.5)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.event_stream import ResolvedCursor
from agentforge.queries.authorization import AuthenticatedPrincipal, authorize_run_read
from agentforge.storage.models import Event, EventReplayWindowRow

_MAX_EVENT_ID_LEN = 128


class CursorError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class _Resolved:
    after_sequence: int
    last_event_id: str | None
    latest_sequence: int
    replay_floor: int
    resync_required: bool
    reason: str | None


def _reject_opaque_id(value: str, *, field: str) -> str:
    if not value or len(value) > _MAX_EVENT_ID_LEN:
        raise CursorError("cursor_invalid", f"{field} length invalid")
    if "\n" in value or "\r" in value or "\x00" in value:
        raise CursorError("cursor_invalid", f"{field} contains illegal control characters")
    return value


def resolve_event_cursor(
    session: Session,
    *,
    principal: AuthenticatedPrincipal,
    run_id: str,
    last_event_id: str | None = None,
    after_sequence: int | None = None,
) -> ResolvedCursor:
    """Resolve SSE resume cursors. First connect with both None → snapshot_required."""
    run = authorize_run_read(session, principal, run_id)
    latest = int(run.last_sequence or 0)
    window = session.get(EventReplayWindowRow, run_id)
    floor = int(window.replay_floor_sequence) if window is not None else 0

    if last_event_id is None and after_sequence is None:
        return ResolvedCursor.model_validate(
            {
                "runId": run_id,
                "afterSequence": 0,
                "lastEventId": None,
                "latestSequence": latest,
                "replayFloorSequence": floor,
                "resyncRequired": True,
                "reason": "snapshot_required",
            }
        )

    from_header: _Resolved | None = None
    from_query: _Resolved | None = None

    if last_event_id is not None:
        eid = _reject_opaque_id(last_event_id, field="Last-Event-ID")
        event = session.scalar(select(Event).where(Event.event_id == eid))
        if event is None:
            # Archived / unknown — do not invent sequence 0
            return ResolvedCursor.model_validate(
                {
                    "runId": run_id,
                    "afterSequence": 0,
                    "lastEventId": eid,
                    "latestSequence": latest,
                    "replayFloorSequence": floor,
                    "resyncRequired": True,
                    "reason": "cursor_expired",
                }
            )
        if event.run_id != run_id:
            raise CursorError(
                "cursor_scope_mismatch",
                "Last-Event-ID belongs to a different run",
            )
        seq = int(event.sequence)
        if seq < floor:
            return ResolvedCursor.model_validate(
                {
                    "runId": run_id,
                    "afterSequence": 0,
                    "lastEventId": eid,
                    "latestSequence": latest,
                    "replayFloorSequence": floor,
                    "resyncRequired": True,
                    "reason": "cursor_expired",
                }
            )
        from_header = _Resolved(
            after_sequence=seq,
            last_event_id=eid,
            latest_sequence=latest,
            replay_floor=floor,
            resync_required=False,
            reason=None,
        )

    if after_sequence is not None:
        if not isinstance(after_sequence, int) or isinstance(after_sequence, bool):
            raise CursorError("cursor_invalid", "afterSequence must be an integer")
        if after_sequence < 0:
            raise CursorError("cursor_invalid", "afterSequence must be non-negative")
        if after_sequence > latest:
            raise CursorError(
                "cursor_ahead",
                f"afterSequence {after_sequence} ahead of latestSequence {latest}",
            )
        if after_sequence > 0 and after_sequence < floor:
            return ResolvedCursor.model_validate(
                {
                    "runId": run_id,
                    "afterSequence": 0,
                    "lastEventId": None,
                    "latestSequence": latest,
                    "replayFloorSequence": floor,
                    "resyncRequired": True,
                    "reason": "cursor_expired",
                }
            )
        # afterSequence=0 is valid for empty / first-event runs
        from_query = _Resolved(
            after_sequence=after_sequence,
            last_event_id=None,
            latest_sequence=latest,
            replay_floor=floor,
            resync_required=False,
            reason=None,
        )

    if from_header is not None and from_query is not None:
        if from_header.after_sequence != from_query.after_sequence:
            raise CursorError(
                "cursor_conflict",
                "Last-Event-ID and afterSequence resolve to different positions",
            )
        chosen = from_header
    elif from_header is not None:
        chosen = from_header
    else:
        assert from_query is not None
        chosen = from_query

    return ResolvedCursor.model_validate(
        {
            "runId": run_id,
            "afterSequence": chosen.after_sequence,
            "lastEventId": chosen.last_event_id,
            "latestSequence": chosen.latest_sequence,
            "replayFloorSequence": chosen.replay_floor,
            "resyncRequired": chosen.resync_required,
            "reason": chosen.reason,
        }
    )
