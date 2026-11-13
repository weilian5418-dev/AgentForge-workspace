"""Short fenced bridges from graph actions to domain services (D11.6+)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime
from langgraph.types import interrupt
from sqlalchemy.orm import Session

from agentforge.contracts.agents import ExecutionRequest
from agentforge.contracts.collaboration import LeaseFence, NextAction
from agentforge.driver.binding import build_operation_key, sha256_hex
from agentforge.driver.dispatch import DispatchService
from agentforge.driver.fencing import assert_driver_fence, run_fenced_transaction
from agentforge.driver.planner import choose_next_action
from agentforge.driver.repository import DriverRepository
from agentforge.driver.snapshots import SnapshotReader
from agentforge.driver.state import (
    CollaborationState,
    DriverContext,
    action_from_state,
    state_from_action,
)
from agentforge.driver.submissions import SubmissionBridge
from agentforge.driver.waits import WaitService
from agentforge.driver.workspace_preparation import ensure_workspace_operation
from agentforge.instances.errors import InstanceError
from agentforge.instances.preparation import ExecutionPreparationService
from agentforge.storage.event_log import append_fact
from agentforge.storage.models import Plan, Run, SchedulerOperationRow
from agentforge.storage.uow import db_now


def _now(session: Session | None = None) -> datetime:
    if session is not None:
        try:
            value = db_now(session)
            if getattr(value, "tzinfo", None) is None:
                return value.replace(tzinfo=UTC)
            return value
        except Exception:
            pass
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --- Domain bridges (session + fence) ---


def ensure_workspaces(
    session: Session,
    fence: LeaseFence,
    action: NextAction,
    *,
    state_dir: Path,
    role_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Register fixed-identity workspace prepare intents for needed roles."""
    run = session.get(Run, fence.run_id)
    if run is None:
        raise ValueError("run missing")
    roles = list(role_ids or [])
    if not roles:
        from sqlalchemy import select

        from agentforge.storage.models import RoleAssignmentRow

        assignments = list(
            session.scalars(
                select(RoleAssignmentRow).where(
                    RoleAssignmentRow.run_id == fence.run_id,
                    RoleAssignmentRow.assignment_version == run.assignment_version,
                )
            ).all()
        )
        roles = [a.role_id for a in assignments]

    results = []
    for role_id in roles:
        results.append(
            ensure_workspace_operation(
                session,
                fence,
                role_id=role_id,
                state_dir=state_dir,
            )
        )
    return {"status": "accepted", "operations": results}


def ensure_instances(
    session: Session,
    fence: LeaseFence,
    *,
    actor: str = "driver",
) -> dict[str, Any]:
    """Phase: write D09 ExecutionPreparationService intent — no session create here."""
    assert_driver_fence(session, fence, require_active_thread=fence.thread_id is not None)
    run = session.get(Run, fence.run_id, with_for_update=True)
    if run is None:
        raise ValueError("run missing")
    if (
        run.plan_version is None
        or run.assignment_version is None
        or run.gate_policy_version is None
    ):
        raise ValueError("run missing plan/assignment/gate versions")
    plan = session.get(Plan, (run.id, run.plan_version))
    if plan is None or not plan.plan_hash:
        raise ValueError("approved plan hash required")

    idem = (
        f"prepare:{run.id}:p{run.plan_version}:a{run.assignment_version}:g{run.active_generation}"
    )
    body = ExecutionRequest.model_validate(
        {
            "planHash": plan.plan_hash,
            "planVersion": run.plan_version,
            "assignmentVersion": run.assignment_version,
            "gatePolicyVersion": run.gate_policy_version,
        }
    )
    try:
        accepted = ExecutionPreparationService(session).prepare(
            run.id, body, actor=actor, idempotency_key=idem
        )
    except InstanceError as exc:
        return {"status": "blocked", "errorCode": exc.code, "message": exc.message}
    return {
        "status": "accepted",
        "operationId": accepted.operation_id,
        "note": "await session_ready; prepare 202 is not session ready",
    }


