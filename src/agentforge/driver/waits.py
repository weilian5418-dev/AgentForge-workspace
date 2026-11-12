"""Persistent wait registration — reason + versioned scope, no arbitrary SQL."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.collaboration import LeaseFence, RunWait, WaitReason
from agentforge.driver.fencing import assert_driver_fence
from agentforge.driver.repository import DriverRepository
from agentforge.storage.event_log import append_fact
from agentforge.storage.models import (
    InstanceAttemptRow,
    NativeSessionRow,
    Plan,
    PlanConfirmationRequestRow,
    ReleaseRecordRow,
    Run,
    RunWaitRow,
)
from agentforge.storage.uow import db_now

PredicateFn = Callable[[Session, str, dict[str, Any]], bool]


def _now(session: Session) -> datetime:
    value = db_now(session)
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=UTC)  # type: ignore[union-attr]
    return value  # type: ignore[return-value]


def _pred_plan_confirmation(session: Session, run_id: str, scope: dict[str, Any]) -> bool:
    run = session.get(Run, run_id)
    if run is None or run.plan_version is None:
        return False
    plan = session.scalar(
        select(Plan).where(Plan.run_id == run_id, Plan.version == run.plan_version)
    )
    if plan is None or plan.status != "approved":
        return False
    open_req = session.scalar(
        select(PlanConfirmationRequestRow).where(
            PlanConfirmationRequestRow.run_id == run_id,
            PlanConfirmationRequestRow.plan_version == run.plan_version,
            PlanConfirmationRequestRow.status == "pending",
        )
    )
    return open_req is None


def _pred_session_ready(session: Session, run_id: str, scope: dict[str, Any]) -> bool:
    instance_id = scope.get("instanceId") or scope.get("instance_id")
    if not instance_id:
        return False
    row = session.scalar(
        select(NativeSessionRow).where(
            NativeSessionRow.instance_id == str(instance_id),
            NativeSessionRow.binding_status == "active",
        )
    )
    return row is not None


def _pred_task_submission(session: Session, run_id: str, scope: dict[str, Any]) -> bool:
    attempt_id = scope.get("attemptId") or scope.get("attempt_id")
    if not attempt_id:
        return False
    attempt = session.get(InstanceAttemptRow, str(attempt_id))
    return attempt is not None and attempt.status in (
        "submitted",
        "awaiting_stop_verification",
        "stopped",
    )


def _pred_dependency(session: Session, run_id: str, scope: dict[str, Any]) -> bool:
    release_ids = scope.get("releaseRecordIds") or scope.get("release_record_ids") or []
    if not release_ids:
        return False
    for rid in release_ids:
        if session.get(ReleaseRecordRow, str(rid)) is None:
            return False
    return True


def _pred_driver_yield(_session: Session, _run_id: str, _scope: dict[str, Any]) -> bool:
    # Internal yield barrier — satisfied by matching wake, not domain state.
    return False


def _pred_always_false(_session: Session, _run_id: str, _scope: dict[str, Any]) -> bool:
    return False


PREDICATE_REGISTRY: dict[str, PredicateFn] = {
    "plan_confirmation": _pred_plan_confirmation,
    "workspace_ready": _pred_always_false,  # filled when workspace coordinator lands
    "session_ready": _pred_session_ready,
    "task_submission": _pred_task_submission,
    "native_approval": _pred_always_false,
    "stage_review": _pred_always_false,
    "user_gate_decision": _pred_always_false,
    "stop_verification": _pred_always_false,
    "recovery_evidence": _pred_always_false,
    "capacity": _pred_always_false,
    "dependency": _pred_dependency,
    "driver_yield": _pred_driver_yield,
}

PREDICATE_VERSION = 1


@dataclass(frozen=True)
class WaitOutcome:
    status: Literal["satisfied", "waiting"]
    wait: RunWait | None = None


def evaluate_wait_predicate(
    session: Session, *, run_id: str, reason: WaitReason | str, scope: dict[str, Any]
) -> bool:
    fn = PREDICATE_REGISTRY.get(str(reason))
    if fn is None:
        raise ValueError(f"unregistered wait reason: {reason}")
    return bool(fn(session, run_id, scope))


def _to_contract(row: RunWaitRow) -> RunWait:
    return RunWait.model_validate(
        {
            "waitId": row.id,
            "runId": row.run_id,
            "threadId": row.thread_id,
            "waitKey": row.wait_key,
            "reason": row.reason,
            "scopeJson": row.scope_json or {},
            "predicateVersion": row.predicate_version,
            "afterSequence": row.after_sequence,
            "status": row.status,
            "deadlineAt": row.deadline_at,
            "resolvedByRef": row.resolved_by_ref,
            "createdAt": row.created_at,
            "resolvedAt": row.resolved_at,
        }
    )


class WaitService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = DriverRepository(session)

    def register_or_observe(
        self,
        fence: LeaseFence,
        *,
        wait_key: str,
        reason: WaitReason | str,
        scope: dict[str, Any],
        predicate_version: int = PREDICATE_VERSION,
        deadline_at: datetime | None = None,
        thread_id: str | None = None,
    ) -> WaitOutcome:
        assert_driver_fence(
            self.session, fence, require_active_thread=fence.thread_id is not None
        )
        run = self.session.get(Run, fence.run_id, with_for_update=True)
        if run is None:
            raise ValueError(f"run {fence.run_id} missing")

        if evaluate_wait_predicate(
            self.session, run_id=fence.run_id, reason=reason, scope=scope
        ):
            return WaitOutcome(status="satisfied", wait=None)

        existing = self.repo.get_wait_by_key(fence.run_id, wait_key)
        if existing is not None and existing.status == "open":
            return WaitOutcome(status="waiting", wait=_to_contract(existing))

        if existing is not None and existing.status != "open":
            # Supersede prior terminal wait with same key by reopening only when allowed.
            existing.status = "superseded"
            existing.resolved_at = _now(self.session)

        row = RunWaitRow(
            id=f"wait_{uuid.uuid4().hex[:16]}",
            run_id=fence.run_id,
            thread_id=thread_id or fence.thread_id,
            wait_key=wait_key,
            reason=str(reason),
            scope_json=dict(scope),
            predicate_version=predicate_version,
            after_sequence=int(run.last_sequence or 0),
            status="open",
            deadline_at=deadline_at,
            created_at=_now(self.session),
        )
        self.repo.add_wait(row)
        self.session.flush()
        append_fact(
            self.session,
            fence.run_id,
            "driver.wait_registered",
            {
                "waitId": row.id,
                "reason": row.reason,
                "threadId": row.thread_id,
            },
        )
        return WaitOutcome(status="waiting", wait=_to_contract(row))

    def resolve_wait(
        self,
        wait_id: str,
        *,
        resolved_by_ref: str,
        status: Literal["resolved", "canceled", "superseded"] = "resolved",
    ) -> RunWait:
        row = self.repo.get_wait(wait_id)
        if row is None:
            raise ValueError(f"wait {wait_id} not found")
        if row.status != "open":
            return _to_contract(row)
        row.status = status
        row.resolved_by_ref = resolved_by_ref
        row.resolved_at = _now(self.session)
        return _to_contract(row)

    def get_bound_wait(self, wait_ref: str) -> RunWait:
        row = self.repo.get_wait(wait_ref)
        if row is None:
            raise ValueError(f"wait {wait_ref} not found")
        return _to_contract(row)

    def recheck_open_wait(self, wait_id: str) -> WaitOutcome:
        row = self.repo.get_wait(wait_id)
        if row is None:
            raise ValueError(f"wait {wait_id} not found")
        if row.status != "open":
            return WaitOutcome(status="satisfied", wait=_to_contract(row))
        if evaluate_wait_predicate(
            self.session,
            run_id=row.run_id,
            reason=row.reason,
            scope=dict(row.scope_json or {}),
        ):
            return WaitOutcome(
                status="satisfied",
                wait=self.resolve_wait(row.id, resolved_by_ref="predicate"),
            )
        return WaitOutcome(status="waiting", wait=_to_contract(row))
