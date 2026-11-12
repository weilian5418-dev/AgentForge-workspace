"""Planning application services: run create, propose, revise, assign, confirm."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from agentforge.contracts.agents import RoleAssignment, RoleAssignmentPutItem
from agentforge.contracts.plans import (
    CompilationReport,
    ConfirmationRequest,
    PlanDecisionRequest,
    PlanProposal,
    PlanProposalRequest,
    PlanResponse,
    PlanRevisionRequest,
    PlanSpec,
)
from agentforge.contracts.runs import RunAcceptedResponse, RunCreateRequest, RunSnapshotResponse
from agentforge.planning.compiler import CompilerFacts, compile_plan
from agentforge.planning.context import PlanningContextBuilder, PlanningContextError
from agentforge.planning.hashing import compute_contract_hash, compute_plan_hash
from agentforge.planning.proposals import ProposalError, ProposalGenerator
from agentforge.planning.repository import PlanRepository, plan_row_from_spec
from agentforge.projects.grants import GrantError, require_active_grant
from agentforge.storage.models import (
    Plan,
    PlanConfirmationRequestRow,
    PlanDecisionRow,
    PlanProposalRow,
    Project,
    RoleAssignmentRow,
    Run,
)


class PlanningError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


class PlanningService:
    """Coordinates planning without starting agents or writing user projects."""

    def __init__(
        self,
        session: Session,
        *,
        proposal_generator: ProposalGenerator | None = None,
        known_validator_ids: frozenset[str] | None = None,
    ) -> None:
        self.session = session
        self.repo = PlanRepository(session)
        self.context_builder = PlanningContextBuilder(session)
        self.proposals = proposal_generator or ProposalGenerator()
        self.known_validator_ids = known_validator_ids or frozenset(
            {"markdown", "syntax", "pptx_structure", "generic"}
        )

    def create_run(self, body: RunCreateRequest) -> RunAcceptedResponse:
        project = self.session.get(Project, body.project_id)
        if project is None:
            raise PlanningError("project_not_found", "Project not found")
        if project.grant_status != "active":
            raise PlanningError("grant_inactive", "Directory grant inactive")
        try:
            require_active_grant(self.session, project.directory_grant_id)
        except GrantError as exc:
            raise PlanningError(exc.code, exc.message) from exc

        if body.idempotency_key:
            existing = self.repo.get_run_by_idempotency(body.project_id, body.idempotency_key)
            if existing is not None:
                return RunAcceptedResponse(
                    run_id=existing.id,
                    status=existing.status,  # type: ignore[arg-type]
                    status_version=existing.status_version,
                    events_url=f"/api/v1/runs/{existing.id}/events",
                )

        baseline_id = body.baseline_id or project.current_baseline_id
        if not baseline_id:
            raise PlanningError("baseline_required", "Project has no baseline to pin")

        run = Run(
            id=f"run_{uuid.uuid4().hex[:20]}",
            project_id=body.project_id,
            goal=body.goal,
            mode=body.mode,
            status="planning",
            status_version=1,
            plan_version=None,
            assignment_version=None,
            gate_policy_version=None,
            active_generation=1,
            last_sequence=0,
            execution_profile=body.execution_profile,
            failure_policy=body.failure_policy,
            budget=body.budget.model_dump(mode="json", by_alias=True),
            planning_provider_profile_id=body.planning_provider_profile_id,
            baseline_id=baseline_id,
            idempotency_key=body.idempotency_key,
            input_refs=list(body.input_refs),
            outputs=list(body.outputs),
            created_at=_now(),
            updated_at=_now(),
        )
        self.repo.add_run(run)
        self.session.flush()
        return RunAcceptedResponse(
            run_id=run.id,
            status="planning",
            status_version=1,
            events_url=f"/api/v1/runs/{run.id}/events",
        )

    def get_run(self, run_id: str) -> RunSnapshotResponse:
        run = self._require_run(run_id)
        return self._run_snapshot(run)

    def propose(
        self, run_id: str, body: PlanProposalRequest | None = None
    ) -> PlanResponse:
        run = self._require_run(run_id)
        if run.status not in {"planning", "awaiting_confirmation", "blocked"}:
            raise PlanningError("invalid_state", f"Cannot propose in status={run.status}")
        req = body or PlanProposalRequest.model_validate({"source": "auto"})
        try:
            context = self.context_builder.build_for_run(run, persist=True)
            proposal = self.proposals.generate(run=run, context=context, source=req.source)
        except (PlanningContextError, ProposalError) as exc:
            raise PlanningError(exc.code, exc.message) from exc

        self.repo.add_proposal(
            PlanProposalRow(
                id=proposal.proposal_id,
                run_id=run.id,
                context_id=context.context_id,
                source=proposal.source,
                model_call_id=proposal.model_call_id,
                draft_json=proposal.draft.model_dump(mode="json", by_alias=True),
                diagnostics_json=[
                    d.model_dump(mode="json", by_alias=True) for d in proposal.diagnostics
                ],
                status="active",
                created_at=proposal.created_at,
            )
        )
        return self._persist_compiled_revision(
            run,
            proposal.draft,
            context_id=context.context_id,
            summary=req.note or proposal.draft.goal,
        )

    def revise(self, run_id: str, body: PlanRevisionRequest) -> PlanResponse:
        run = self._lock_run(run_id)
        if run.status_version != body.expected_status_version:
            raise PlanningError("status_version_conflict", "Run statusVersion mismatch")
        if run.plan_version != body.expected_plan_version:
            raise PlanningError("plan_version_conflict", "expectedPlanVersion mismatch")
        self._supersede_pending_confirmations(run)
        return self._persist_compiled_revision(
            run,
            body.spec,
            context_id=self.repo.latest_plan(run.id).context_id if self.repo.latest_plan(run.id) else None,
            summary=body.note or body.spec.goal,
            expected_current=body.expected_plan_version,
        )

    def put_assignments(
        self,
        run_id: str,
        *,
        plan_version: int,
        items: list[RoleAssignmentPutItem],
        capability_check_refs: list[str] | None = None,
    ) -> list[RoleAssignment]:
        run = self._lock_run(run_id)
        plan = self.repo.get_plan(run_id, plan_version)
        if plan is None or plan.spec_json is None:
            raise PlanningError("plan_not_found", "Plan version not found")
        if plan.status == "approved":
            raise PlanningError("plan_already_approved", "Cannot reassign an approved plan in place")
        spec = PlanSpec.model_validate(plan.spec_json)
        role_ids = {r.role_id for r in spec.roles}
        assigned = {i.role_id for i in items}
        if assigned != role_ids:
            raise PlanningError(
                "assignment_incomplete",
                "Assignment set must cover exactly the plan roles",
            )
        next_version = (run.assignment_version or 0) + 1
        now = _now()
        out: list[RoleAssignment] = []
        refs = list(capability_check_refs or [])
        for item in items:
            row = RoleAssignmentRow(
                id=f"asg_{uuid.uuid4().hex[:16]}",
                run_id=run_id,
                plan_version=plan_version,
                assignment_version=next_version,
                role_id=item.role_id,
                agent_profile_id=item.agent_profile_id,
                capability_check_refs=refs,
                profile_config_version=None,
                confirmed_at=None,
                created_at=now,
            )
            self.repo.add_assignment(row)
            out.append(
                RoleAssignment(
                    assignment_id=row.id,
                    run_id=run_id,
                    plan_version=plan_version,
                    assignment_version=next_version,
                    role_id=row.role_id,
                    agent_profile_id=row.agent_profile_id,
                )
            )
        run.assignment_version = next_version
        run.status_version += 1
        run.updated_at = now
        self._supersede_pending_confirmations(run)
        # Open a new confirmation request for the current plan + new assignment.
        self._open_confirmation(run, plan)
        self.session.flush()
        return out

    def decide(self, run_id: str, body: PlanDecisionRequest, *, actor: str = "local") -> PlanResponse:
        run = self._lock_run(run_id)
        req = self.repo.get_confirmation_request(body.confirmation_request_id)
        if req is None or req.run_id != run_id:
            raise PlanningError("confirmation_not_found", "Confirmation request not found")

        existing = self.repo.get_decision_for_request(req.id)
        if existing is not None:
            if existing.decision == body.decision:
                # Idempotent same decision (statusVersion may have advanced after first write).
                return self.get_plan(run_id)
            raise PlanningError("decision_conflict", "Conflicting decision for the same request")

        if run.status_version != body.status_version:
            raise PlanningError("status_version_conflict", "Run statusVersion mismatch")
        if req.status != "pending":
            raise PlanningError("confirmation_not_pending", f"Request status={req.status}")

        if (
            req.plan_version != body.plan_version
            or req.plan_hash != body.plan_hash
            or req.assignment_version != body.assignment_version
            or req.gate_policy_version != body.gate_policy_version
            or req.status_version != body.status_version
        ):
            raise PlanningError(
                "confirmation_scope_mismatch",
                "Decision does not match current confirmation scope",
            )

        plan = self.repo.get_plan(run_id, req.plan_version)
        if plan is None or plan.plan_hash != req.plan_hash:
            raise PlanningError("plan_hash_mismatch", "Plan hash no longer matches request")
        if plan.compilation_json:
            report = CompilationReport.model_validate(plan.compilation_json)
            if report.status != "valid":
                raise PlanningError("compilation_not_valid", "Cannot approve a non-valid plan")

        if body.decision == "approve":
            if not run.assignment_version or run.assignment_version != req.assignment_version:
                raise PlanningError("assignment_required", "Complete assignments before approve")
            assignments = self.repo.list_assignments(run_id, req.assignment_version)
            if not assignments:
                raise PlanningError("assignment_required", "No assignments for this version")

        self.repo.add_decision(
            PlanDecisionRow(
                id=f"pdec_{uuid.uuid4().hex[:16]}",
                request_id=req.id,
                run_id=run_id,
                actor=actor,
                decision=body.decision,
                note=body.note,
                decided_at=_now(),
            )
        )
        req.status = "approved" if body.decision == "approve" else "rejected"
        if body.decision == "approve":
            plan.status = "approved"
            run.status = "preparing"
            run.plan_version = plan.version
            run.gate_policy_version = req.gate_policy_version
            # Mark prior proposed plans superseded except current.
            for older in self.repo.list_plans(run_id):
                if older.version != plan.version and older.status == "proposed":
                    older.status = "superseded"
        else:
            plan.status = "rejected"
            # Remain awaiting_confirmation / planning until revise.
            if run.status == "awaiting_confirmation":
                run.status = "planning"
        run.status_version += 1
        run.updated_at = _now()
        self.session.flush()
        return self.get_plan(run_id)

    def get_plan(self, run_id: str) -> PlanResponse:
        run = self._require_run(run_id)
        plan = self.repo.latest_plan(run_id)
        if plan is None or plan.spec_json is None:
            raise PlanningError("plan_not_found", "No plan for run")
        return self._to_plan_response(run, plan)

    def _persist_compiled_revision(
        self,
        run: Run,
        spec: PlanSpec,
        *,
        context_id: str | None,
        summary: str,
        expected_current: int | None = None,
    ) -> PlanResponse:
        report = compile_plan(
            spec,
            CompilerFacts(known_validator_ids=self.known_validator_ids),
        )
        plan_hash = compute_plan_hash(spec)
        deliverables = [d for wp in spec.work_packages for d in wp.deliverables]
        contract_hash = compute_contract_hash(deliverables)
        current = run.plan_version or 0
        if expected_current is not None and current != expected_current:
            raise PlanningError("plan_version_conflict", "Plan version changed")
        next_version = current + 1
        # Supersede previous proposed plan row (keep immutable history).
        if current >= 1:
            prev = self.repo.get_plan(run.id, current)
            if prev is not None and prev.status == "proposed":
                prev.status = "superseded"
        row = plan_row_from_spec(
            run_id=run.id,
            version=next_version,
            plan_hash=plan_hash,
            contract_hash=contract_hash,
            status="proposed",
            summary=summary[:4096],
            spec=spec.model_dump(mode="json", by_alias=True),
            compilation=report.model_dump(mode="json", by_alias=True),
            context_id=context_id,
            baseline_id=spec.baseline_id,
            input_ids=list(spec.input_ids),
            gate_policy_version=spec.gate_policy_version,
            supersedes_version=current if current >= 1 else None,
        )
        self.repo.add_plan(row)
        run.plan_version = next_version
        run.gate_policy_version = spec.gate_policy_version
        run.assignment_version = None  # force re-assign after plan change
        if report.status == "valid":
            run.status = "awaiting_confirmation"
        elif report.status in {"blocked", "invalid"}:
            run.status = "blocked"
        else:
            run.status = "planning"
        run.status_version += 1
        run.updated_at = _now()
        self._supersede_pending_confirmations(run)
        if report.status == "valid":
            self._open_confirmation(run, row)
        self.session.flush()
        return self._to_plan_response(run, row)

    def _open_confirmation(self, run: Run, plan: Plan) -> PlanConfirmationRequestRow | None:
        if not plan.plan_hash:
            raise PlanningError("plan_hash_missing", "Plan hash required for confirmation")
        if not run.assignment_version:
            # Confirmation opens only after assignments exist.
            return None
        req = PlanConfirmationRequestRow(
            id=f"creq_{uuid.uuid4().hex[:16]}",
            run_id=run.id,
            plan_version=plan.version,
            plan_hash=plan.plan_hash,
            assignment_version=run.assignment_version,
            gate_policy_version=plan.gate_policy_version or 1,
            status_version=run.status_version,
            compilation_report_ref=None,
            capability_check_refs=[],
            status="pending",
            failure_reason=None,
            created_at=_now(),
            expires_at=None,
        )
        self.repo.add_confirmation_request(req)
        run.current_confirmation_request_id = req.id
        return req

    def _supersede_pending_confirmations(self, run: Run) -> None:
        pending = self.repo.pending_confirmation_for_run(run.id)
        if pending is not None:
            pending.status = "superseded"
            if run.current_confirmation_request_id == pending.id:
                run.current_confirmation_request_id = None

    def _require_run(self, run_id: str) -> Run:
        run = self.repo.get_run(run_id)
        if run is None:
            raise PlanningError("run_not_found", "Run not found")
        return run

    def _lock_run(self, run_id: str) -> Run:
        run = self.repo.lock_run(run_id)
        if run is None:
            raise PlanningError("run_not_found", "Run not found")
        return run

    def _run_snapshot(self, run: Run) -> RunSnapshotResponse:
        from agentforge.contracts.common import BudgetLimits

        return RunSnapshotResponse(
            id=run.id,
            project_id=run.project_id,
            goal=run.goal,
            mode=run.mode,  # type: ignore[arg-type]
            status=run.status,  # type: ignore[arg-type]
            status_version=run.status_version,
            plan_version=run.plan_version,
            assignment_version=run.assignment_version,
            gate_policy_version=run.gate_policy_version,
            active_generation=run.active_generation,
            latest_sequence=run.last_sequence,
            execution_profile=run.execution_profile,  # type: ignore[arg-type]
            failure_policy=run.failure_policy,  # type: ignore[arg-type]
            budget=BudgetLimits.model_validate(run.budget or {}),
            planning_provider_profile_id=run.planning_provider_profile_id,
            baseline_id=run.baseline_id,
            input_refs=list(run.input_refs or []),
            outputs=list(run.outputs or []),
            cancel_requested_at=run.cancel_requested_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    def _to_plan_response(self, run: Run, plan: Plan) -> PlanResponse:
        spec = PlanSpec.model_validate(plan.spec_json or {})
        compilation = (
            CompilationReport.model_validate(plan.compilation_json)
            if plan.compilation_json
            else None
        )
        confirmation = None
        confirmation_state: Literal["none", "awaiting_confirmation", "approved", "rejected"] = "none"
        if run.current_confirmation_request_id:
            row = self.repo.get_confirmation_request(run.current_confirmation_request_id)
            if row is not None:
                confirmation = ConfirmationRequest(
                    request_id=row.id,
                    run_id=row.run_id,
                    plan_version=row.plan_version,
                    plan_hash=row.plan_hash,
                    assignment_version=row.assignment_version,
                    gate_policy_version=row.gate_policy_version,
                    status_version=row.status_version,
                    compilation_report_ref=row.compilation_report_ref,
                    capability_check_refs=list(row.capability_check_refs or []),
                    status=row.status,  # type: ignore[arg-type]
                    failure_reason=row.failure_reason,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                )
                if row.status == "pending":
                    confirmation_state = "awaiting_confirmation"
                elif row.status == "approved":
                    confirmation_state = "approved"
                elif row.status == "rejected":
                    confirmation_state = "rejected"
        elif plan.status == "approved":
            confirmation_state = "approved"
        elif plan.status == "rejected":
            confirmation_state = "rejected"

        assignments: list[RoleAssignment] = []
        if run.assignment_version:
            for row in self.repo.list_assignments(run.id, run.assignment_version):
                assignments.append(
                    RoleAssignment(
                        assignment_id=row.id,
                        run_id=row.run_id,
                        plan_version=row.plan_version,
                        assignment_version=row.assignment_version,
                        role_id=row.role_id,
                        agent_profile_id=row.agent_profile_id,
                        confirmed_at=row.confirmed_at,
                    )
                )

        return PlanResponse(
            run_id=run.id,
            plan_version=plan.version,
            plan_hash=plan.plan_hash or ("0" * 64),
            contract_hash=plan.contract_hash,
            status=plan.status,  # type: ignore[arg-type]
            summary=plan.summary,
            baseline_id=plan.baseline_id or spec.baseline_id,
            input_ids=list(plan.input_ids or spec.input_ids),
            context_id=plan.context_id,
            routing=spec.routing,
            roles=list(spec.roles),
            stages=list(spec.stages),
            gate_policy=spec.stages[0].gate_policy if spec.stages else None,
            work_packages=list(spec.work_packages),
            assignments=assignments,
            assignment_version=run.assignment_version,
            gate_policy_version=plan.gate_policy_version,
            compilation=compilation,
            confirmation=confirmation,
            confirmation_state=confirmation_state,
            created_at=plan.created_at,
        )