def dispatch_ready(
    session: Session,
    fence: LeaseFence,
    action: NextAction,
) -> dict[str, Any]:
    """Reserve attempt + create task.assignment (does not wait for native ack)."""
    if not action.work_package_id:
        raise ValueError("dispatch_ready requires work_package_id")
    try:
        return DispatchService(session).reserve_and_create_message(
            fence,
            action.work_package_id,
            stage_id=action.stage_id,
        )
    except InstanceError as exc:
        return {"status": "blocked", "errorCode": exc.code, "message": exc.message}


def accept_submission(
    session: Session,
    fence: LeaseFence,
    *,
    message_ref: str,
) -> dict[str, Any]:
    """Bridge a verified stage.submission message into StageService.submit."""
    assert_driver_fence(session, fence, require_active_thread=fence.thread_id is not None)
    try:
        return SubmissionBridge(session).accept(message_ref)
    except InstanceError as exc:
        return {"status": "blocked", "errorCode": exc.code, "message": exc.message}


def prepare_handoff(session: Session, fence: LeaseFence) -> dict[str, Any]:
    """Seal IntegrationHandoff when all required packages are released — D11 end."""
    assert_driver_fence(session, fence, require_active_thread=fence.thread_id is not None)
    run = session.get(Run, fence.run_id, with_for_update=True, populate_existing=True)
    if run is None:
        raise ValueError("run missing")
    if run.collaboration_state == "ready_for_integration":
        repo = DriverRepository(session)
        existing = repo.find_operation(
            run.id,
            build_operation_key(
                kind="integration_handoff",
                run_id=run.id,
                plan_version=int(run.plan_version or 0),
                assignment_version=int(run.assignment_version or 0),
                generation=int(run.active_generation),
            ),
        )
        return {
            "status": "succeeded",
            "handoffRef": (existing.result_refs or [None])[0] if existing else None,
            "note": "already_ready",
        }

    snap = SnapshotReader(session).load(run.id, fence)
    required = [wp.work_package_id for wp in snap.work_packages if wp.required]
    released = snap.released_work_packages()
    if not required or not all(wp in released for wp in required):
        return {
            "status": "blocked",
            "errorCode": "handoff_incomplete",
            "message": "not all released",
        }

    release_ids = [r.release_id for r in snap.releases]
    approved_plan = session.get(Plan, (run.id, run.plan_version))
    if approved_plan is None:
        raise ValueError("Approved plan missing")
    body = {
        "runId": run.id,
        "planVersion": run.plan_version,
        "assignmentVersion": run.assignment_version,
        "gatePolicyVersion": run.gate_policy_version,
        "executionGeneration": run.active_generation,
        "releaseRecordIds": sorted(release_ids),
        "candidateSetHashes": [],
        "contractHash": approved_plan.contract_hash,
        "environmentFingerprint": "0" * 64,
    }
    digest = sha256_hex(body)
    op_key = build_operation_key(
        kind="integration_handoff",
        run_id=run.id,
        plan_version=int(run.plan_version or 0),
        assignment_version=int(run.assignment_version or 0),
        generation=int(run.active_generation),
    )
    repo = DriverRepository(session)
    existing = repo.find_operation(run.id, op_key)
    if existing is not None:
        if existing.request_hash != digest:
            return {
                "status": "blocked",
                "errorCode": "idempotency_conflict",
                "message": "handoff key reused with different body",
            }
        return {
            "status": existing.status,
            "handoffRef": (existing.result_refs or [None])[0],
            "operationId": existing.id,
        }

    handoff_id = _id("hof")
    op = SchedulerOperationRow(
        id=_id("sop"),
        run_id=run.id,
        thread_id=fence.thread_id,
        kind="integration_handoff",
        operation_key=op_key,
        request_hash=digest,
        binding_json={**body, "handoffId": handoff_id, "handoffHash": digest},
        status="succeeded",
        result_refs=[handoff_id],
        created_at=_now(session),
        updated_at=_now(session),
    )
    repo.add_operation(op)
    run.collaboration_state = "ready_for_integration"
    run.collaboration_ready_at = _now(session)
    append_fact(
        session,
        run.id,
        "run.collaboration_ready",
        {
            "handoffId": handoff_id,
            "handoffHash": digest,
            "planVersion": run.plan_version,
            "assignmentVersion": run.assignment_version,
        },
    )
    # Intentionally no integration.requested outbox — D12 consumes the handoff record.
    return {
        "status": "succeeded",
        "handoffRef": handoff_id,
        "operationId": op.id,
        "handoffHash": digest,
    }


