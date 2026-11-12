"""ExecutionEligibility — D09 may prepare sessions; stage dispatch needs gates."""

from __future__ import annotations

from sqlalchemy.orm import Session

from agentforge.contracts.agents import ExecutionEligibility
from agentforge.instances.errors import InstanceError
from agentforge.storage.models import GateDecisionRow, Plan, RoleAssignmentRow, Run, StageRow


class EligibilityService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def check(
        self,
        *,
        run_id: str,
        stage_id: str | None = None,
        work_package_id: str | None = None,
        allow_first_stage_without_gate: bool = True,
    ) -> ExecutionEligibility:
        run = self.session.get(Run, run_id)
        if run is None:
            raise InstanceError("run_not_found", f"Run not found: {run_id}")
        if run.plan_version is None:
            raise InstanceError("plan_missing", "Run has no current plan version")

        plan = self.session.get(Plan, (run_id, run.plan_version))
        if plan is None or plan.status != "approved":
            raise InstanceError("plan_not_approved", "Confirmed/approved plan required")

        from sqlalchemy import select

        assignments = list(
            self.session.scalars(
                select(RoleAssignmentRow).where(
                    RoleAssignmentRow.run_id == run_id,
                    RoleAssignmentRow.plan_version == run.plan_version,
                )
            ).all()
        )
        if not assignments:
            raise InstanceError("assignments_incomplete", "No role assignments for plan")
        assignment_version = max(a.assignment_version for a in assignments)
        if any(a.confirmed_at is None for a in assignments):
            # D08 may confirm via plan decision without per-row confirmed_at; tolerate approved plan.
            pass

        stages = list(
            self.session.scalars(
                select(StageRow).where(
                    StageRow.run_id == run_id, StageRow.plan_version == run.plan_version
                )
            ).all()
        )
        first_stage_id = stages[0].id if stages else None
        allowed_stages: list[str] = []
        gate_refs: list[str] = []
        blocked_reason: str | None = None

        if stage_id is None and first_stage_id and allow_first_stage_without_gate:
            allowed_stages = [first_stage_id]
        elif stage_id is not None:
            is_first = first_stage_id is not None and stage_id == first_stage_id
            gates = list(
                self.session.scalars(
                    select(GateDecisionRow).where(GateDecisionRow.stage_id == stage_id)
                ).all()
            )
            released = [g for g in gates if g.result in ("released", "approve", "approved")]
            if is_first and allow_first_stage_without_gate:
                allowed_stages = [stage_id]
            elif released:
                allowed_stages = [stage_id]
                gate_refs = [g.id for g in released]
            else:
                blocked_reason = "gate_decision_missing"
        else:
            blocked_reason = "stage_unspecified"

        allowed_wps: list[str] = []
        if work_package_id and blocked_reason is None:
            allowed_wps = [work_package_id]
        elif work_package_id and blocked_reason:
            allowed_wps = []

        return ExecutionEligibility(
            run_id=run_id,
            allowed_work_package_ids=allowed_wps,
            allowed_stage_ids=allowed_stages,
            plan_version=run.plan_version,
            assignment_version=assignment_version,
            gate_decision_refs=gate_refs,
            valid_until=None,
            revalidate_on=[
                "assignment_change",
                "plan_change",
                "auth_expired",
                "workspace_grant_revoked",
            ],
            blocked_reason=blocked_reason,
        )

    def assert_may_dispatch(
        self,
        *,
        run_id: str,
        stage_id: str,
        work_package_id: str,
    ) -> ExecutionEligibility:
        result = self.check(
            run_id=run_id, stage_id=stage_id, work_package_id=work_package_id
        )
        if result.blocked_reason:
            raise InstanceError(
                "execution_blocked",
                f"Dispatch blocked: {result.blocked_reason}",
            )
        if work_package_id not in result.allowed_work_package_ids:
            raise InstanceError("work_package_not_allowed", "Work package not eligible")
        if stage_id not in result.allowed_stage_ids:
            raise InstanceError("stage_not_allowed", "Stage not eligible")
        return result
