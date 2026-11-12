"""Instance / session / operation persistence — no transaction ownership."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.instances.errors import InstanceError
from agentforge.storage.models import (
    AgentInstanceRow,
    ExecutionRequestRow,
    InstanceAttemptRow,
    InstanceOperationRow,
    NativeEventReceiptRow,
    NativeSessionRow,
    ProtocolRequestRow,
    RoleAssignmentRow,
)


def _now() -> datetime:
    return datetime.now(UTC)


class InstanceRepository:
    """Data access only. Callers own UoW / short transactions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_instance(self, instance_id: str) -> AgentInstanceRow | None:
        return self.session.get(AgentInstanceRow, instance_id)

    def list_instances_for_run(self, run_id: str) -> list[AgentInstanceRow]:
        return list(
            self.session.scalars(
                select(AgentInstanceRow)
                .where(AgentInstanceRow.run_id == run_id)
                .order_by(AgentInstanceRow.created_at.asc())
            ).all()
        )

    def find_instance(
        self, *, run_id: str, assignment_id: str, role_id: str
    ) -> AgentInstanceRow | None:
        row = self.session.scalar(
            select(AgentInstanceRow).where(
                AgentInstanceRow.run_id == run_id,
                AgentInstanceRow.assignment_id == assignment_id,
                AgentInstanceRow.role_id == role_id,
            )
        )
        if row is not None and (
            row.run_id != run_id or row.assignment_id != assignment_id or row.role_id != role_id
        ):
            row = None
        if row is not None:
            return row
        for obj in getattr(self.session, "added", []):
            if (
                isinstance(obj, AgentInstanceRow)
                and obj.run_id == run_id
                and obj.assignment_id == assignment_id
                and obj.role_id == role_id
            ):
                return obj
        return None

    def add_instance(self, row: AgentInstanceRow) -> AgentInstanceRow:
        self.session.add(row)
        return row

    def list_assignments(self, *, run_id: str, assignment_version: int) -> list[RoleAssignmentRow]:
        rows = list(
            self.session.scalars(
                select(RoleAssignmentRow).where(
                    RoleAssignmentRow.run_id == run_id,
                    RoleAssignmentRow.assignment_version == assignment_version,
                )
            ).all()
        )
        filtered = [
            r for r in rows if r.run_id == run_id and r.assignment_version == assignment_version
        ]
        if filtered:
            return filtered
        return [
            obj
            for obj in getattr(self.session, "added", [])
            if isinstance(obj, RoleAssignmentRow)
            and obj.run_id == run_id
            and obj.assignment_version == assignment_version
        ]

    def get_execution_request(
        self, *, run_id: str, idempotency_key: str
    ) -> ExecutionRequestRow | None:
        return self.session.scalar(
            select(ExecutionRequestRow).where(
                ExecutionRequestRow.run_id == run_id,
                ExecutionRequestRow.idempotency_key == idempotency_key,
            )
        )

    def add_execution_request(self, row: ExecutionRequestRow) -> ExecutionRequestRow:
        self.session.add(row)
        return row

    def add_operation(self, row: InstanceOperationRow) -> InstanceOperationRow:
        self.session.add(row)
        return row

    def get_operation(self, operation_id: str) -> InstanceOperationRow | None:
        return self.session.get(InstanceOperationRow, operation_id)

    def find_operation_by_idempotency(
        self, *, instance_id: str, kind: str, idempotency_key: str
    ) -> InstanceOperationRow | None:
        return self.session.scalar(
            select(InstanceOperationRow).where(
                InstanceOperationRow.instance_id == instance_id,
                InstanceOperationRow.kind == kind,
                InstanceOperationRow.idempotency_key == idempotency_key,
            )
        )

    def claim_operations(self, **kwargs: Any) -> Any:
        raise InstanceError(
            "engine_coordinator_required",
            "Use durable instances.worker, not transaction-bound claim helpers",
        )

    def add_native_session(self, row: NativeSessionRow) -> NativeSessionRow:
        self.session.add(row)
        return row

    def get_native_session(self, session_id: str) -> NativeSessionRow | None:
        return self.session.get(NativeSessionRow, session_id)

    def active_session_for_instance(self, instance_id: str) -> NativeSessionRow | None:
        return self.session.scalar(
            select(NativeSessionRow).where(
                NativeSessionRow.instance_id == instance_id,
                NativeSessionRow.binding_status == "active",
            )
        )

    def session_by_operation(self, operation_id: str) -> NativeSessionRow | None:
        row = self.session.scalar(
            select(NativeSessionRow).where(NativeSessionRow.create_operation_id == operation_id)
        )
        if row is not None and row.create_operation_id != operation_id:
            row = None
        if row is not None:
            return row
        for obj in getattr(self.session, "added", []):
            if isinstance(obj, NativeSessionRow) and obj.create_operation_id == operation_id:
                return obj
        return None

    def add_attempt(self, row: InstanceAttemptRow) -> InstanceAttemptRow:
        self.session.add(row)
        return row

    def get_attempt(self, attempt_id: str) -> InstanceAttemptRow | None:
        return self.session.get(InstanceAttemptRow, attempt_id)

    def add_protocol_request(self, row: ProtocolRequestRow) -> ProtocolRequestRow:
        self.session.add(row)
        return row

    def get_protocol_request(self, request_id: str) -> ProtocolRequestRow | None:
        return self.session.get(
            ProtocolRequestRow, request_id, with_for_update=True, populate_existing=True
        )

    def pending_protocol_request(self, request_id: str) -> ProtocolRequestRow | None:
        row = self.get_protocol_request(request_id)
        if row is None or row.status != "pending":
            return row
        return row

    def add_event_receipt(self, row: NativeEventReceiptRow) -> NativeEventReceiptRow:
        self.session.add(row)
        return row

    def find_event_receipt(
        self, *, instance_id: str, source: str, dedupe_key: str
    ) -> NativeEventReceiptRow | None:
        row = self.session.scalar(
            select(NativeEventReceiptRow).where(
                NativeEventReceiptRow.instance_id == instance_id,
                NativeEventReceiptRow.source == source,
                NativeEventReceiptRow.dedupe_key == dedupe_key,
            )
        )
        if row is not None and (
            row.instance_id != instance_id or row.source != source or row.dedupe_key != dedupe_key
        ):
            row = None
        if row is not None:
            return row
        for obj in getattr(self.session, "added", []):
            if (
                isinstance(obj, NativeEventReceiptRow)
                and obj.instance_id == instance_id
                and obj.source == source
                and obj.dedupe_key == dedupe_key
            ):
                return obj
        return None

    def latest_event_cursor(self, *, instance_id: str, source: str) -> str | None:
        row = self.session.scalar(
            select(NativeEventReceiptRow)
            .where(
                NativeEventReceiptRow.instance_id == instance_id,
                NativeEventReceiptRow.source == source,
                NativeEventReceiptRow.cursor.is_not(None),
            )
            .order_by(NativeEventReceiptRow.received_at.desc())
            .limit(1)
        )
        return row.cursor if row else None
