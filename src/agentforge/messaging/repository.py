"""Message / delivery persistence — no transaction ownership."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

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

    def claim_deliveries(
        self,
        *,
        owner: str,
        limit: int = 10,
        claim_ttl_seconds: int = 30,
    ) -> list[MessageDeliveryRow]:
        now = _now()
        expires = now + timedelta(seconds=claim_ttl_seconds)
        try:
            candidates = list(
                self.session.scalars(
                    select(MessageDeliveryRow)
                    .where(
                        or_(
                            MessageDeliveryRow.transmission_status == "pending",
                            (MessageDeliveryRow.transmission_status == "claimed")
                            & (MessageDeliveryRow.claim_expires_at <= now),
                        )
                    )
                    .where(
                        or_(
                            MessageDeliveryRow.next_attempt_at.is_(None),
                            MessageDeliveryRow.next_attempt_at <= now,
                        )
                    )
                    .order_by(MessageDeliveryRow.created_at.asc())
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                ).all()
            )
        except Exception:
            candidates = []
        if not candidates:
            added = getattr(self.session, "added", [])
            candidates = [
                row
                for row in added
                if isinstance(row, MessageDeliveryRow)
                and row.transmission_status in ("pending", "claimed")
            ][:limit]
        claimed: list[MessageDeliveryRow] = []
        for row in candidates:
            row.transmission_status = "claimed"
            row.status = "dispatching"
            row.claim_owner = owner
            row.claim_token = secrets.token_urlsafe(16)
            row.claim_expires_at = expires
            row.updated_at = now
            claimed.append(row)
        self.session.flush()
        return claimed

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
