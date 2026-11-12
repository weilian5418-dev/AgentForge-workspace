"""Bounded async SSE event stream service (D12.6)."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agentforge.contracts.event_stream import EventBatch, PublicEventEnvelope, SseControlFrame
from agentforge.core.config import Settings
from agentforge.core.credentials import CredentialError, CredentialVerifier
from agentforge.queries.authorization import AuthenticatedPrincipal, authorize_run_read
from agentforge.streaming.framing import (
    encode_business_event,
    encode_control_frame,
    encode_heartbeat,
)
from agentforge.streaming.projector import ProjectionError, project_public_event
from agentforge.streaming.retention import read_after_in_window

MAX_BATCH_EVENTS = 100
MAX_BATCH_BYTES = 256 * 1024
HEARTBEAT_SECONDS = 15.0
POLL_SECONDS = 1.0
MAX_STREAM_SECONDS = 300.0
MAX_CONNECTIONS_TOTAL = 10
MAX_CONNECTIONS_PER_CREDENTIAL = 2


class StreamLimitError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class StreamSlotRegistry:
    """Process-local connection accounting — not a distributed lease."""

    total_limit: int = MAX_CONNECTIONS_TOTAL
    per_credential_limit: int = MAX_CONNECTIONS_PER_CREDENTIAL
    _total: int = 0
    _by_cred: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self, credential_id: str) -> None:
        async with self._lock:
            if self._total >= self.total_limit:
                raise StreamLimitError("stream_limit_reached", "global stream limit reached")
            if self._by_cred[credential_id] >= self.per_credential_limit:
                raise StreamLimitError(
                    "stream_limit_reached", "per-credential stream limit reached"
                )
            self._total += 1
            self._by_cred[credential_id] += 1

    async def release(self, credential_id: str) -> None:
        async with self._lock:
            if self._by_cred[credential_id] > 0:
                self._by_cred[credential_id] -= 1
            if self._total > 0:
                self._total -= 1


def read_bounded_batch(
    engine: Engine,
    *,
    principal: AuthenticatedPrincipal,
    run_id: str,
    after_sequence: int,
    max_batch: int = MAX_BATCH_EVENTS,
) -> EventBatch:
    """Short sync read — caller must not hold Session across await."""
    max_batch = max(1, min(int(max_batch), MAX_BATCH_EVENTS))
    with engine.connect() as conn:
        with Session(bind=conn, autoflush=False, expire_on_commit=False) as session:
            authorize_run_read(session, principal, run_id)
            window_read = read_after_in_window(
                session,
                run_id=run_id,
                after_sequence=after_sequence,
                limit=max_batch,
            )
            if window_read.status in {
                "cursor_expired",
                "cursor_ahead",
                "gap_detected",
            }:
                return EventBatch.model_validate(
                    {
                        "events": [],
                        "nextAfterSequence": after_sequence,
                        "resyncRequired": True,
                        "reason": window_read.reason or window_read.status,
                        "encodedBytes": 0,
                    }
                )
            if window_read.status == "caught_up":
                return EventBatch.model_validate(
                    {
                        "events": [],
                        "nextAfterSequence": after_sequence,
                        "resyncRequired": False,
                        "encodedBytes": 0,
                    }
                )
            events: list[PublicEventEnvelope] = []
            encoded = 0
            next_seq = after_sequence
            for row in window_read.events:
                try:
                    projected = project_public_event(
                        event_id=row.event_id,
                        run_id=row.run_id,
                        sequence=int(row.sequence),
                        event_type=row.type,
                        timestamp=row.created_at,
                        payload=dict(row.payload or {}),
                    )
                except ProjectionError as exc:
                    if exc.code == "unknown_event_type":
                        return EventBatch.model_validate(
                            {
                                "events": events,
                                "nextAfterSequence": next_seq,
                                "resyncRequired": True,
                                "reason": "unknown_event_type",
                                "encodedBytes": encoded,
                            }
                        )
                    raise
                frame = encode_business_event(projected)
                if events and encoded + len(frame) > MAX_BATCH_BYTES:
                    break
                if not events and len(frame) > MAX_BATCH_BYTES:
                    return EventBatch.model_validate(
                        {
                            "events": [],
                            "nextAfterSequence": after_sequence,
                            "resyncRequired": True,
                            "reason": "gap_detected",
                            "encodedBytes": 0,
                        }
                    )
                events.append(projected)
                encoded += len(frame)
                next_seq = int(row.sequence)
            return EventBatch.model_validate(
                {
                    "events": [e.model_dump(mode="json", by_alias=True) for e in events],
                    "nextAfterSequence": next_seq,
                    "resyncRequired": False,
                    "encodedBytes": encoded,
                }
            )


class EventStreamService:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        *,
        verifier: CredentialVerifier,
        slots: StreamSlotRegistry | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._verifier = verifier
        self._slots = slots or StreamSlotRegistry()

    def ensure_current_authorization(
        self,
        principal: AuthenticatedPrincipal,
        *,
        bearer_token: str | None,
    ) -> AuthenticatedPrincipal:
        """Re-verify credential; env tokens re-check digest, registry reloads revoke."""
        if bearer_token is None:
            # Ticket / internal principals — cannot re-check bearer; stream lifetime is the bound.
            return principal
        try:
            record = self._verifier.verify(bearer_token)
        except CredentialError as exc:
            raise StreamLimitError("auth_revoked", "credential revoked or expired") from exc
        if record.credential_id != principal.credential_id:
            raise StreamLimitError("auth_revoked", "credential identity changed")
        return AuthenticatedPrincipal(
            credential_id=record.credential_id,
            credential_version=record.version,
            actor_ref=principal.actor_ref,
            scopes=frozenset({record.scope}),
        )

    async def iter_frames(
        self,
        *,
        principal: AuthenticatedPrincipal,
        run_id: str,
        after_sequence: int,
        bearer_token: str | None = None,
        is_disconnected: Callable[[], Any] | None = None,
        max_batch: int = MAX_BATCH_EVENTS,
        max_stream_seconds: float = MAX_STREAM_SECONDS,
        manage_slot: bool = True,
    ) -> AsyncIterator[bytes]:
        if manage_slot:
            await self._slots.acquire(principal.credential_id)
        started = time.monotonic()
        cursor = after_sequence
        last_heartbeat = started
        try:
            while True:
                if is_disconnected is not None and await _maybe_await(is_disconnected()):
                    return
                if time.monotonic() - started >= max_stream_seconds:
                    frame = SseControlFrame.model_validate(
                        {
                            "event": "stream_limit_reached",
                            "reason": "stream_limit_reached",
                            "runId": run_id,
                            "message": "reconnect with cursor",
                        }
                    )
                    yield encode_control_frame(frame)
                    return
                try:
                    principal = self.ensure_current_authorization(
                        principal, bearer_token=bearer_token
                    )
                except StreamLimitError:
                    yield encode_control_frame(
                        SseControlFrame.model_validate(
                            {
                                "event": "auth_revoked",
                                "reason": "auth_revoked",
                                "runId": run_id,
                            }
                        )
                    )
                    return

                batch = await asyncio.to_thread(
                    read_bounded_batch,
                    self._engine,
                    principal=principal,
                    run_id=run_id,
                    after_sequence=cursor,
                    max_batch=max_batch,
                )
                if batch.resync_required:
                    yield encode_control_frame(
                        SseControlFrame.model_validate(
                            {
                                "event": "resync_required",
                                "reason": batch.reason or "cursor_expired",
                                "snapshotUrl": f"/api/v1/runs/{run_id}/snapshot",
                                "runId": run_id,
                            }
                        )
                    )
                    return
                for event in batch.events:
                    yield encode_business_event(event)
                    cursor = int(event.sequence)
                now = time.monotonic()
                if not batch.events:
                    if now - last_heartbeat >= HEARTBEAT_SECONDS:
                        yield encode_heartbeat()
                        last_heartbeat = now
                    await asyncio.sleep(POLL_SECONDS)
        finally:
            if manage_slot:
                await self._slots.release(principal.credential_id)


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value
