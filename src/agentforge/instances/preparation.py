"""ExecutionPreparationService — prepare instances from D08 confirmation (no task start)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.agents import AgentInstance, ExecutionRequest
from agentforge.contracts.plans import PlanSpec
from agentforge.contracts.runs import OperationAccepted
from agentforge.instances.bindings import require_profile
from agentforge.instances.eligibility import EligibilityService
from agentforge.instances.errors import InstanceError
from agentforge.instances.repository import InstanceRepository
from agentforge.instances.status import combine_instance_status_facets
from agentforge.storage.models import (
    AgentInstanceRow,
    ExecutionRequestRow,
    GitWorkspaceRow,
    InstanceOperationRow,
    Outbox,
    Plan,
    Project,
    Run,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class ExecutionPreparationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = InstanceRepository(session)
        self.eligibility = EligibilityService(session)

    def prepare(
        self,
        run_id: str,
        body: ExecutionRequest,
        *,
        actor: str,
        idempotency_key: str,
    ) -> OperationAccepted:
        run = self.session.get(Run, run_id, with_for_update=True, populate_existing=True)
        if run is None:
            raise InstanceError("run_not_found", run_id)
        existing = self.repo.get_execution_request(run_id=run_id, idempotency_key=idempotency_key)
        if existing is not None:
            if (
                existing.plan_hash != body.plan_hash
                or existing.plan_version != body.plan_version
                or existing.assignment_version != body.assignment_version
                or existing.gate_policy_version != body.gate_policy_version
            ):
                raise InstanceError("idempotency_conflict", "Execution request changed")
            return OperationAccepted(
                operation_id=existing.id,
                status="accepted" if existing.status == "prepared" else existing.status,  # type: ignore[arg-type]
                run_id=run_id,
            )

        run = self.session.get(Run, run_id)
        if run is None:
            raise InstanceError("run_not_found", f"Run not found: {run_id}")
        project = self.session.get(Project, run.project_id)
        if project is None:
            raise InstanceError("project_not_found", "Project missing")
        if getattr(project, "grant_status", "active") != "active":
            raise InstanceError("workspace_grant_revoked", "Directory grant inactive")

        plan = self.session.get(Plan, (run_id, body.plan_version))
        if plan is None:
            raise InstanceError("plan_not_found", "Plan version missing")
        if plan.status != "approved":
            raise InstanceError("plan_not_approved", "Execution requires approved plan")
        if plan.plan_hash and plan.plan_hash != body.plan_hash:
            raise InstanceError("plan_hash_mismatch", "Confirmed planHash is stale")
        if run.plan_version != body.plan_version:
            raise InstanceError("plan_version_stale", "planVersion is not current")
        gate_ver = plan.gate_policy_version or 1
        if gate_ver != body.gate_policy_version:
            raise InstanceError("gate_policy_mismatch", "gatePolicyVersion changed")

        assignments = self.repo.list_assignments(
            run_id=run_id, assignment_version=body.assignment_version
        )
        if not assignments:
            raise InstanceError("assignments_incomplete", "No assignments for version")
        if any(a.plan_version != body.plan_version for a in assignments):
            raise InstanceError("assignment_plan_mismatch", "Assignments bind other plan")

        if (
            run.assignment_version != body.assignment_version
            or run.gate_policy_version != body.gate_policy_version
        ):
            raise InstanceError("assignment_revoked", "Execution binding is stale")
        spec = PlanSpec.model_validate(plan.spec_json)
        workspaces = {}
        # Re-check profiles / auth — block, do not auto-replace.
        for asg in assignments:
            role = next((r for r in spec.roles if r.role_id == asg.role_id), None)
            if role is None or asg.confirmed_at is None:
                raise InstanceError("assignment_revoked", "Role is not confirmed")
            require_profile(
                self.session,
                asg.agent_profile_id,
                set(role.required_capabilities),
                expected_version=asg.profile_config_version,
            )
            candidates = list(
                self.session.scalars(
                    select(GitWorkspaceRow).where(
                        GitWorkspaceRow.project_id == run.project_id,
                        GitWorkspaceRow.baseline_id == run.baseline_id,
                        GitWorkspaceRow.role_id == asg.role_id,
                        GitWorkspaceRow.status == "active",
                    )
                ).all()
            )
            if len(candidates) != 1:
                raise InstanceError(
                    "workspace_unbound",
                    "Prepare one independent active workspace per role using D07",
                )
            workspaces[asg.id] = candidates[0]

        request_id = _id("exreq")
        self.repo.add_execution_request(
            ExecutionRequestRow(
                id=request_id,
                run_id=run_id,
                idempotency_key=idempotency_key,
                plan_hash=body.plan_hash,
                plan_version=body.plan_version,
                assignment_version=body.assignment_version,
                gate_policy_version=body.gate_policy_version,
                status="prepared",
                actor=actor,
                result_json={},
                created_at=_now(),
            )
        )

        instance_ids: list[str] = []
        for asg in assignments:
            existing_inst = self.repo.find_instance(
                run_id=run_id, assignment_id=asg.id, role_id=asg.role_id
            )
            if existing_inst is not None:
                instance_ids.append(existing_inst.id)
                continue
            inst_id = _id("inst")
            lifecycle = "created"
            activity = "idle"
            row = AgentInstanceRow(
                id=inst_id,
                assignment_id=asg.id,
                run_id=run_id,
                role_id=asg.role_id,
                agent_profile_id=asg.agent_profile_id,
                assignment_version=body.assignment_version,
                plan_version=body.plan_version,
                plan_hash=body.plan_hash,
                gate_policy_version=body.gate_policy_version,
                lifecycle=lifecycle,
                activity=activity,
                status=combine_instance_status_facets("created", "idle"),
                execution_environment=project.environment_id,
                workspace_id=workspaces[asg.id].id,
                generation=run.active_generation,
                created_at=_now(),
                updated_at=_now(),
            )
            self.repo.add_instance(row)
            op_id = _id("iop")
            self.repo.add_operation(
                InstanceOperationRow(
                    id=op_id,
                    instance_id=inst_id,
                    kind="create_session",
                    status="prepared",
                    idempotency_key=f"{request_id}:{inst_id}:create_session",
                    payload_json={"executionRequestId": request_id},
                    prepared_at=_now(),
                )
            )
            self.session.add(
                Outbox(
                    id=_id("obx"),
                    run_id=run_id,
                    event_id=None,
                    intent_type="instance.create_session",
                    target="session_coordinator",
                    payload={"operationId": op_id, "instanceId": inst_id},
                    idempotency_key=f"create_session:{op_id}",
                    delivery_status="pending",
                    attempt_count=0,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            instance_ids.append(inst_id)

        # Eligibility for first-stage awareness only — does not dispatch tasks.
        eligibility = self.eligibility.check(run_id=run_id)
        req = self.repo.get_execution_request(run_id=run_id, idempotency_key=idempotency_key)
        if req is not None:
            req.result_json = {
                "instanceIds": instance_ids,
                "eligibility": eligibility.model_dump(by_alias=True),
            }

        if run.status in ("awaiting_confirmation", "planning", "awaiting_approval"):
            run.status = "preparing"
            run.updated_at = _now()

        return OperationAccepted(operation_id=request_id, status="accepted", run_id=run_id)

    def list_instances(self, run_id: str) -> list[AgentInstance]:
        rows = self.repo.list_instances_for_run(run_id)
        return [self._to_contract(r) for r in rows]

    def get_instance(self, instance_id: str) -> dict[str, Any]:
        row = self.repo.get_instance(instance_id)
        if row is None:
            raise InstanceError("instance_not_found", f"Instance not found: {instance_id}")
        native = (
            self.repo.get_native_session(row.native_session_id)
            if row.native_session_id
            else self.repo.active_session_for_instance(row.id)
        )
        return {
            "instance": self._to_contract(row),
            "session": None
            if native is None
            else {
                "nativeSessionId": native.id,
                "sessionRef": native.session_ref,
                "bindingStatus": native.binding_status,
                "workspaceId": native.workspace_id,
            },
            "currentExecution": {
                "workPackageId": row.current_work_package_id,
                "stageId": row.stage_id,
                "attemptId": row.current_attempt_id,
                "generation": row.generation,
            },
        }

    @staticmethod
    def _to_contract(row: AgentInstanceRow) -> AgentInstance:
        return AgentInstance.model_validate(
            {
                "instanceId": row.id,
                "assignmentId": row.assignment_id,
                "runId": row.run_id,
                "roleId": row.role_id,
                "agentProfileId": row.agent_profile_id,
                "assignmentVersion": row.assignment_version,
                "sessionRef": row.session_ref,
                "nativeSessionId": row.native_session_id,
                "workspaceId": row.workspace_id,
                "stageId": row.stage_id,
                "lifecycle": row.lifecycle or "created",
                "activity": row.activity or "idle",
                "status": row.status,
                "executionEnvironment": row.execution_environment,
                "currentAttemptId": row.current_attempt_id,
                "permissionScopeRef": row.permission_scope_ref,
                "budgetRef": row.budget_ref,
                "currentExecution": {
                    "workPackageId": row.current_work_package_id,
                    "stageId": row.stage_id,
                    "attemptId": row.current_attempt_id,
                    "generation": row.generation,
                },
            }
        )
