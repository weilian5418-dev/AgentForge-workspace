"""Short fenced bridges from graph actions to domain services (D11.6+)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from agentforge.contracts.agents import ExecutionRequest
from agentforge.contracts.collaboration import LeaseFence, NextAction
from agentforge.driver.dispatch import DispatchService
from agentforge.driver.submissions import SubmissionBridge
from agentforge.driver.workspace_preparation import ensure_workspace_operation
from agentforge.instances.errors import InstanceError
from agentforge.instances.preparation import ExecutionPreparationService
from agentforge.storage.models import Plan, Run


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
    roles = role_ids or ([] if not action.detail else [])
    if not roles and action.detail and "role" in (action.detail or ""):
        # Fallback: caller should pass role_ids from snapshot instances.
        roles = []
    if not roles:
        # Derive from active assignments via plan roles needing a workspace.
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
    from agentforge.driver.fencing import assert_driver_fence

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
    from agentforge.driver.fencing import assert_driver_fence

    assert_driver_fence(session, fence, require_active_thread=fence.thread_id is not None)
    try:
        return SubmissionBridge(session).accept(message_ref)
    except InstanceError as exc:
        return {"status": "blocked", "errorCode": exc.code, "message": exc.message}
