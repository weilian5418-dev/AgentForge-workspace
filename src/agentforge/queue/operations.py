"""Async operation ledger: accept → dispatch intent → terminal/unknown (D02)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.orm import Session

from agentforge.contracts.runs import OperationAccepted, OperationStatusResponse
from agentforge.queue.constants import INTENT_MODEL_PROBE, TASK_MODEL_PROBE, TASK_VERSION
from agentforge.storage.models import AsyncOperationRow, Outbox, ProviderProfileRow


class OperationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def accept_model_probe(
    session: Session,
    *,
    provider_id: str,
    grant_id: str,
    probe_tools: bool = False,
) -> OperationAccepted:
    provider = session.get(ProviderProfileRow, provider_id)
    if provider is None or provider.status != "enabled":
        raise OperationError("provider_not_found", "Provider not available")

    operation_id = f"op_{uuid.uuid4().hex[:20]}"
    outbox_id = f"obx_{uuid.uuid4().hex[:20]}"
    payload = {
        "taskType": TASK_MODEL_PROBE,
        "taskVersion": TASK_VERSION,
        "operationId": operation_id,
        "providerId": provider_id,
        "grantId": grant_id,
        "providerConfigVersion": provider.config_version,
        "probeTools": probe_tools,
    }
    operation = AsyncOperationRow(
        id=operation_id,
        operation_type=TASK_MODEL_PROBE,
        status="accepted",
        provider_id=provider_id,
        grant_id=grant_id,
        provider_config_version=provider.config_version,
        outbox_id=outbox_id,
        uncertain_side_effects=False,
        message="Probe accepted; awaiting durable dispatch",
        payload=payload,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    outbox = Outbox(
        id=outbox_id,
        run_id=None,
        event_id=None,
        intent_type=INTENT_MODEL_PROBE,
        target="queue:model",
        payload={
            "operationId": operation_id,
            "taskType": TASK_MODEL_PROBE,
            "taskVersion": TASK_VERSION,
            "providerConfigVersion": provider.config_version,
        },
        idempotency_key=f"op:{operation_id}",
        delivery_status="pending",
        attempt_count=0,
    )
    session.add(operation)
    session.add(outbox)
    session.flush()
    return OperationAccepted(operation_id=operation_id, status="accepted")


def get_operation(session: Session, operation_id: str) -> OperationStatusResponse:
    row = session.get(AsyncOperationRow, operation_id)
    if row is None:
        from sqlalchemy import select

        from agentforge.storage.models import (
            ExecutionRequestRow,
            InstanceOperationRow,
            StageRow,
            StageSubmissionRow,
        )

        from agentforge.storage.models import RecoveryCaseRow, RecoveryDecisionRow

        native = session.get(InstanceOperationRow, operation_id)
        if native:
            status = {
                "prepared": "accepted",
                "claimed": "running",
                "unknown": "needs_reconciliation",
            }.get(native.status, native.status)
            return OperationStatusResponse(
                operation_id=native.id,
                operation_type="instance." + native.kind,
                status=cast(Any, status),
                result_ref=native.native_ref,
                uncertain_side_effects=native.status in {"unknown", "needs_reconciliation"},
                message=native.error_code,
                created_at=native.prepared_at,
                updated_at=native.completed_at or native.prepared_at,
            )
        decision = session.get(RecoveryDecisionRow, operation_id)
        if decision is not None:
            case = session.get(RecoveryCaseRow, decision.case_id)
            return OperationStatusResponse(
                operation_id=decision.id,
                operation_type="recovery.decision",
                run_id=case.run_id if case else None,
                status="succeeded" if case and case.status == "applied" else "accepted",
                result_ref=decision.case_id,
                uncertain_side_effects=False,
                message=decision.decision,
                created_at=decision.created_at,
                updated_at=decision.created_at,
            )
        case_row = session.get(RecoveryCaseRow, operation_id)
        if case_row is not None:
            status = {
                "open": "accepted",
                "observing": "running",
                "awaiting_decision": "waiting_user",
                "applying": "running",
                "blocked": "blocked",
                "applied": "succeeded",
                "superseded": "canceled",
                "canceled": "canceled",
            }.get(case_row.status, "running")
            return OperationStatusResponse(
                operation_id=case_row.id,
                operation_type="recovery.case",
                run_id=case_row.run_id,
                status=cast(Any, status),
                result_ref=case_row.id,
                uncertain_side_effects=case_row.status == "blocked",
                message=case_row.proposed_action,
                created_at=case_row.created_at,
                updated_at=case_row.updated_at,
            )
        request = session.get(ExecutionRequestRow, operation_id)
        if request:
            return OperationStatusResponse(
                operation_id=request.id,
                operation_type="execution.prepare",
                run_id=request.run_id,
                status=cast(Any, "accepted" if request.status == "prepared" else request.status),
                uncertain_side_effects=False,
                created_at=request.created_at,
                updated_at=request.created_at,
            )
        submission = session.scalar(
            select(StageSubmissionRow).where(StageSubmissionRow.operation_id == operation_id)
        )
        if submission:
            stage = session.get(StageRow, submission.stage_id)
            return OperationStatusResponse(
                operation_id=operation_id,
                operation_type="stage.submission",
                run_id=stage.run_id if stage else None,
                status="blocked"
                if stage and stage.status == "blocked"
                else "succeeded"
                if submission.manifest_hash
                else "running",
                result_ref=submission.id,
                uncertain_side_effects=stage is None
                or stage.waiting_reason == "awaiting_stop_verification",
                created_at=submission.created_at,
                updated_at=stage.updated_at if stage else submission.created_at,
            )
        raise OperationError("operation_not_found", "Operation not found")
    return OperationStatusResponse(
        operation_id=row.id,
        operation_type=row.operation_type,
        status=row.status,  # type: ignore[arg-type]
        run_id=row.run_id,
        result_ref=row.result_ref,
        uncertain_side_effects=bool(row.uncertain_side_effects),
        message=row.message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def mark_running(session: Session, operation_id: str) -> AsyncOperationRow:
    row = session.get(AsyncOperationRow, operation_id)
    if row is None:
        raise OperationError("operation_not_found", "Operation not found")
    if row.status in {"succeeded", "failed", "canceled", "needs_reconciliation"}:
        return row
    row.status = "running"
    row.updated_at = datetime.now(UTC)
    session.flush()
    return row


def complete_operation(
    session: Session,
    operation_id: str,
    *,
    status: str,
    result_ref: str | None,
    message: str | None,
    uncertain: bool = False,
) -> AsyncOperationRow:
    row = session.get(AsyncOperationRow, operation_id)
    if row is None:
        raise OperationError("operation_not_found", "Operation not found")
    # Terminal success/failure are sticky for idempotent worker retries.
    if row.status == "succeeded":
        return row
    if row.status == "needs_reconciliation" and status != "succeeded":
        return row
    row.status = status
    row.result_ref = result_ref
    row.message = message
    row.uncertain_side_effects = uncertain
    row.updated_at = datetime.now(UTC)
    session.flush()
    return row


def request_manual_replay(session: Session, operation_id: str) -> AsyncOperationRow:
    """Re-queue only when ledger proves no unknown dispatched side effect."""
    row = session.get(AsyncOperationRow, operation_id)
    if row is None:
        raise OperationError("operation_not_found", "Operation not found")
    if row.uncertain_side_effects or row.status == "needs_reconciliation":
        raise OperationError(
            "replay_blocked",
            "Operation has uncertain side effects; reconcile before replay",
        )
    if row.status not in {"failed", "blocked", "accepted"}:
        raise OperationError(
            "replay_blocked",
            f"Cannot replay operation in status={row.status}",
        )
    outbox_id = f"obx_{uuid.uuid4().hex[:20]}"
    outbox = Outbox(
        id=outbox_id,
        run_id=None,
        event_id=None,
        intent_type=INTENT_MODEL_PROBE,
        target="queue:model",
        payload={
            "operationId": row.id,
            "taskType": TASK_MODEL_PROBE,
            "taskVersion": TASK_VERSION,
            "providerConfigVersion": row.provider_config_version,
            "replay": True,
        },
        idempotency_key=f"op:{row.id}:replay:{row.replay_count + 1}",
        delivery_status="pending",
        attempt_count=0,
    )
    row.outbox_id = outbox_id
    row.replay_count = int(row.replay_count) + 1
    row.status = "accepted"
    row.message = "Manual replay accepted"
    row.updated_at = datetime.now(UTC)
    session.add(outbox)
    session.flush()
    return row
