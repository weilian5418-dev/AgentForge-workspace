"""Persistence helpers for driver threads, waits, wakes, and scheduler operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agentforge.storage.models import (
    DriverThreadRow,
    RunWaitRow,
    RunWakeSignalRow,
    SchedulerOperationRow,
)


class DriverRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # --- threads ---

    def get_thread_by_id(self, thread_id: str) -> DriverThreadRow | None:
        return self.session.scalar(
            select(DriverThreadRow).where(DriverThreadRow.thread_id == thread_id)
        )

    def get_active_thread(self, run_id: str) -> DriverThreadRow | None:
        return self.session.scalar(
            select(DriverThreadRow).where(
                DriverThreadRow.run_id == run_id, DriverThreadRow.status == "active"
            )
        )

    def add_thread(self, row: DriverThreadRow) -> DriverThreadRow:
        self.session.add(row)
        return row

    # --- waits ---

    def get_wait(self, wait_id: str) -> RunWaitRow | None:
        return self.session.get(RunWaitRow, wait_id)

    def get_wait_by_key(self, run_id: str, wait_key: str) -> RunWaitRow | None:
        return self.session.scalar(
            select(RunWaitRow).where(
                RunWaitRow.run_id == run_id, RunWaitRow.wait_key == wait_key
            )
        )

    def list_open_waits(self, run_id: str) -> list[RunWaitRow]:
        return list(
            self.session.scalars(
                select(RunWaitRow).where(
                    RunWaitRow.run_id == run_id, RunWaitRow.status == "open"
                )
            ).all()
        )

    def add_wait(self, row: RunWaitRow) -> RunWaitRow:
        self.session.add(row)
        return row

    def list_due_open_waits(self, *, now: datetime, limit: int = 100) -> list[RunWaitRow]:
        return list(
            self.session.scalars(
                select(RunWaitRow)
                .where(
                    RunWaitRow.status == "open",
                    RunWaitRow.deadline_at.is_not(None),
                    RunWaitRow.deadline_at <= now,
                )
                .order_by(RunWaitRow.deadline_at)
                .limit(limit)
            ).all()
        )

    # --- wakes ---

    def get_wake(self, wake_id: str) -> RunWakeSignalRow | None:
        return self.session.get(RunWakeSignalRow, wake_id)

    def find_wake(
        self, *, run_id: str, source_kind: str, source_ref: str
    ) -> RunWakeSignalRow | None:
        for obj in getattr(self.session, "added", []):
            if (
                isinstance(obj, RunWakeSignalRow)
                and obj.run_id == run_id
                and obj.source_kind == source_kind
                and obj.source_ref == source_ref
            ):
                return obj
        try:
            return self.session.scalar(
                select(RunWakeSignalRow).where(
                    RunWakeSignalRow.run_id == run_id,
                    RunWakeSignalRow.source_kind == source_kind,
                    RunWakeSignalRow.source_ref == source_ref,
                )
            )
        except Exception:
            return None

    def add_wake(self, row: RunWakeSignalRow) -> RunWakeSignalRow:
        self.session.add(row)
        return row

    def claim_pending_wakes(
        self,
        *,
        run_id: str,
        owner: str,
        claim_expires_at: datetime,
        limit: int = 32,
    ) -> list[RunWakeSignalRow]:
        rows = list(
            self.session.scalars(
                select(RunWakeSignalRow)
                .where(
                    RunWakeSignalRow.run_id == run_id,
                    RunWakeSignalRow.status == "pending",
                )
                .order_by(RunWakeSignalRow.priority.asc(), RunWakeSignalRow.created_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
        )
        for row in rows:
            row.status = "claimed"
            row.claim_owner = owner
            row.claim_expires_at = claim_expires_at
        return rows

    def complete_wakes(self, wake_ids: list[str]) -> int:
        if not wake_ids:
            return 0
        result = self.session.execute(
            update(RunWakeSignalRow)
            .where(
                RunWakeSignalRow.id.in_(wake_ids),
                RunWakeSignalRow.status.in_(("pending", "claimed")),
            )
            .values(status="completed")
        )
        return int(result.rowcount or 0)

    def mark_wake_obsolete(self, wake_id: str) -> None:
        row = self.get_wake(wake_id)
        if row is not None and row.status in ("pending", "claimed"):
            row.status = "obsolete"

    def list_pending_wakes(self, *, limit: int = 100) -> list[RunWakeSignalRow]:
        return list(
            self.session.scalars(
                select(RunWakeSignalRow)
                .where(RunWakeSignalRow.status == "pending")
                .order_by(RunWakeSignalRow.created_at.asc())
                .limit(limit)
            ).all()
        )

    # --- scheduler operations ---

    def find_operation(self, run_id: str, operation_key: str) -> SchedulerOperationRow | None:
        return self.session.scalar(
            select(SchedulerOperationRow).where(
                SchedulerOperationRow.run_id == run_id,
                SchedulerOperationRow.operation_key == operation_key,
            )
        )

    def add_operation(self, row: SchedulerOperationRow) -> SchedulerOperationRow:
        self.session.add(row)
        return row

    def get_operation(self, operation_id: str) -> SchedulerOperationRow | None:
        return self.session.get(SchedulerOperationRow, operation_id)
