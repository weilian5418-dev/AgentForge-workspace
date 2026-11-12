from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agentforge.contracts.events import parse_event_payload
from agentforge.storage.event_log import append_fact
from agentforge.storage.models import Event, EventReplayWindowRow, Outbox, Run
from agentforge.storage.uow import UnitOfWork


class EventRepository:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def append_with_outbox(
        self,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        outbox_target: str,
        outbox_intent_type: str | None = None,
        event_id: str | None = None,
    ) -> tuple[Event, Outbox]:
        """Lock run, validate payload, allocate sequence, write event + outbox together.

        Prefer this for transport wake intents. For pure domain facts without SSE
        outbox, use :func:`append_fact` (same sequence rules, no sse outbox).
        """
        session = self._uow.session
        # Validate before allocating sequence so invalid payloads leave no hole.
        parsed = parse_event_payload(event_type, payload)
        run = session.execute(
            select(Run).where(Run.id == run_id).with_for_update()
        ).scalar_one()
        next_sequence = int(run.last_sequence) + 1
        session.execute(update(Run).where(Run.id == run_id).values(last_sequence=next_sequence))

        eid = event_id or f"evt_{uuid.uuid4().hex}"
        event = Event(
            event_id=eid,
            run_id=run_id,
            sequence=next_sequence,
            schema_version="1",
            type=event_type,
            payload=parsed.model_dump(mode="json", by_alias=True),
        )
        session.add(event)

        outbox = Outbox(
            id=f"obx_{uuid.uuid4().hex}",
            run_id=run_id,
            event_id=eid,
            intent_type=outbox_intent_type or event_type,
            target=outbox_target,
            payload={
                "eventId": eid,
                "runId": run_id,
                "sequence": next_sequence,
                "type": event_type,
            },
            idempotency_key=f"event:{eid}",
            delivery_status="pending",
            attempt_count=0,
        )
        session.add(outbox)
        session.flush()
        return event, outbox

    def list_after(self, run_id: str, after_sequence: int, *, limit: int = 100) -> list[Event]:
        if after_sequence < 0:
            raise ValueError("after_sequence must be >= 0")
        floor = self.replay_floor(run_id)
        if after_sequence < floor:
            raise ValueError("cursor_expired: after_sequence below replay floor")
        stmt = (
            select(Event)
            .where(Event.run_id == run_id, Event.sequence > after_sequence)
            .order_by(Event.sequence.asc())
            .limit(limit)
        )
        return list(self._uow.session.execute(stmt).scalars())

    def replay_floor(self, run_id: str) -> int:
        row = self._uow.session.get(EventReplayWindowRow, run_id)
        if row is None:
            return 0
        return int(row.replay_floor_sequence)


def ensure_replay_window(session: Session, run_id: str) -> EventReplayWindowRow:
    row = session.get(EventReplayWindowRow, run_id)
    if row is not None:
        return row
    row = EventReplayWindowRow(
        run_id=run_id,
        replay_floor_sequence=0,
        last_archived_sequence=0,
        retention_version=1,
    )
    session.add(row)
    session.flush()
    return row


def append_domain_fact(
    session: Session, run_id: str, event_type: str, payload: dict[str, Any]
) -> Event:
    """Single entry for domain facts — typed payload + sequence in the same UOW."""
    return append_fact(session, run_id, event_type, payload)