def register_wait_action(
    session: Session,
    fence: LeaseFence,
    action: NextAction,
) -> dict[str, Any]:
    wait_key = action.wait_key or f"yield:{action.detail or 'none'}"
    reason = action.wait_reason or "driver_yield"
    scope: dict[str, Any] = {}
    if action.stage_id:
        scope["stageId"] = action.stage_id
    if action.work_package_id:
        scope["workPackageId"] = action.work_package_id
    from agentforge.storage.models import StageRow

    if action.stage_id:
        stage = session.get(StageRow, action.stage_id)
        scope["stageStatus"] = stage.status if stage else None
    if wait_key.startswith("session:"):
        scope["instanceId"] = wait_key.removeprefix("session:")
    if wait_key.startswith(("stop:", "submit:", "attempt:")):
        scope["attemptId"] = wait_key.split(":", 1)[1]
    if wait_key.startswith("recovery:"):
        scope["caseId"] = wait_key.removeprefix("recovery:")
    outcome = WaitService(session).register_or_observe(
        fence,
        wait_key=wait_key,
        reason=reason,
        scope=scope,
        thread_id=fence.thread_id,
    )
    if outcome.status == "satisfied":
        return {"status": "satisfied", "waitId": None}
    assert outcome.wait is not None
    return {"status": "waiting", "waitId": outcome.wait.wait_id, "reason": reason}


def open_recovery_stub(session: Session, fence: LeaseFence, action: NextAction) -> dict[str, Any]:
    """Open RecoveryCase + scheduler op; probe runs via recovery.inspect outbox."""
    assert_driver_fence(session, fence, require_active_thread=fence.thread_id is not None)
    run = session.get(Run, fence.run_id, with_for_update=True)
    if run is None:
        raise ValueError("run missing")
    op_key = build_operation_key(
        kind="recovery_inspect",
        run_id=run.id,
        plan_version=int(run.plan_version or 0),
        assignment_version=int(run.assignment_version or 0),
        generation=int(run.active_generation),
        work_package_id=action.work_package_id,
    )
    repo = DriverRepository(session)
    existing = repo.find_operation(run.id, op_key)
    if existing is not None:
        return {
            "status": existing.status,
            "operationId": existing.id,
            "recoveryCaseRef": (existing.result_refs or [None])[0],
        }

    from agentforge.recovery.service import RecoveryService

    reason = action.recovery_reason or action.detail or "open_recovery"
    op_id = _id("sop")
    case = RecoveryService(session).open_case(
        run.id,
        reason=str(reason),
        request_operation_id=op_id,
        work_package_id=action.work_package_id,
        emit_outbox=True,
    )
    repo.add_operation(
        SchedulerOperationRow(
            id=op_id,
            run_id=run.id,
            thread_id=fence.thread_id,
            kind="recovery_inspect",
            operation_key=op_key,
            request_hash=sha256_hex(
                {"reason": reason, "wp": action.work_package_id, "caseId": case.case_id}
            ),
            binding_json={
                "caseId": case.case_id,
                "reason": reason,
                "workPackageId": action.work_package_id,
            },
            status="waiting",
            result_refs=[case.case_id],
            created_at=_now(session),
            updated_at=_now(session),
        )
    )
    return {
        "status": "waiting",
        "operationId": op_id,
        "recoveryCaseRef": case.case_id,
    }


# --- Graph node wrappers ---


