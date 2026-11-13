"""WakeSignal recording — same transaction as domain facts + outbox."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from agentforge.contracts.collaboration import WakeSignal, WakeSourceKind
from agentforge.driver.repository import DriverRepository
from agentforge.storage.event_log import append_fact
from agentforge.storage.models import Outbox, RunWakeSignalRow
from agentforge.storage.uow import db_now

# Sources that may wake the Driver. Heartbeats / wake_recorded are excluded.
WAKE_SOURCE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "plan_confirmation",
        "instance_operation",
        "delivery",
        "stage_gate",
        "stage_submission",
        "review_decision",
        "user_decision",
        "control",
        "recovery",
        "authorization",
        "timer",
        "sweep",
        "native_event",
    }
)


def _now(session: Session) -> datetime:
    try:
        value = db_now(session)
    except Exception:
        return datetime.now(UTC)
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=UTC)
    return value


def _to_contract(row: RunWakeSignalRow) -> WakeSignal:
    return WakeSignal.model_validate(
        {
            "wakeSignalId": row.id,
            "runId": row.run_id,
            "sourceKind": row.source_kind,
            "sourceRef": row.source_ref,
            "sourceSequence": row.source_sequence,
            "bindingJson": row.binding_json or {},
            "priority": row.priority,
            "status": row.status,
            "claimOwner": row.claim_owner,
            "claimExpiresAt": row.claim_expires_at,
            "createdAt": row.created_at,
        }
    )


def record_wake_signal(
    session: Session,
    *,
    run_id: str,
    source_kind: WakeSourceKind | str,
    source_ref: str,
    binding: dict[str, Any] | None = None,
    source_sequence: int | None = None,
    priority: int = 100,
    emit_event: bool = True,
    emit_outbox: bool = True,
) -> WakeSignal:
    """Idempotent wake by (run_id, source_kind, source_ref). Safe to call after domain write."""
    if source_kind not in WAKE_SOURCE_ALLOWLIST:
        raise ValueError(f"wake source_kind not allowlisted: {source_kind}")

    repo = DriverRepository(session)
    existing = repo.find_wake(run_id=run_id, source_kind=str(source_kind), source_ref=source_ref)
    if existing is not None:
        if existing.status == "obsolete":
            existing.status = "pending"
            existing.claim_owner = None
            existing.claim_expires_at = None
        if emit_outbox and existing.status == "pending":
            _ensure_wake_outbox(session, existing)
        return _to_contract(existing)

    row = RunWakeSignalRow(
        id=f"wake_{uuid.uuid4().hex[:16]}",
        run_id=run_id,
        source_kind=str(source_kind),
        source_ref=source_ref,
        source_sequence=source_sequence,
        binding_json=dict(binding or {}),
        priority=priority,
        status="pending",
        created_at=_now(session),
    )
    repo.add_wake(row)
    session.flush()

    if emit_event:
        try:
            append_fact(
                session,
                run_id,
                "driver.wake_recorded",
                {
                    "wakeSignalId": row.id,
                    "sourceKind": row.source_kind,
                    "sourceRef": row.source_ref,
                },
            )
        except (AssertionError, AttributeError, ValueError):
            # Mem/unit ledgers may not support event append; wake row still persists.
            pass
    if emit_outbox:
        _ensure_wake_outbox(session, row)
    return _to_contract(row)


def _ensure_wake_outbox(session: Session, row: RunWakeSignalRow) -> None:
    from sqlalchemy import select

    key = f"driver.wake:{row.id}"
    for obj in getattr(session, "added", []):
        if isinstance(obj, Outbox) and obj.idempotency_key == key:
            return
    try:
        existing = session.scalar(select(Outbox).where(Outbox.idempotency_key == key))
    except Exception:
        # Unit/mem sessions may reject SQL; fall through to in-memory add.
        existing = None
    if existing is not None:
        return
    session.add(
        Outbox(
            id=f"obx_{uuid.uuid4().hex[:16]}",
            run_id=row.run_id,
            event_id=None,
            intent_type="driver.wake",
            target="driver",
            payload={
                "operationId": row.id,
                "runId": row.run_id,
                "wakeSignalId": row.id,
                "taskVersion": 1,
            },
            idempotency_key=key,
            delivery_status="pending",
            attempt_count=0,
            created_at=_now(session),
            updated_at=_now(session),
        )
    )


def claim_pending_signals(
    session: Session,
    *,
    run_id: str,
    owner: str,
    claim_ttl_seconds: int = 30,
    limit: int = 32,
) -> list[WakeSignal]:
    from datetime import timedelta

    expires = _now(session) + timedelta(seconds=claim_ttl_seconds)
    rows = DriverRepository(session).claim_pending_wakes(
        run_id=run_id, owner=owner, claim_expires_at=expires, limit=limit
    )
    return [_to_contract(r) for r in rows]


def complete_observed_signals(session: Session, wake_ids: list[str]) -> int:
    return DriverRepository(session).complete_wakes(wake_ids)
