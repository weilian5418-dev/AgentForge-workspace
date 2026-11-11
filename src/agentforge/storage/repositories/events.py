from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update

from agentforge.storage.models import Event, Outbox, Run
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
        """Lock run, allocate sequence, write event + outbox in the current transaction."""
        session = self._uow.session
        run = session.execute(select(Run).where(Run.id == run_id).with_for_update()).scalar_one()
        next_sequence = int(run.last_sequence) + 1
        session.execute(update(Run).where(Run.id == run_id).values(last_sequence=next_sequence))

        eid = event_id or f"evt_{uuid.uuid4().hex}"
        event = Event(
            event_id=eid,
            run_id=run_id,
            sequence=next_sequence,
            schema_version="1",
            type=event_type,
            payload=payload,
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
        stmt = (
            select(Event)
            .where(Event.run_id == run_id, Event.sequence > after_sequence)
            .order_by(Event.sequence.asc())
            .limit(limit)
        )
        return list(self._uow.session.execute(stmt).scalars())
