"""Message / delivery persistence — no transaction ownership."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.instances.errors import InstanceError
from agentforge.storage.models import (
    CollaborationMessageRow,
    DeliveryAttemptRow,
    MessageDeliveryRow,
)


def _now() -> datetime:
    return datetime.now(UTC)


class MessageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_message(self, row: CollaborationMessageRow) -> CollaborationMessageRow:
        self.session.add(row)
        return row

    def get_message(self, message_id: str) -> CollaborationMessageRow | None:
        return self.session.get(CollaborationMessageRow, message_id)

    def list_messages(self, run_id: str) -> list[CollaborationMessageRow]:
        return list(
            self.session.scalars(
                select(CollaborationMessageRow)
                .where(CollaborationMessageRow.run_id == run_id)
                .order_by(CollaborationMessageRow.created_at.asc())
            ).all()
        )

    def add_delivery(self, row: MessageDeliveryRow) -> MessageDeliveryRow:
        self.session.add(row)
        return row

    def get_delivery(self, delivery_id: str) -> MessageDeliveryRow | None:
        return self.session.get(MessageDeliveryRow, delivery_id)

    def list_deliveries(self, message_id: str) -> list[MessageDeliveryRow]:
        return list(
            self.session.scalars(
                select(MessageDeliveryRow)
                .where(MessageDeliveryRow.message_id == message_id)
                .order_by(MessageDeliveryRow.created_at.asc())
            ).all()
        )

    def find_delivery(
        self, *, message_id: str, recipient_instance_id: str, attempt_id: str
    ) -> MessageDeliveryRow | None:
        return self.session.scalar(
            select(MessageDeliveryRow).where(
                MessageDeliveryRow.message_id == message_id,
                MessageDeliveryRow.recipient_instance_id == recipient_instance_id,
                MessageDeliveryRow.attempt_id == attempt_id,
            )
        )

    def claim_deliveries(self, **kwargs: Any) -> Any:
        raise InstanceError(
            "engine_coordinator_required",
            "Use durable instances.worker, not transaction-bound claim helpers",
        )

    def add_delivery_attempt(self, row: DeliveryAttemptRow) -> DeliveryAttemptRow:
        self.session.add(row)
        return row

    def list_delivery_attempts(self, delivery_id: str) -> list[DeliveryAttemptRow]:
        return list(
            self.session.scalars(
                select(DeliveryAttemptRow)
                .where(DeliveryAttemptRow.delivery_id == delivery_id)
                .order_by(DeliveryAttemptRow.started_at.asc())
            ).all()
        )
