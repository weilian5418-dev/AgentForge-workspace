from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import or_, select, update

from agentforge.storage.exceptions import OutboxClaimError
from agentforge.storage.models import Outbox
from agentforge.storage.uow import UnitOfWork, db_now


class OutboxRepository:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def claim_batch(
        self,
        *,
        owner: str,
        limit: int = 10,
        claim_ttl_seconds: int = 30,
    ) -> list[Outbox]:
        """Claim due pending/failed or expired claimed rows. Send happens after commit."""
        session = self._uow.session
        now = db_now(session)
        expires = now + timedelta(seconds=claim_ttl_seconds)

        candidates = session.execute(
            select(Outbox)
            .where(
                or_(
                    Outbox.delivery_status.in_(("pending", "failed")),
                    (Outbox.delivery_status == "claimed") & (Outbox.claim_expires_at <= now),
                )
            )
            .where(or_(Outbox.next_retry_at.is_(None), Outbox.next_retry_at <= now))
            .order_by(Outbox.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
        ).scalars()

        claimed: list[Outbox] = []
        for row in candidates:
            row.delivery_status = "claimed"
            row.claim_owner = owner
            row.claim_token = secrets.token_urlsafe(16)
            row.claim_expires_at = expires
            row.updated_at = now
            claimed.append(row)
        session.flush()
        return claimed

    def confirm_dispatched(self, *, outbox_id: str, owner: str, token: str) -> Outbox:
        session = self._uow.session
        now = db_now(session)
        result = session.execute(
            update(Outbox)
            .where(
                Outbox.id == outbox_id,
                Outbox.delivery_status == "claimed",
                Outbox.claim_owner == owner,
                Outbox.claim_token == token,
                Outbox.claim_expires_at > now,
            )
            .values(
                delivery_status="dispatched",
                claim_owner=None,
                claim_token=None,
                claim_expires_at=None,
                last_error=None,
                updated_at=now,
            )
            .returning(Outbox)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise OutboxClaimError(f"cannot confirm outbox {outbox_id}")
        return row

    def report_failure(
        self,
        *,
        outbox_id: str,
        owner: str,
        token: str,
        error: str,
        max_attempts: int = 5,
        retry_delay_seconds: int = 5,
    ) -> Outbox:
        session = self._uow.session
        now = db_now(session)
        masked = error[:512]
        row = session.execute(
            select(Outbox)
            .where(
                Outbox.id == outbox_id,
                Outbox.delivery_status == "claimed",
                Outbox.claim_owner == owner,
                Outbox.claim_token == token,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise OutboxClaimError(f"cannot fail outbox {outbox_id}")

        attempts = int(row.attempt_count) + 1
        row.attempt_count = attempts
        row.last_error = masked
        row.claim_owner = None
        row.claim_token = None
        row.claim_expires_at = None
        row.updated_at = now
        if attempts >= max_attempts:
            row.delivery_status = "dead"
            row.next_retry_at = None
        else:
            row.delivery_status = "failed"
            row.next_retry_at = now + timedelta(seconds=retry_delay_seconds)
        session.flush()
        return row
