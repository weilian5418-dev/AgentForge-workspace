from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from agentforge.storage.exceptions import IdempotencyConflictError, IdempotencyInProgressError
from agentforge.storage.models import IdempotencyRequest
from agentforge.storage.uow import UnitOfWork, db_now


class IdempotencyRepository:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def begin_or_replay(
        self,
        *,
        identity_scope: str,
        operation_type: str,
        request_key: str,
        request_hash: str,
        owner: str,
        processing_ttl_seconds: int = 30,
    ) -> tuple[str, IdempotencyRequest | None]:
        """Return ('execute', None) | ('replay', row) | raise conflict/in-progress."""
        session = self._uow.session
        now = db_now(session)
        expires = now + timedelta(seconds=processing_ttl_seconds)
        token = secrets.token_urlsafe(16)
        row_id = f"idem_{uuid.uuid4().hex}"

        insert_stmt = (
            insert(IdempotencyRequest)
            .values(
                id=row_id,
                identity_scope=identity_scope,
                operation_type=operation_type,
                request_key=request_key,
                request_hash=request_hash,
                response_json={},
                state="processing",
                processing_owner=owner,
                processing_token=token,
                processing_expires_at=expires,
            )
            .on_conflict_do_nothing(
                constraint="idempotency_requests_identity_scope_operation_type_request__key"
            )
            .returning(IdempotencyRequest)
        )
        inserted = session.execute(insert_stmt).scalar_one_or_none()
        if inserted is not None:
            return "execute", inserted

        existing = session.execute(
            select(IdempotencyRequest)
            .where(
                IdempotencyRequest.identity_scope == identity_scope,
                IdempotencyRequest.operation_type == operation_type,
                IdempotencyRequest.request_key == request_key,
            )
            .with_for_update()
        ).scalar_one()

        if existing.request_hash != request_hash:
            raise IdempotencyConflictError("same idempotency key with different request content")
        if existing.state == "completed":
            return "replay", existing
        if (
            existing.state == "processing"
            and existing.processing_expires_at is not None
            and existing.processing_expires_at > now
        ):
            raise IdempotencyInProgressError("identical request is still processing")

        # Expired processing slot: take over.
        existing.state = "processing"
        existing.request_hash = request_hash
        existing.processing_owner = owner
        existing.processing_token = token
        existing.processing_expires_at = expires
        existing.response_json = {}
        existing.response_status_code = None
        session.flush()
        return "execute", existing

    def complete(
        self,
        *,
        record: IdempotencyRequest,
        owner: str,
        token: str,
        status_code: int,
        response_json: dict[str, Any],
    ) -> IdempotencyRequest:
        result = self._uow.session.execute(
            update(IdempotencyRequest)
            .where(
                IdempotencyRequest.id == record.id,
                IdempotencyRequest.state == "processing",
                IdempotencyRequest.processing_owner == owner,
                IdempotencyRequest.processing_token == token,
            )
            .values(
                state="completed",
                response_status_code=status_code,
                response_json=response_json,
                processing_owner=None,
                processing_token=None,
                processing_expires_at=None,
            )
            .returning(IdempotencyRequest)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise IdempotencyInProgressError("idempotency completion token mismatch")
        return row
