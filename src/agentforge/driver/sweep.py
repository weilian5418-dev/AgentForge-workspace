"""Bounded sweeps: rebuild missing wake keys; timer wakes for overdue waits."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agentforge.driver.signals import record_wake_signal
from agentforge.driver.repository import DriverRepository
from agentforge.storage.models import InstanceOperationRow, Outbox, RunWakeSignalRow
from agentforge.storage.uow import db_now, unit_of_work_connection


@dataclass
class SweepReport:
    timer_wakes: int = 0
    operation_wakes: int = 0
    requeued_outbox: int = 0


def _now(session: Session) -> datetime:
    value = db_now(session)
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=UTC)  # type: ignore[union-attr]
    return value  # type: ignore[return-value]


def _requeue_wake_outbox(session: Session, wake: RunWakeSignalRow) -> bool:
    key = f"driver.wake:{wake.id}"
    existing = session.scalar(select(Outbox).where(Outbox.idempotency_key == key))
    if existing is not None:
        if existing.delivery_status in ("pending", "claimed", "failed"):
            # Reminder may be re-dispatched; do not invent a second native action.
            if existing.delivery_status == "failed":
                existing.delivery_status = "pending"
                existing.updated_at = _now(session)
                return True
            return False
        # Already dispatched — insert a fresh reminder with a sweep suffix key.
        key = f"driver.wake:{wake.id}:sweep:{uuid.uuid4().hex[:8]}"
    session.add(
        Outbox(
            id=f"obx_{uuid.uuid4().hex[:16]}",
            run_id=wake.run_id,
            event_id=None,
            intent_type="driver.wake",
            target="driver",
            payload={
                "operationId": wake.id,
                "runId": wake.run_id,
                "wakeSignalId": wake.id,
                "taskVersion": 1,
                "sweep": True,
            },
            idempotency_key=key,
            delivery_status="pending",
            attempt_count=0,
            created_at=_now(session),
            updated_at=_now(session),
        )
    )
    return True


def sweep_once(
    engine: Engine,
    *,
    limit: int = 50,
    stale_pending_minutes: int = 5,
) -> SweepReport:
    """One bounded maintenance pass — never re-executes native side effects."""
    report = SweepReport()
    with unit_of_work_connection(engine) as uow:
        session = uow.session
        repo = DriverRepository(session)
        now = _now(session)

        for wait in repo.list_due_open_waits(now=now, limit=limit):
            record_wake_signal(
                session,
                run_id=wait.run_id,
                source_kind="timer",
                source_ref=f"wait-deadline:{wait.id}",
                binding={"waitId": wait.id, "reason": wait.reason},
                priority=50,
            )
            report.timer_wakes += 1

        # Terminal instance operations without a matching wake → rebuild same dedupe key.
        terminal = list(
            session.scalars(
                select(InstanceOperationRow)
                .where(
                    InstanceOperationRow.status.in_(
                        ("succeeded", "failed", "unknown", "canceled")
                    )
                )
                .order_by(InstanceOperationRow.prepared_at.desc())
                .limit(limit)
            ).all()
        )
        for op in terminal:
            # Need run_id via instance — skip if not bound.
            run_id = getattr(op, "run_id", None)
            if not run_id:
                from agentforge.storage.models import AgentInstanceRow

                inst = session.get(AgentInstanceRow, op.instance_id)
                run_id = getattr(inst, "run_id", None) if inst else None
            if not run_id:
                continue
            version = op.completed_at.isoformat() if op.completed_at else op.status
            source_ref = f"{op.id}:{version}"
            before = repo.find_wake(
                run_id=str(run_id),
                source_kind="instance_operation",
                source_ref=source_ref,
            )
            record_wake_signal(
                session,
                run_id=str(run_id),
                source_kind="instance_operation",
                source_ref=source_ref,
                binding={"operationId": op.id, "status": op.status},
                priority=80,
            )
            if before is None:
                report.operation_wakes += 1

        cutoff = now - timedelta(minutes=stale_pending_minutes)
        pending = list(
            session.scalars(
                select(RunWakeSignalRow)
                .where(
                    RunWakeSignalRow.status == "pending",
                    RunWakeSignalRow.created_at <= cutoff,
                )
                .order_by(RunWakeSignalRow.created_at.asc())
                .limit(limit)
            ).all()
        )
        for wake in pending:
            if _requeue_wake_outbox(session, wake):
                report.requeued_outbox += 1

    return report


def sweep_report_dict(report: SweepReport) -> dict[str, Any]:
    return {
        "timerWakes": report.timer_wakes,
        "operationWakes": report.operation_wakes,
        "requeuedOutbox": report.requeued_outbox,
    }
