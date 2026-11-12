"""Read-only collaboration queries for API/CLI (D11.11) — no lease tokens."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentforge.contracts.collaboration import (
    DriverStatusResponse,
    DriverThread,
    RunWait,
    RunWaitListResponse,
)
from agentforge.driver.control import RunControlError
from agentforge.storage.models import DriverThreadRow, Run, RunWaitRow, RunWakeSignalRow


def _to_wait(row: RunWaitRow) -> RunWait:
    return RunWait.model_validate(
        {
            "waitId": row.id,
            "runId": row.run_id,
            "threadId": row.thread_id,
            "waitKey": row.wait_key,
            "reason": row.reason,
            "scopeJson": row.scope_json or {},
            "predicateVersion": row.predicate_version,
            "afterSequence": int(row.after_sequence or 0),
            "status": row.status,
            "deadlineAt": row.deadline_at,
            "resolvedByRef": row.resolved_by_ref,
            "createdAt": row.created_at,
            "resolvedAt": row.resolved_at,
        }
    )


def _to_thread(row: DriverThreadRow) -> DriverThread:
    return DriverThread.model_validate(
        {
            "threadRowId": row.id,
            "runId": row.run_id,
            "threadId": row.thread_id,
            "planVersion": row.plan_version,
            "assignmentVersion": row.assignment_version,
            "gatePolicyVersion": row.gate_policy_version,
            "executionGeneration": row.execution_generation,
            "graphVersion": row.graph_version,
            "stateSchemaVersion": row.state_schema_version,
            "checkpointEpoch": row.checkpoint_epoch,
            "bindingHash": row.binding_hash,
            "status": row.status,
            "latestCheckpointId": row.latest_checkpoint_id,
            "lastObservedSequence": int(row.last_observed_sequence or 0),
            "createdAt": row.created_at,
            "updatedAt": row.updated_at,
        }
    )


def list_run_waits(
    session: Session, run_id: str, *, limit: int = 100, open_only: bool = True
) -> RunWaitListResponse:
    run = session.get(Run, run_id)
    if run is None:
        raise RunControlError("run_not_found", run_id)
    stmt = select(RunWaitRow).where(RunWaitRow.run_id == run_id)
    if open_only:
        stmt = stmt.where(RunWaitRow.status == "open")
    stmt = stmt.order_by(RunWaitRow.created_at.desc()).limit(min(limit, 100))
    items = [_to_wait(row) for row in session.scalars(stmt).all()]
    return RunWaitListResponse(items=items)


def get_driver_status(session: Session, run_id: str) -> DriverStatusResponse:
    run = session.get(Run, run_id)
    if run is None:
        raise RunControlError("run_not_found", run_id)
    thread_row = session.scalar(
        select(DriverThreadRow).where(
            DriverThreadRow.run_id == run_id,
            DriverThreadRow.status == "active",
        )
    )
    open_waits = int(
        session.scalar(
            select(func.count())
            .select_from(RunWaitRow)
            .where(RunWaitRow.run_id == run_id, RunWaitRow.status == "open")
        )
        or 0
    )
    pending_wakes = int(
        session.scalar(
            select(func.count())
            .select_from(RunWakeSignalRow)
            .where(RunWakeSignalRow.run_id == run_id, RunWakeSignalRow.status == "pending")
        )
        or 0
    )
    thread = _to_thread(thread_row) if thread_row is not None else None
    return DriverStatusResponse.model_validate(
        {
            "runId": run_id,
            "collaborationState": run.collaboration_state,
            "thread": thread.model_dump(mode="json", by_alias=True) if thread else None,
            "lastObservedSequence": int(
                thread_row.last_observed_sequence if thread_row is not None else run.last_sequence or 0
            ),
            "openWaitCount": open_waits,
            "pendingWakeCount": pending_wakes,
            "graphVersion": thread_row.graph_version if thread_row is not None else None,
            "bindingHash": thread_row.binding_hash if thread_row is not None else None,
        }
    )
