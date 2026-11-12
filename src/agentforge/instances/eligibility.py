"""Dispatch eligibility from approved DAG, exact assignments and versioned releases."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.agents import ExecutionEligibility
from agentforge.contracts.plans import PlanSpec
from agentforge.instances.errors import InstanceError
from agentforge.storage.models import (
    AgentInstanceRow,
    ArtifactCandidateRow,
    InstanceAttemptRow,
    Plan,
    ReleaseRecordRow,
    RoleAssignmentRow,
    Run,
    StageRow,
)


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
            raise InstanceError("run_not_found", run_id)
        plan = self.session.get(Plan, (run.id, run.plan_version)) if run.plan_version else None
        if plan is None or plan.status != "approved" or not plan.spec_json:
            raise InstanceError("plan_not_approved", "Current approved plan required")
        if run.status not in {"preparing", "running"}:
            raise InstanceError("execution_blocked", "Run is not admitting execution")
        assert run.plan_version is not None and run.assignment_version is not None
        spec = PlanSpec.model_validate(plan.spec_json)

        def physical(logical: Any) -> Any:
            return f"{run.id}:p{run.plan_version}:{logical}"

        definitions = {physical(s.stage_id): s for s in spec.stages}
        assignments = list(
            self.session.scalars(
                select(RoleAssignmentRow).where(
                    RoleAssignmentRow.run_id == run.id,
                    RoleAssignmentRow.plan_version == run.plan_version,
                    RoleAssignmentRow.assignment_version == run.assignment_version,
                )
            ).all()
        )
        if {a.role_id for a in assignments} != {r.role_id for r in spec.roles} or any(
            a.confirmed_at is None for a in assignments
        ):
            raise InstanceError(
                "assignments_incomplete", "Current confirmed assignment set required"
            )
        if stage_id is None:
            stage_id = next((physical(s.stage_id) for s in spec.stages if not s.dependencies), None)
        definition = definitions.get(stage_id)
        blocked = None
        gate_refs = []
        if definition is None:
            blocked = "stage_not_in_plan"
        else:
            stage = self.session.get(StageRow, stage_id)
            if (
                stage is None
                or stage.plan_version != run.plan_version
                or stage.assignment_version != run.assignment_version
                or stage.gate_policy_version != run.gate_policy_version
            ):
                blocked = "stale_stage"
            elif stage.write_access_closed or stage.status not in {
                "pending",
                "ready",
                "executing",
                "stopping",
                "reviewing",
            }:
                blocked = "stage_writes_closed"
            from agentforge.storage.models import StageSubmissionRow

            if work_package_id and stage:
                submissions = list(
                    self.session.scalars(
                        select(StageSubmissionRow).where(
                            StageSubmissionRow.stage_id == stage.id,
                            StageSubmissionRow.plan_version == run.plan_version,
                            StageSubmissionRow.generation == run.active_generation,
                        )
                    ).all()
                )
                for submission in submissions:
                    if work_package_id not in submission.work_package_ids:
                        continue
                    for ref in submission.attempt_refs:
                        attempt = self.session.get(InstanceAttemptRow, ref)
                        instance = (
                            self.session.get(AgentInstanceRow, attempt.instance_id)
                            if attempt
                            else None
                        )
                        if attempt and instance and instance.current_attempt_id == attempt.id:
                            blocked = "work_package_submitted"
            needed: dict[str, set[str] | None] = {
                physical(dep): None for dep in definition.dependencies
            }
            package = (
                next((w for w in spec.work_packages if w.id == work_package_id), None)
                if work_package_id
                else None
            )
            if work_package_id and (package is None or package.stage_id != definition.stage_id):
                blocked = "work_package_not_in_stage"
            if package:
                for dep in package.depends_on:
                    producer = next((w for w in spec.work_packages if w.id == dep), None)
                    if producer is None:
                        blocked = "dependency_missing"
                        continue
                    key = physical(producer.stage_id)
                    if key not in needed:
                        needed[key] = set()
                    selected = needed[key]
                    if selected is not None:
                        selected.add(dep)
            for upstream_id in needed:
                upstream = self.session.get(StageRow, upstream_id)
                releases = list(
                    self.session.scalars(
                        select(ReleaseRecordRow).where(ReleaseRecordRow.stage_id == upstream_id)
                    ).all()
                )
                valid = (
                    releases
                    if upstream
                    and upstream.plan_version == run.plan_version
                    and upstream.assignment_version == run.assignment_version
                    else []
                )
                covered = {wp for r in valid for wp in r.work_package_ids}
                required = set(
                    next(
                        s.required_work_packages
                        for s in spec.stages
                        if physical(s.stage_id) == upstream_id
                    )
                )
                selected = needed[upstream_id]
                required = selected if selected is not None else required
                if not required <= covered:
                    blocked = "upstream_gate_missing"
                for release in valid:
                    for member in release.candidate_versions:
                        c = self.session.get(ArtifactCandidateRow, member["candidateId"])
                        if (
                            c is None
                            or c.content_hash != member.get("contentHash")
                            or c.state in {"rejected", "superseded"}
                        ):
                            blocked = "upstream_candidate_invalid"
                    gate_refs.append(release.gate_decision_id)
        return ExecutionEligibility(
            run_id=run.id,
            allowed_stage_ids=[stage_id] if stage_id and not blocked else [],
            allowed_work_package_ids=[work_package_id] if work_package_id and not blocked else [],
            plan_version=run.plan_version,
            assignment_version=run.assignment_version,
            gate_decision_refs=gate_refs,
            blocked_reason=blocked,
            revalidate_on=["plan", "assignment", "stage", "grant", "capability", "budget"],
        )

    def assert_may_dispatch(self, *, run_id: str, stage_id: str, work_package_id: str) -> Any:
        result = self.check(run_id=run_id, stage_id=stage_id, work_package_id=work_package_id)
        if result.blocked_reason:
            raise InstanceError("execution_blocked", result.blocked_reason)
        return result
