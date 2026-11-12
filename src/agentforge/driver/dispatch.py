"""Reserve InstanceAttempt and create task.assignment in one fenced transaction (D11.7)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.collaboration import LeaseFence
from agentforge.contracts.messages import MessageCreateRequest, TaskAssignmentPayload
from agentforge.contracts.plans import PlanSpec
from agentforge.driver.binding import build_operation_key, sha256_hex
from agentforge.driver.fencing import assert_driver_fence
from agentforge.driver.inputs import ReleasedInputResolver, find_release_ids_for_upstream
from agentforge.driver.repository import DriverRepository
from agentforge.instances.eligibility import EligibilityService
from agentforge.instances.errors import InstanceError
from agentforge.instances.status import activity_may_accept_work
from agentforge.messaging.service import MessageService
from agentforge.storage.models import (
    AgentInstanceRow,
    InstanceAttemptRow,
    Plan,
    Run,
    SchedulerOperationRow,
    StageRow,
)
from agentforge.storage.uow import db_now


def _now(session: Session | None = None) -> datetime:
    if session is not None:
        try:
            value = db_now(session)
            if getattr(value, "tzinfo", None) is None:
                return value.replace(tzinfo=UTC)  # type: ignore[union-attr]
            return value  # type: ignore[return-value]
        except Exception:
            pass
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class DispatchService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = DriverRepository(session)
        self.eligibility = EligibilityService(session)
        self.messages = MessageService(session)
        self.inputs = ReleasedInputResolver(session)

    def reserve_and_create_message(
        self,
        fence: LeaseFence,
        work_package_id: str,
        *,
        stage_id: str | None = None,
        repair_round: int = 0,
    ) -> dict[str, Any]:
        """Fence + eligibility + reserved attempt + assignment message + outbox."""
        assert_driver_fence(self.session, fence, require_active_thread=fence.thread_id is not None)
        run = self.session.get(Run, fence.run_id, with_for_update=True, populate_existing=True)
        if run is None:
            raise InstanceError("run_not_found", fence.run_id)
        self._assert_run_admits_dispatch(run)
        if run.plan_version is None or run.assignment_version is None:
            raise InstanceError("execution_blocked", "plan/assignment versions required")

        plan = self.session.get(Plan, (run.id, run.plan_version))
        if plan is None or plan.status != "approved" or not plan.spec_json:
            raise InstanceError("plan_not_approved", "Current approved plan required")
        spec = PlanSpec.model_validate(plan.spec_json)
        wp = next((w for w in spec.work_packages if w.id == work_package_id), None)
        if wp is None:
            raise InstanceError("work_package_not_found", work_package_id)

        physical_stage = stage_id or f"{run.id}:p{run.plan_version}:{wp.stage_id}"
        self.eligibility.assert_may_dispatch(
            run_id=run.id, stage_id=physical_stage, work_package_id=work_package_id
        )

        op_key = build_operation_key(
            kind="assign",
            run_id=run.id,
            plan_version=int(run.plan_version),
            assignment_version=int(run.assignment_version),
            generation=int(run.active_generation),
            stage_id=physical_stage,
            work_package_id=work_package_id,
            repair_round=repair_round,
        )
        instance = self._select_instance(run, owner_role_id=wp.owner_role_id)
        stage = self.session.get(StageRow, physical_stage)
        if stage is None:
            raise InstanceError("stage_not_found", physical_stage)

        upstream_stage_ids = self._upstream_stage_ids(run, spec, wp, physical_stage)
        release_ids = find_release_ids_for_upstream(self.session, upstream_stage_ids=upstream_stage_ids)
        required_upstream_wps = set(wp.depends_on or [])
        bundle = self.inputs.resolve(
            run_id=run.id,
            release_ids=release_ids,
            required_work_package_ids=required_upstream_wps or None,
        )

        request_body = {
            "runId": run.id,
            "stageId": physical_stage,
            "workPackageId": work_package_id,
            "instanceId": instance.id,
            "workspaceId": instance.workspace_id,
            "objective": wp.objective,
            "releaseRefs": list(bundle.release_refs),
            "candidateRefs": list(bundle.candidate_refs),
            "bundleDigest": bundle.digest,
            "gatePolicyVersion": run.gate_policy_version,
            "generation": run.active_generation,
            "repairRound": repair_round,
        }
        digest = sha256_hex(request_body)

        existing = self.repo.find_operation(run.id, op_key)
        if existing is not None:
            if existing.request_hash != digest:
                raise InstanceError(
                    "idempotency_conflict",
                    "assign key reused with different request body",
                )
            return {
                "operationId": existing.id,
                "operationKey": op_key,
                "status": existing.status,
                "resultRefs": list(existing.result_refs or []),
                "attemptId": (existing.binding_json or {}).get("attemptId"),
                "messageId": (existing.binding_json or {}).get("messageId"),
            }

        # Active attempt unique index is the slot authority.
        prior_attempt = self.session.scalar(
            select(InstanceAttemptRow).where(
                InstanceAttemptRow.run_id == run.id,
                InstanceAttemptRow.work_package_id == work_package_id,
                InstanceAttemptRow.status.in_(
                    [
                        "scheduled",
                        "dispatching",
                        "running",
                        "submitted",
                        "awaiting_stop_verification",
                        "unknown",
                    ]
                ),
            )
        )
        if prior_attempt is not None:
            return {
                "operationId": prior_attempt.scheduler_operation_id,
                "operationKey": op_key,
                "status": "accepted",
                "resultRefs": [prior_attempt.id],
                "attemptId": prior_attempt.id,
                "messageId": None,
                "note": "active_attempt_exists",
            }

        attempt_id = _id("ia")
        attempt = InstanceAttemptRow(
            id=attempt_id,
            instance_id=instance.id,
            run_id=run.id,
            work_package_id=work_package_id,
            stage_id=physical_stage,
            generation=int(run.active_generation),
            workspace_id=instance.workspace_id or "",
            native_session_id=instance.native_session_id,
            status="scheduled",
            created_at=_now(self.session),
        )
        self.session.add(attempt)
        self.session.flush()

        payload = TaskAssignmentPayload.model_validate(
            {
                "type": "task.assignment",
                "workPackageId": work_package_id,
                "objective": wp.objective,
                "stageId": physical_stage,
                "attemptId": attempt_id,
                "inputReleaseRefs": list(bundle.release_refs),
                "inputCandidateRefs": list(bundle.candidate_refs),
                "contextBundleRef": bundle.bundle_id,
            }
        )
        body = MessageCreateRequest.model_validate(
            {
                "idempotencyKey": op_key,
                "recipientRefs": [instance.id],
                "payload": payload.model_dump(by_alias=True),
                "stageId": physical_stage,
                "workPackageId": work_package_id,
                "replyRequired": False,
            }
        )
        message = self.messages.create(
            run.id,
            body,
            sender_ref="platform:driver",
            project_id=run.project_id,
            plan_version=run.plan_version,
            assignment_version=run.assignment_version,
        )
        deliveries = self.messages.list_deliveries(message.message_id)
        delivery_id = deliveries[0].delivery_id if deliveries else None

        attempt.dispatch_delivery_id = delivery_id
        op_id = _id("sop")
        attempt.scheduler_operation_id = op_id
        binding = {
            **request_body,
            "attemptId": attempt_id,
            "messageId": message.message_id,
            "deliveryId": delivery_id,
            "contextBundle": bundle.as_dict(),
        }
        op = SchedulerOperationRow(
            id=op_id,
            run_id=run.id,
            thread_id=fence.thread_id,
            kind="task_assign",
            operation_key=op_key,
            request_hash=digest,
            binding_json=binding,
            status="accepted",
            result_refs=[attempt_id, message.message_id] + ([delivery_id] if delivery_id else []),
            created_at=_now(self.session),
            updated_at=_now(self.session),
        )
        self.repo.add_operation(op)
        self.session.flush()
        return {
            "operationId": op_id,
            "operationKey": op_key,
            "status": "accepted",
            "resultRefs": list(op.result_refs),
            "attemptId": attempt_id,
            "messageId": message.message_id,
            "deliveryId": delivery_id,
        }

    def _assert_run_admits_dispatch(self, run: Run) -> None:
        from agentforge.driver.control import run_admits_new_development
        from agentforge.runtime.control import admits_new_work, read_admission_db

        if not run_admits_new_development(run):
            raise InstanceError(
                "execution_blocked", f"run status {run.status} refuses dispatch"
            )
        if not admits_new_work(read_admission_db(self.session)):
            raise InstanceError("admission_blocked", "platform admission refuses new dispatch")

    def _select_instance(self, run: Run, *, owner_role_id: str) -> AgentInstanceRow:
        instances = list(
            self.session.scalars(
                select(AgentInstanceRow).where(
                    AgentInstanceRow.run_id == run.id,
                    AgentInstanceRow.role_id == owner_role_id,
                )
            ).all()
        )
        ready = [
            i
            for i in instances
            if i.lifecycle == "active"
            and activity_may_accept_work(i.activity)  # type: ignore[arg-type]
            and i.workspace_id
            and i.native_session_id
            and i.assignment_version == run.assignment_version
            and i.plan_version == run.plan_version
            and (i.generation is None or i.generation == run.active_generation)
        ]
        if not ready:
            raise InstanceError(
                "instance_not_ready",
                f"no idle active session for role {owner_role_id}",
            )
        # Prefer instance without another active attempt.
        for inst in ready:
            active = self.session.scalar(
                select(InstanceAttemptRow).where(
                    InstanceAttemptRow.instance_id == inst.id,
                    InstanceAttemptRow.status.in_(
                        [
                            "scheduled",
                            "dispatching",
                            "running",
                            "submitted",
                            "awaiting_stop_verification",
                            "unknown",
                        ]
                    ),
                )
            )
            if active is None:
                return inst
        raise InstanceError("instance_busy", "all role instances hold an active attempt")

    @staticmethod
    def _upstream_stage_ids(
        run: Run, spec: PlanSpec, wp: Any, physical_stage: str
    ) -> list[str]:
        def physical(logical: str) -> str:
            return f"{run.id}:p{run.plan_version}:{logical}"

        stage_def = next((s for s in spec.stages if physical(s.stage_id) == physical_stage), None)
        ids: set[str] = set()
        if stage_def:
            for dep in stage_def.dependencies or []:
                ids.add(physical(dep))
        for dep_wp in wp.depends_on or []:
            producer = next((w for w in spec.work_packages if w.id == dep_wp), None)
            if producer is not None:
                ids.add(physical(producer.stage_id))
        return sorted(ids)
