"""Event replay window, archive, and floor advance (D12.7)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from agentforge.storage.models import Event, EventReplayWindowRow, Run
from agentforge.storage.repositories.events import ensure_replay_window
from agentforge.storage.uow import db_now


class RetentionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ReplayWindow:
    run_id: str
    replay_floor_sequence: int
    last_archived_sequence: int
    retention_version: int
    latest_sequence: int


@dataclass(frozen=True, slots=True)
class WindowReadResult:
    events: list[Event]
    next_after_sequence: int
    status: Literal["ok", "caught_up", "cursor_expired", "cursor_ahead", "gap_detected"]
    reason: str | None = None


def get_replay_window(session: Session, run_id: str) -> ReplayWindow:
    run = session.get(Run, run_id)
    if run is None:
        raise RetentionError("run_not_found", run_id)
    window = ensure_replay_window(session, run_id)
    latest = int(run.last_sequence or 0)
    floor = int(window.replay_floor_sequence)
    archived = int(window.last_archived_sequence)
    if floor > latest:
        raise RetentionError(
            "storage_integrity",
            f"replay floor {floor} exceeds last_sequence {latest}",
        )
    if archived > floor:
        raise RetentionError(
            "storage_integrity",
            f"archived {archived} exceeds floor {floor}",
        )
    return ReplayWindow(
        run_id=run_id,
        replay_floor_sequence=floor,
        last_archived_sequence=archived,
        retention_version=int(window.retention_version),
        latest_sequence=latest,
    )


def read_after_in_window(
    session: Session,
    *,
    run_id: str,
    after_sequence: int,
    limit: int = 100,
) -> WindowReadResult:
    """Read events in the same transaction as the window watermark."""
    if after_sequence < 0:
        raise RetentionError("cursor_invalid", "after_sequence must be non-negative")
    window = get_replay_window(session, run_id)
    if after_sequence < window.replay_floor_sequence:
        return WindowReadResult(
            events=[],
            next_after_sequence=after_sequence,
            status="cursor_expired",
            reason="cursor_expired",
        )
    if after_sequence > window.latest_sequence:
        return WindowReadResult(
            events=[],
            next_after_sequence=after_sequence,
            status="cursor_ahead",
            reason="cursor_ahead",
        )
    if after_sequence == window.latest_sequence:
        return WindowReadResult(
            events=[],
            next_after_sequence=after_sequence,
            status="caught_up",
        )

    limit = max(1, min(int(limit), 100))
    rows = list(
        session.scalars(
            select(Event)
            .where(Event.run_id == run_id, Event.sequence > after_sequence)
            .order_by(Event.sequence.asc())
            .limit(limit)
        ).all()
    )
    expected = after_sequence + 1
    if not rows or int(rows[0].sequence) != expected:
        # Missing contiguous next event while still inside online window
        return WindowReadResult(
            events=[],
            next_after_sequence=after_sequence,
            status="gap_detected",
            reason="gap_detected",
        )
    # Truncate at first gap inside the page
    contiguous: list[Event] = []
    next_expected = expected
    for row in rows:
        if int(row.sequence) != next_expected:
            break
        contiguous.append(row)
        next_expected += 1
    return WindowReadResult(
        events=contiguous,
        next_after_sequence=int(contiguous[-1].sequence),
        status="ok",
    )


def advance_replay_floor(
    session: Session,
    *,
    run_id: str,
    new_floor: int,
    delete_online: bool = False,
) -> ReplayWindow:
    """Advance replay floor. Default: policy only — do not delete audit Events.

    delete_online=True requires last_archived_sequence >= new_floor (archive first).
    """
    run = session.get(Run, run_id, with_for_update=True)
    if run is None:
        raise RetentionError("run_not_found", run_id)
    ensure_replay_window(session, run_id)
    window = session.get(EventReplayWindowRow, run_id, with_for_update=True)
    assert window is not None
    latest = int(run.last_sequence or 0)
    current = int(window.replay_floor_sequence)
    archived = int(window.last_archived_sequence)
    if new_floor < current:
        raise RetentionError("invalid_request", "floor can only move forward")
    if new_floor > latest:
        raise RetentionError(
            "invalid_request",
            f"floor {new_floor} cannot exceed last_sequence {latest}",
        )
    if delete_online and archived < new_floor:
        raise RetentionError(
            "archive_required",
            "cannot delete online events past archived watermark",
        )
    window.replay_floor_sequence = new_floor
    window.retention_version = int(window.retention_version) + 1
    window.updated_at = _now(session)
    if delete_online and new_floor > 0:
        session.execute(
            delete(Event).where(
                Event.run_id == run_id,
                Event.sequence <= new_floor,
                Event.sequence <= archived,
            )
        )
    session.flush()
    return get_replay_window(session, run_id)


def archive_event_range(
    session: Session,
    *,
    run_id: str,
    from_sequence_exclusive: int,
    to_sequence_inclusive: int,
    archive_dir: Path,
    raise_floor: bool = True,
    delete_online: bool = False,
) -> dict[str, Any]:
    """Archive a contiguous online range, verify readable, then raise watermarks.

    On archive failure the floor/archived watermarks are not raised.
    """
    if to_sequence_inclusive <= from_sequence_exclusive:
        raise RetentionError("invalid_request", "empty archive range")
    run = session.get(Run, run_id, with_for_update=True)
    if run is None:
        raise RetentionError("run_not_found", run_id)
    ensure_replay_window(session, run_id)
    window = session.get(EventReplayWindowRow, run_id, with_for_update=True)
    assert window is not None
    latest = int(run.last_sequence or 0)
    if to_sequence_inclusive > latest:
        raise RetentionError("invalid_request", "archive end past last_sequence")
    if from_sequence_exclusive < int(window.last_archived_sequence):
        raise RetentionError("invalid_request", "range overlaps already archived")

    rows = list(
        session.scalars(
            select(Event)
            .where(
                Event.run_id == run_id,
                Event.sequence > from_sequence_exclusive,
                Event.sequence <= to_sequence_inclusive,
            )
            .order_by(Event.sequence.asc())
        ).all()
    )
    expected = from_sequence_exclusive + 1
    for row in rows:
        if int(row.sequence) != expected:
            raise RetentionError(
                "gap_detected",
                f"missing sequence {expected} before archive",
            )
        expected += 1
    if not rows or int(rows[-1].sequence) != to_sequence_inclusive:
        raise RetentionError("gap_detected", "archive range not fully present online")

    payload = {
        "schemaVersion": "1",
        "runId": run_id,
        "fromSequenceExclusive": from_sequence_exclusive,
        "toSequenceInclusive": to_sequence_inclusive,
        "events": [
            {
                "eventId": r.event_id,
                "sequence": int(r.sequence),
                "type": r.type,
                "timestamp": r.created_at.isoformat(),
                "payload": r.payload,
            }
            for r in rows
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"{run_id}_{from_sequence_exclusive}_{to_sequence_inclusive}_{digest[:16]}.json"
    # Write then verify before raising watermarks
    path.write_bytes(raw)
    verified = path.read_bytes()
    if hashlib.sha256(verified).hexdigest() != digest:
        path.unlink(missing_ok=True)
        raise RetentionError("archive_failed", "archive content failed verification")
    try:
        check = json.loads(verified.decode("utf-8"))
        if check.get("toSequenceInclusive") != to_sequence_inclusive:
            raise RetentionError("archive_failed", "archive payload corrupt")
    except (json.JSONDecodeError, RetentionError):
        path.unlink(missing_ok=True)
        raise RetentionError("archive_failed", "archive unreadable") from None

    window.last_archived_sequence = to_sequence_inclusive
    if raise_floor:
        window.replay_floor_sequence = max(
            int(window.replay_floor_sequence), to_sequence_inclusive
        )
    window.retention_version = int(window.retention_version) + 1
    window.updated_at = _now(session)
    if delete_online:
        session.execute(
            delete(Event).where(
                Event.run_id == run_id,
                Event.sequence > from_sequence_exclusive,
                Event.sequence <= to_sequence_inclusive,
            )
        )
    session.flush()
    return {
        "runId": run_id,
        "archivePath": str(path),
        "archiveHash": digest,
        "lastArchivedSequence": to_sequence_inclusive,
        "replayFloorSequence": int(window.replay_floor_sequence),
        "retentionVersion": int(window.retention_version),
        "deletedOnline": delete_online,
    }


def _now(session: Session) -> datetime:
    try:
        value = db_now(session)
        if getattr(value, "tzinfo", None) is None:
            return value.replace(tzinfo=UTC)  # type: ignore[union-attr]
        return value  # type: ignore[return-value]
    except Exception:
        return datetime.now(UTC)