def refresh_snapshot(state: CollaborationState, runtime: Runtime[DriverContext]) -> dict[str, Any]:
    ctx = runtime.context
    steps = int(state.get("steps_remaining", 8))

    def _load(session: Session) -> Any:
        return SnapshotReader(session).load(ctx.fence.run_id, ctx.fence)

    snap = run_fenced_transaction(ctx.engine, ctx.fence, _load)
    ctx.latest_snapshot = snap
    return {
        "run_id": snap.run_id,
        "thread_id": ctx.fence.thread_id,
        "snapshot_hash": snap.snapshot_hash,
        "observed_sequence": snap.last_sequence,
        "collaboration_state": snap.collaboration_state,
        "steps_remaining": max(0, steps - 1),
        "next_action": None,
        "last_error": None,
    }


def decide_next_action(
    state: CollaborationState, runtime: Runtime[DriverContext]
) -> dict[str, Any]:
    ctx = runtime.context
    if int(state.get("steps_remaining") or 0) <= 0:
        action = NextAction(
            kind="register_wait",
            wait_reason="driver_yield",
            wait_key=f"yield:{state.get('observed_sequence') or 0}",
            detail="fragment_budget",
        )
        ctx.last_action = action
        return {**state_from_action(action), "fragment_outcome": "yielded"}
    snap = ctx.latest_snapshot
    if snap is None:
        raise RuntimeError("refresh_snapshot must run before decide_next_action")
    if snap.collaboration_state == "ready_for_integration" and snap.run_status not in {
        "cancel_requested",
        "pause_requested",
        "paused",
        "blocked",
        "canceled",
    }:
        action = NextAction(kind="end", detail="ready_for_integration")
        ctx.last_action = action
        return {
            **state_from_action(action),
            "fragment_outcome": "ready_for_integration",
        }
    action = choose_next_action(snap)
    ctx.last_action = action
    return state_from_action(action)


def node_ensure_workspaces(
    state: CollaborationState, runtime: Runtime[DriverContext]
) -> dict[str, Any]:
    ctx = runtime.context
    action = action_from_state(state)

    def _apply(session: Session) -> dict[str, Any]:
        return ensure_workspaces(session, ctx.fence, action, state_dir=ctx.state_dir)

    result = run_fenced_transaction(ctx.engine, ctx.fence, _apply)
    return {"operation_ref": (result.get("operations") or [{}])[0].get("operationId")}


def node_ensure_instances(
    state: CollaborationState, runtime: Runtime[DriverContext]
) -> dict[str, Any]:
    ctx = runtime.context

    def _apply(session: Session) -> dict[str, Any]:
        return ensure_instances(session, ctx.fence)

    result = run_fenced_transaction(ctx.engine, ctx.fence, _apply)
    return {"operation_ref": result.get("operationId"), "last_error": result.get("errorCode")}


def node_dispatch_ready(
    state: CollaborationState, runtime: Runtime[DriverContext]
) -> dict[str, Any]:
    ctx = runtime.context
    action = action_from_state(state)

    def _apply(session: Session) -> dict[str, Any]:
        return dispatch_ready(session, ctx.fence, action)

    result = run_fenced_transaction(ctx.engine, ctx.fence, _apply)
    return {
        "operation_ref": result.get("operationId") or result.get("attemptId"),
        "last_error": result.get("errorCode"),
    }


def node_accept_submissions(
    state: CollaborationState, runtime: Runtime[DriverContext]
) -> dict[str, Any]:
    ctx = runtime.context
    message_ref = (state.get("action_detail") or "").removeprefix("message:")
    if not message_ref or not message_ref.startswith("msg"):
        # No concrete message — wait for stop / stage pipeline instead of inventing submit.
        return {"last_error": None, "fragment_outcome": None}

    def _apply(session: Session) -> dict[str, Any]:
        return accept_submission(session, ctx.fence, message_ref=message_ref)

    result = run_fenced_transaction(ctx.engine, ctx.fence, _apply)
    return {
        "operation_ref": result.get("operationId"),
        "last_error": result.get("errorCode"),
    }


