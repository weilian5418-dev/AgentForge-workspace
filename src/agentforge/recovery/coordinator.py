"""Apply approved recovery transitions through existing durable native operations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from agentforge.core.config import Settings
from agentforge.instances.worker import process_instance_operation
from agentforge.recovery.errors import RecoveryError
from agentforge.storage.models import (
    AgentInstanceRow,
    InstanceAttemptRow,
    InstanceOperationRow,
    Outbox,
    RecoveryCaseRow,
    RecoveryObservationRow,
    Run,
    StageRow,
    StageSubmissionRow,
)
from agentforge.storage.uow import unit_of_work_connection


def process_transition(
    engine: Engine,
    settings: Settings,
    *,
    case_id: str | None = None,
    rework_order_id: str | None = None,
) -> str:
    if not case_id:
        raise RecoveryError("case_not_found", "Recovery case required")
    with unit_of_work_connection(engine) as uow:
        s = uow.session
        hint = s.get(RecoveryCaseRow, case_id)
        if hint is None:
            raise RecoveryError("case_not_found", case_id)
        run = s.get(Run, hint.run_id, with_for_update=True, populate_existing=True)
        case = s.get(RecoveryCaseRow, case_id, with_for_update=True, populate_existing=True)
        assert run is not None and case is not None
        if case.status != "applying":
            return case.status
        binding = case.context_json or {}
        if (case.plan_version, case.assignment_version, binding.get("activeGeneration")) != (
            run.plan_version,
            run.assignment_version,
            run.active_generation,
        ) or run.status in {"canceled", "cancel_requested", "completed", "failed"}:
            raise RecoveryError("recovery_evidence_stale", "Run binding changed")
        observations = list(
            s.scalars(
                select(RecoveryObservationRow).where(RecoveryObservationRow.case_id == case.id)
            ).all()
        )
        native = {}
        for observation in observations:
            value = (observation.binding_json or {}).get("nativeObservation")
            if value and observation.observed_at >= datetime.now(UTC) - timedelta(minutes=5):
                native[value["instanceId"]] = value
        instances = list(
            s.scalars(select(AgentInstanceRow).where(AgentInstanceRow.run_id == run.id)).all()
        )
        for snapshot_instance in instances:
            inst: Any = snapshot_instance
            observed = native.get(inst.id)
            if (
                not observed
                or observed.get("support") != "supported"
                or (
                    observed.get("nativeSessionId"),
                    observed.get("workspaceId"),
                    observed.get("generation"),
                    observed.get("sessionRef"),
                )
                != (inst.native_session_id, inst.workspace_id, inst.generation, inst.session_ref)
            ):
                case.status = "blocked"
                case.blocker_codes = ["native_state_unknown"]
                return "blocked"
        action = case.proposed_action
        if action in {
            "await_reconciliation",
            "await_stop_verification",
            "wait_authorization",
            "block_or_rework",
        }:
            case.status = "blocked"
            return "blocked"
        if action == "resume_existing":
            from agentforge.instances.bindings import require_profile
            from agentforge.recovery.resume import resume_existing_session

            opids = []
            for inst in instances:
                require_profile(s, inst.agent_profile_id, {"resumeOrHandoff"})
                result = resume_existing_session(
                    s,
                    instance_id=inst.id,
                    attempt_id=inst.current_attempt_id,
                    idempotency_key=f"recover:{case.id}:{inst.id}",
                    actor="recovery:approved",
                )
                opids.append(result["operationId"])
        else:
            opids = []
        run_id = run.id
    for opid in opids:
        result = process_instance_operation(engine, opid)
        if result["status"] != "succeeded":
            return str(result["status"])
    if action == "handoff_new_session":
        # A verified stopped execution is necessary; an unsupported probe is never stop evidence.
        for instance_id, observed in native.items():
            if (
                observed.get("running") is not False
                or observed.get("childrenRunning") is not False
                or observed.get("writesQuiesced") is not True
            ):
                raise RecoveryError("pending_stop_verification", "Old writable execution must stop")
            from agentforge.contracts.agents import AgentInstanceActionRequest
            from agentforge.instances.actions import InstanceActionService

            for kind in ["request_stop", "close"]:
                with unit_of_work_connection(engine) as uow:
                    accepted = InstanceActionService(uow.session).request_action(
                        instance_id,
                        AgentInstanceActionRequest.model_validate(
                            {"action": kind, "reason": f"handoff:{case_id}"}
                        ),
                        actor="recovery:handoff",
                    )
                if (
                    process_instance_operation(engine, accepted.operation_id)["status"]
                    != "succeeded"
                ):
                    return "needs_reconciliation"
            from agentforge.contracts.collaboration import LeaseFence
            from agentforge.driver.workspace_preparation import (
                ensure_workspace_operation,
                process_workspace_preparation,
            )
            from agentforge.storage.repositories.leases import LeaseRepository

            with unit_of_work_connection(engine) as uow:
                lease = LeaseRepository(uow).acquire_run_lease(
                    run_id=run_id, owner=f"recovery:{uuid.uuid4().hex}"
                )
                inst = uow.session.get(AgentInstanceRow, instance_id)
                assert inst is not None
                prepared = ensure_workspace_operation(
                    uow.session,
                    LeaseFence(
                        run_id=run_id,
                        owner=lease.owner,
                        token=lease.token,
                        lease_generation=lease.generation,
                    ),
                    role_id=inst.role_id,
                    purpose=case_id,
                    state_dir=settings.state_dir,
                )
                LeaseRepository(uow).release_run_lease(
                    run_id=run_id, owner=lease.owner, token=lease.token, generation=lease.generation
                )
            workspace = process_workspace_preparation(
                engine, {"operationId": prepared["operationId"]}, settings=settings
            )
            if workspace["status"] != "succeeded":
                return str(workspace["status"])
            with unit_of_work_connection(engine) as uow:
                s = uow.session
                run = s.get(Run, run_id, with_for_update=True)
                inst = s.get(AgentInstanceRow, instance_id, with_for_update=True)
                assert run is not None and inst is not None
                if inst.lifecycle != "closed":
                    raise RecoveryError("pending_stop_verification", "Old session has not closed")
                old = inst.current_attempt_id
                if old:
                    attempt = s.get(InstanceAttemptRow, old)
                    if attempt:
                        attempt.status = "superseded"
                inst.workspace_id = workspace["workspaceId"]
                inst.native_session_id = None
                inst.session_ref = None
                inst.current_attempt_id = None
                inst.lifecycle = "created"
                inst.activity = "idle"
                key = f"handoff-create:{case_id}:{instance_id}"
                op = s.scalar(
                    select(InstanceOperationRow).where(InstanceOperationRow.idempotency_key == key)
                )
                if op is None:
                    op = InstanceOperationRow(
                        id="iop_" + uuid.uuid4().hex,
                        instance_id=instance_id,
                        kind="create_session",
                        status="prepared",
                        idempotency_key=key,
                        payload_json={"recoveryCaseId": case_id, "sourceAttemptId": old},
                    )
                    s.add(op)
                opid = op.id
            if process_instance_operation(engine, opid)["status"] != "succeeded":
                return "needs_reconciliation"
    with unit_of_work_connection(engine) as uow:
        s = uow.session
        run = s.get(Run, run_id, with_for_update=True, populate_existing=True)
        case = s.get(RecoveryCaseRow, case_id, with_for_update=True, populate_existing=True)
        assert run is not None and case is not None
        if run.status in {"cancel_requested", "canceled", "completed", "failed"}:
            return "blocked"
        if action == "continue_stage_processing":
            for sub in s.scalars(
                select(StageSubmissionRow)
                .join(StageRow, StageRow.id == StageSubmissionRow.stage_id)
                .where(StageRow.run_id == run_id)
            ).all():
                key = f"recovery-stage:{case_id}:{sub.id}"
                if not s.scalar(select(Outbox).where(Outbox.idempotency_key == key)):
                    s.add(
                        Outbox(
                            id="obx_" + uuid.uuid4().hex,
                            run_id=run_id,
                            intent_type="stage.stop_verify",
                            target="stage",
                            payload={"operationId": sub.id, "submissionId": sub.id},
                            idempotency_key=key,
                            delivery_status="pending",
                            attempt_count=0,
                        )
                    )
        case.status = "applied"
        from agentforge.driver.control import RunControlService

        if run.status in {"paused", "blocked", "pause_requested"}:
            RunControlService(s).apply_verified_resume(
                run_id, recovery_case_id=case_id, operation_id=case.request_operation_id
            )
        from agentforge.storage.event_log import append_fact

        append_fact(
            s,
            run_id,
            "recovery.applied",
            {"caseId": case_id, "status": "applied", "action": action, "blockerCodes": []},
        )
        from agentforge.driver.signals import record_wake_signal

        record_wake_signal(
            s,
            run_id=run_id,
            source_kind="recovery",
            source_ref=f"applied:{case_id}",
            binding={"caseId": case_id},
        )
    return "applied"
