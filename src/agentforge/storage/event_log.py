"""Append committed facts in the same transaction as their domain change."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from agentforge.contracts.events import parse_event_payload
from agentforge.storage.models import Event, Run


def append_fact(session: Session, run_id: str, event_type: str, payload: dict[str, Any]) -> Event:
    parsed = parse_event_payload(event_type, payload)
    session.flush()  # Preserve domain mutations before refreshing the locked run row.
    run = session.get(Run, run_id, with_for_update=True, populate_existing=True)
    if run is None:
        raise ValueError("Event run is missing")
    run.last_sequence = int(run.last_sequence or 0) + 1
    event = Event(
        event_id="evt_" + uuid.uuid4().hex,
        run_id=run.id,
        sequence=run.last_sequence,
        schema_version="1",
        type=event_type,
        payload=parsed.model_dump(mode="json", by_alias=True),
    )
    session.add(event)
    return event