def node_register_wait(
    state: CollaborationState, runtime: Runtime[DriverContext]
) -> dict[str, Any]:
    ctx = runtime.context
    action = action_from_state(state)

    def _apply(session: Session) -> dict[str, Any]:
        return register_wait_action(session, ctx.fence, action)

    result = run_fenced_transaction(ctx.engine, ctx.fence, _apply)
    if result.get("status") == "satisfied":
        return {"wait_ref": None, "next_action": "refresh"}
    if action.wait_reason == "driver_yield" and action.detail == "fragment_budget":
        from agentforge.driver.signals import record_wake_signal
        from agentforge.storage.uow import unit_of_work_connection

        with unit_of_work_connection(ctx.engine) as uow:
            record_wake_signal(
                uow.session,
                run_id=ctx.fence.run_id,
                source_kind="driver_continue",
                source_ref=f"{result.get('waitId')}:{ctx.fence.lease_generation}",
                binding={"waitId": result.get("waitId")},
            )
    return {"wait_ref": result.get("waitId")}


def await_condition(state: CollaborationState, runtime: Runtime[DriverContext]) -> dict[str, Any]:
    """Single interrupt — resume data is a re-check hint only."""
    wait_ref = state.get("wait_ref")
    if not wait_ref:
        return {"wait_ref": None, "next_action": "refresh", "fragment_outcome": "yielded"}
    ctx = runtime.context

    def _bound(session: Session) -> Any:
        return WaitService(session).get_bound_wait(wait_ref)

    wait = run_fenced_transaction(ctx.engine, ctx.fence, _bound)
    interrupt(
        {
            "waitId": wait.wait_id,
            "reason": wait.reason,
            "scope": wait.scope_json,
        }
    )
    return {
        "wait_ref": None,
        "next_action": "refresh",
        "steps_remaining": 8,
        "fragment_outcome": None,
    }


def node_prepare_handoff(
    state: CollaborationState, runtime: Runtime[DriverContext]
) -> dict[str, Any]:
    ctx = runtime.context

    def _apply(session: Session) -> dict[str, Any]:
        return prepare_handoff(session, ctx.fence)

    result = run_fenced_transaction(ctx.engine, ctx.fence, _apply)
    return {
        "handoff_ref": result.get("handoffRef"),
        "operation_ref": result.get("operationId"),
        "collaboration_state": "ready_for_integration"
        if result.get("status") == "succeeded"
        else state.get("collaboration_state"),
        "fragment_outcome": "ready_for_integration"
        if result.get("status") == "succeeded"
        else "blocked",
        "last_error": result.get("errorCode"),
    }


def node_open_recovery(
    state: CollaborationState, runtime: Runtime[DriverContext]
) -> dict[str, Any]:
    ctx = runtime.context
    action = action_from_state(state)

    def _apply(session: Session) -> dict[str, Any]:
        opened = open_recovery_stub(session, ctx.fence, action)
        wait = register_wait_action(
            session,
            ctx.fence,
            NextAction(
                kind="register_wait",
                work_package_id=action.work_package_id,
                wait_reason="recovery_evidence",
                wait_key=f"recovery:{opened.get('recoveryCaseRef') or 'case'}",
                detail=action.recovery_reason or action.detail,
            ),
        )
        return {**opened, "waitId": wait.get("waitId")}

    result = run_fenced_transaction(ctx.engine, ctx.fence, _apply)
    return {
        "recovery_case_ref": result.get("recoveryCaseRef"),
        "operation_ref": result.get("operationId"),
        "wait_ref": result.get("waitId"),
        "fragment_outcome": "blocked",
    }


def node_prepare_rework(
    state: CollaborationState, runtime: Runtime[DriverContext]
) -> dict[str, Any]:
    ctx = runtime.context

    def apply(session: Session) -> dict[str, Any]:
        from sqlalchemy import select

        from agentforge.contracts.agents import AgentInstanceActionRequest
        from agentforge.instances.actions import InstanceActionService
        from agentforge.stages.service import StageService
        from agentforge.storage.models import AgentInstanceRow, ReworkOrderRow

        stage_id = state.get("action_stage_id")
        order = session.scalar(
            select(ReworkOrderRow).where(
                ReworkOrderRow.stage_id == stage_id, ReworkOrderRow.status == "open"
            )
        )
        if order is None:
            return {"status": "blocked", "errorCode": "rework_order_missing"}
        instance = session.scalar(
            select(AgentInstanceRow).where(
                AgentInstanceRow.run_id == ctx.fence.run_id,
                AgentInstanceRow.role_id == order.target_role_id,
            )
        )
        if instance is None:
            return {"status": "blocked", "errorCode": "instance_missing"}
        if instance.lifecycle != "closed":
            action = "close" if instance.lifecycle == "stopped" else "request_stop"
            accepted = InstanceActionService(session).request_action(
                instance.id,
                AgentInstanceActionRequest.model_validate(
                    dict(action=action, reason=f"rework:{order.id}")
                ),
                actor="driver:rework",
            )
            return {"status": "waiting", "operationId": accepted.operation_id}
        prepared = ensure_workspace_operation(
            session,
            ctx.fence,
            role_id=order.target_role_id,
            purpose=order.id,
            state_dir=ctx.state_dir,
        )
        if prepared["status"] != "succeeded":
            return prepared
        StageService(session).accept_rework(order.id, workspace_id=prepared["resultRefs"][0])
        return {"status": "succeeded"}

    result = run_fenced_transaction(ctx.engine, ctx.fence, apply)
    if result["status"] == "succeeded":
        return {"wait_ref": None, "next_action": "refresh"}

    def wait(session: Session) -> dict[str, Any]:
        return register_wait_action(
            session,
            ctx.fence,
            NextAction(
                kind="register_wait",
                stage_id=state.get("action_stage_id"),
                wait_reason="recovery_evidence",
                wait_key=f"rework:{state.get('action_stage_id')}",
                detail="await verified rework preparation",
            ),
        )

    waiting = run_fenced_transaction(ctx.engine, ctx.fence, wait)
    return {"wait_ref": waiting.get("waitId"), "last_error": result.get("errorCode")}


def node_coordinate_stop(
    state: CollaborationState, runtime: Runtime[DriverContext]
) -> dict[str, Any]:
    """Coordinate pause/cancel stops; do not claim paused until executions are quiescent."""
    ctx = runtime.context
    detail = state.get("action_detail") or ""
    snap = ctx.latest_snapshot

    def _apply(session: Session) -> dict[str, Any]:
        from agentforge.driver.control import RunControlService

        ctrl = RunControlService(session)
        run_status = snap.run_status if snap is not None else ""
        if "cancel" in detail or run_status == "cancel_requested":
            coord = ctrl.coordinate_cancel(ctx.fence.run_id)
        else:
            coord = ctrl.coordinate_pause(ctx.fence.run_id)
        wait = register_wait_action(
            session,
            ctx.fence,
            NextAction(
                kind="register_wait",
                wait_reason="recovery_evidence",
                wait_key=f"control:{run_status or detail or 'stop'}",
                detail=str(coord.get("status")),
            ),
        )
        return {**coord, "waitId": wait.get("waitId")}

    result = run_fenced_transaction(ctx.engine, ctx.fence, _apply)
    terminal = bool(result.get("paused") or result.get("canceled"))
    return {
        "wait_ref": None if terminal else result.get("waitId"),
        "operation_ref": (result.get("stopOperationIds") or [None])[0],
        "fragment_outcome": "yielded" if terminal else "blocked",
        "action_detail": str(result.get("status")),
    }


def node_end(state: CollaborationState, runtime: Runtime[DriverContext]) -> dict[str, Any]:
    outcome = state.get("fragment_outcome") or (
        "ready_for_integration"
        if state.get("collaboration_state") == "ready_for_integration"
        else "yielded"
    )
    return {"fragment_outcome": outcome, "next_action": "end"}
