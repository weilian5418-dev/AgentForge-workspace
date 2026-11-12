"""Authoritative stage service: evidence is loaded, never accepted as caller booleans."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.plans import PlanSpec, PlanStageDefinition
from agentforge.contracts.runs import OperationAccepted
from agentforge.contracts.stages import (
    CandidateSet,
    GateDecision,
    GateEvaluationInput,
    GateEvaluationResult,
    ReviewCycle,
    ReviewReportRequest,
    ReworkOrder,
    Stage,
    StageGatePolicy,
    StageSubmission,
    StageSubmissionRequest,
    StageUserDecision,
    StageUserDecisionRequest,
    WorkPackageCandidateRef,
)
from agentforge.stages.errors import StageError
from agentforge.stages.gate_evaluator import evaluate_gate
from agentforge.stages.repository import StageRepository
from agentforge.storage.event_log import append_fact
from agentforge.storage.models import (
    AgentInstanceRow,
    ArtifactCandidateRow,
    ArtifactValidationReportRow,
    CandidateSetRow,
    GateDecisionRow,
    GitWorkspaceRow,
    InstanceAttemptRow,
    InstanceOperationRow,
    Outbox,
    Plan,
    Project,
    ReleaseRecordRow,
    ReviewCycleRow,
    ReviewReportRow,
    ReviewRequestRow,
    ReworkOrderRow,
    Run,
    StageRow,
    StageSubmissionRow,
    StageUserDecisionRow,
)
from agentforge.storage.required import required


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class StageService:
    def __init__(self, session: Session, *, store: ArtifactStore | None = None) -> None:
        self.session = session
        self.repo = StageRepository(session)
        self.store = store

    def _bound(self, stage_id: str) -> tuple[StageRow, Run, Plan, PlanSpec, PlanStageDefinition]:
        hint = self.session.get(StageRow, stage_id)
        if hint is None:
            raise StageError("stage_not_found", stage_id)
        run = self.session.get(Run, hint.run_id, with_for_update=True, populate_existing=True)
        stage = self.session.get(StageRow, stage_id, with_for_update=True, populate_existing=True)
        if (
            run is None
            or stage is None
            or run.plan_version != stage.plan_version
            or run.assignment_version != stage.assignment_version
            or run.gate_policy_version != stage.gate_policy_version
            or run.status not in {"preparing", "running", "blocked"}
        ):
            raise StageError("stale_stage", "Stage is not bound to the current executable plan")
        plan = self.session.get(Plan, (run.id, run.plan_version))
        if plan is None or plan.status != "approved" or not plan.spec_json:
            raise StageError("plan_not_approved", "Current approved plan is required")
        project = self.session.get(Project, run.project_id)
        if project is None or project.grant_status != "active":
            raise StageError("grant_inactive", "Project access revoked")
        from agentforge.projects.grants import GrantError, require_active_grant

        try:
            require_active_grant(self.session, project.directory_grant_id)
        except GrantError as exc:
            raise StageError(exc.code, exc.message) from exc
        spec = PlanSpec.model_validate(plan.spec_json)
        definition = next(
            (d for d in spec.stages if stage.id == f"{run.id}:p{run.plan_version}:{d.stage_id}"),
            None,
        )
        if definition is None:
            raise StageError("stale_stage", "Stage is not a member of the approved plan")
        if (
            definition.gate_policy.model_dump()
            != StageGatePolicy.model_validate(stage.gate_policy).model_dump()
        ):
            raise StageError("stale_stage", "Stage policy differs from approved plan")
        return stage, run, plan, spec, definition

    def list_stages(self, run_id: str) -> list[Stage]:
        run = self.session.get(Run, run_id)
        return [
            self._to_stage(r)
            for r in self.repo.list_stages(run_id)
            if run and r.plan_version == run.plan_version
        ]

    def get_stage(self, stage_id: str) -> dict[str, Any]:
        row = self.repo.get_stage(stage_id)
        if row is None:
            raise StageError("stage_not_found", stage_id)
        cycle = (
            self.repo.get_review_cycle(row.current_review_cycle_id)
            if row.current_review_cycle_id
            else None
        )
        cs = (
            self.repo.get_candidate_set(row.current_candidate_set_id)
            if row.current_candidate_set_id
            else None
        )
        return {
            "stage": self._to_stage(row),
            "reviewCycle": self._to_cycle(cycle) if cycle else None,
            "candidateSet": self._to_candidate_set(cs) if cs else None,
            "writeAccessClosed": bool(row.write_access_closed),
        }

    def list_submissions(self, stage_id: str) -> Any:
        return [self._to_submission(r) for r in self.repo.list_submissions(stage_id)]

    def list_review_cycles(self, stage_id: str) -> Any:
        return [self._to_cycle(r) for r in self.repo.list_review_cycles(stage_id)]

    def list_gate_decisions(self, stage_id: str) -> Any:
        return [self._to_gate(r) for r in self.repo.list_gate_decisions(stage_id)]

    def list_rework_orders(self, stage_id: str) -> Any:
        return [self._to_rework(r) for r in self.repo.list_rework_orders(stage_id)]

    def submit(
        self, stage_id: str, body: StageSubmissionRequest, *, submitter_ref: str
    ) -> OperationAccepted:
        stage, run, plan, spec, definition = self._bound(stage_id)
        requested = set(body.work_package_ids)
        packages = {w.id: w for w in spec.work_packages if w.stage_id == definition.stage_id}
        if not requested or not requested <= packages.keys():
            raise StageError("invalid_submission", "Submission contains unknown work packages")
        digest = _hash(body.model_dump(mode="json"))
        if body.idempotency_key:
            existing = self.repo.find_submission_by_idempotency(
                stage_id=stage_id, idempotency_key=body.idempotency_key
            )
            if existing:
                if existing.note != f"request-sha256:{digest}":
                    raise StageError("idempotency_conflict", "Submission request changed")
                return OperationAccepted(
                    operation_id=existing.operation_id or existing.id,
                    status="accepted",
                    run_id=run.id,
                )
        already_released = {
            wp for release in self.repo.list_releases(stage.id) for wp in release.work_package_ids
        }
        if requested & already_released:
            raise StageError(
                "illegal_status", "Released branches require an explicit plan revision to change"
            )
        if stage.status == "reviewing" and definition.gate_policy.policy != "dependency_branch":
            raise StageError(
                "illegal_status", "Current cycle must finish before another submission"
            )
        if stage.status not in {
            "ready",
            "executing",
            "submission_requested",
            "stopping",
            "reviewing",
        }:
            raise StageError("illegal_status", "Stage cannot accept a new submission")
        attempts = [required(self.session, InstanceAttemptRow, ref) for ref in body.attempt_refs]
        if (
            not attempts
            or any(
                a is None
                or a.run_id != run.id
                or a.stage_id != stage.id
                or a.generation != run.active_generation
                or a.work_package_id not in requested
                for a in attempts
            )
            or {a.work_package_id for a in attempts} != requested
        ):
            raise StageError(
                "invalid_submission", "Current execution attempts must cover submitted packages"
            )
        if not submitter_ref.startswith("user:"):
            if any(a.instance_id != submitter_ref for a in attempts):
                raise StageError("illegal_submitter", "Submitter does not own these attempts")
        operation = _id("sop")
        row = StageSubmissionRow(
            id=_id("sub"),
            stage_id=stage.id,
            submitter_ref=submitter_ref,
            work_package_ids=sorted(requested),
            attempt_refs=body.attempt_refs,
            output_location_refs=body.output_location_refs,
            candidate_refs=[body.candidate_ref] if body.candidate_ref else [],
            stop_evidence_refs=[],
            candidate_id=body.candidate_ref,
            note=f"request-sha256:{digest}",
            operation_id=operation,
            plan_version=stage.plan_version,
            assignment_version=stage.assignment_version,
            generation=run.active_generation,
            idempotency_key=body.idempotency_key,
            submitted_at=_now(),
            created_at=_now(),
        )
        self.repo.add_submission(row)
        stage.write_access_closed = definition.gate_policy.policy == "stage_atomic"
        stage.status = "stopping"
        stage.waiting_reason = "awaiting_stop_verification"
        stage.concurrency_version += 1
        stage.updated_at = _now()
        self.session.add(
            Outbox(
                id=_id("obx"),
                run_id=run.id,
                intent_type="stage.stop_verify",
                target="stage",
                payload={"operationId": operation, "submissionId": row.id, "stageId": stage.id},
                idempotency_key=f"stage-stop:{row.id}",
                delivery_status="pending",
                attempt_count=0,
            )
        )
        self.session.flush()
        return OperationAccepted(operation_id=operation, status="accepted", run_id=run.id)

    def _check_member(self, member: Any, run: Any, spec: Any, definition: Any) -> Any:
        candidate = self.session.get(ArtifactCandidateRow, member["candidateId"])
        package = next(
            (
                w
                for w in spec.work_packages
                if w.id == member["workPackageId"] and w.stage_id == definition.stage_id
            ),
            None,
        )
        if (
            candidate is None
            or package is None
            or candidate.project_id != run.project_id
            or candidate.state in {"rejected", "superseded"}
        ):
            raise StageError(
                "candidate_invalid", "Candidate is missing, rejected or outside this stage"
            )
        attempt = (
            self.session.get(InstanceAttemptRow, candidate.attempt_id)
            if candidate.attempt_id
            else None
        )
        if (
            attempt is None
            or attempt.run_id != run.id
            or attempt.work_package_id != package.id
            or attempt.generation != run.active_generation
            or attempt.stage_id != f"{run.id}:p{run.plan_version}:{definition.stage_id}"
            or attempt.workspace_id != candidate.workspace_id
        ):
            raise StageError("candidate_invalid", "Candidate does not belong to current execution")
        output = next(
            (
                d
                for d in package.deliverables
                if d.id == candidate.contract_id and d.relative_path == candidate.relative_path
            ),
            None,
        )
        if (
            member.get("contentHash") is not None
            and member["contentHash"] != candidate.content_hash
        ):
            raise StageError("candidate_invalid", "Member hash changed")
        instance = self.session.get(AgentInstanceRow, attempt.instance_id)
        if (
            instance is None
            or instance.assignment_version != run.assignment_version
            or instance.lifecycle not in {"stopped", "closed"}
        ):
            raise StageError("stop_evidence_missing", "Candidate producer no longer quiescent")
        proofop = self.session.get(InstanceOperationRow, candidate.operation_id)
        proof = (proofop.payload_json or {}).get("stopProof", {}) if proofop else {}
        if (
            proofop is None
            or proofop.kind != "request_stop"
            or proofop.status != "succeeded"
            or proofop.attempt_id != attempt.id
            or proof.get("workspaceId") != candidate.workspace_id
            or proof.get("attemptId") != attempt.id
            or proof.get("sessionRef") != instance.session_ref
            or proof.get("generation") != run.active_generation
            or proof.get("writesQuiesced") is not True
        ):
            raise StageError("stop_evidence_missing", "Candidate stop ledger no longer matches")
        if candidate.generation != run.active_generation:
            raise StageError("candidate_invalid", "Candidate generation stale")
        if output is None:
            raise StageError("candidate_invalid", "Candidate has no approved deliverable contract")
        if self.store is None or not self.store.verify(candidate.content_hash):
            raise StageError("candidate_invalid", "Immutable candidate content is not available")
        return candidate, package, output, attempt

    def complete_stop_and_freeze(self, submission_id: str) -> ReviewCycle:
        sub = self.repo.get_submission(submission_id)
        if sub is None:
            raise StageError("submission_not_found", submission_id)
        stage, run, plan, spec, definition = self._bound(sub.stage_id)
        existing = self.repo.cycle_by_submission(submission_id)
        if existing:
            return self._to_cycle(existing)
        if stage.status != "stopping" or sub.generation != run.active_generation:
            raise StageError("illegal_status", "Submission is stale or not stopping")
        # Every submitted attempt must have an authoritative native stop operation.
        submissions = []
        for item in self.repo.list_submissions(stage.id):
            if (
                item.plan_version != stage.plan_version
                or item.assignment_version != run.assignment_version
                or item.generation != run.active_generation
            ):
                continue
            attempts = [self.session.get(InstanceAttemptRow, ref) for ref in item.attempt_refs]
            if all(
                a is not None
                and required(self.session, AgentInstanceRow, a.instance_id).current_attempt_id
                == a.id
                for a in attempts
            ):
                submissions.append(item)
        latest = {}
        for item in sorted(submissions, key=lambda s: s.submitted_at):
            for wp in item.work_package_ids:
                latest[wp] = item
        complete = set(definition.required_work_packages) <= latest.keys()
        if not complete and definition.gate_policy.policy == "stage_atomic":
            raise StageError("submission_incomplete", "Not all required packages have submitted")
        members = []
        stoprefs = []
        for wp, item in latest.items():
            refs = {
                ref
                for ref in item.attempt_refs
                if required(self.session, InstanceAttemptRow, ref).work_package_id == wp
            }
            for ref in refs:
                attempt = self.session.get(InstanceAttemptRow, ref)
                instance = (
                    self.session.get(AgentInstanceRow, attempt.instance_id) if attempt else None
                )
                stops = (
                    list(
                        self.session.scalars(
                            select(InstanceOperationRow).where(
                                InstanceOperationRow.instance_id == attempt.instance_id,
                                InstanceOperationRow.kind == "request_stop",
                                InstanceOperationRow.status == "succeeded",
                            )
                        ).all()
                    )
                    if attempt
                    else []
                )
                stop = next(
                    (
                        o
                        for o in stops
                        if o.attempt_id == ref
                        and o.payload_json.get("stopProof", {}).get("writesQuiesced") is True
                        and o.payload_json["stopProof"].get("generation") == run.active_generation
                    ),
                    None,
                )
                if instance is None or instance.lifecycle != "stopped" or stop is None:
                    raise StageError(
                        "stop_evidence_missing",
                        "Native execution and children are not confirmed stopped",
                    )
                stoprefs.append(stop.id)
            candidates = list(
                self.session.scalars(
                    select(ArtifactCandidateRow).where(ArtifactCandidateRow.attempt_id.in_(refs))
                ).all()
            )
            seen = set()
            for c in candidates:
                if c.attempt_id not in refs:
                    continue
                member = {
                    "workPackageId": wp,
                    "candidateId": c.id,
                    "artifactVersion": c.contract_version,
                    "contentHash": c.content_hash,
                    "required": True,
                }
                candidate, package, output, attempt = self._check_member(
                    member, run, spec, definition
                )
                if output.id in seen:
                    raise StageError("candidate_invalid", "Multiple candidates for one deliverable")
                seen.add(output.id)
                members.append(member)
            package = next(w for w in spec.work_packages if w.id == wp)
            if not {d.id for d in package.deliverables if d.required} <= seen:
                raise StageError("candidate_invalid", "Missing required frozen deliverables")
        if not members:
            raise StageError("candidate_invalid", "No real immutable candidates")
        environments = {
            required(self.session, ArtifactCandidateRow, m["candidateId"]).environment_hash
            for m in members
        }
        if len(environments) != 1:
            raise StageError("candidate_invalid", "Candidate environments differ")
        manifest = _hash(
            {
                "members": members,
                "contract": plan.contract_hash,
                "plan": plan.plan_hash,
                "environment": sorted(environments),
                "stops": sorted(stoprefs),
            }
        )
        cs = CandidateSetRow(
            id=_id("cs"),
            stage_id=stage.id,
            submission_id=sub.id,
            members=members,
            required_work_packages_complete=complete,
            stop_evidence_refs=sorted(stoprefs),
            contract_hash=plan.contract_hash,
            environment_fingerprint=next(iter(environments)),
            manifest_hash=manifest,
            created_at=_now(),
        )
        self.repo.add_candidate_set(cs)
        cycle = ReviewCycleRow(
            id=_id("rc"),
            stage_id=stage.id,
            submission_id=sub.id,
            candidate_set_id=cs.id,
            candidate_set_hash=manifest,
            contract_hash=cs.contract_hash,
            gate_policy_version=stage.gate_policy_version,
            environment_fingerprint=cs.environment_fingerprint,
            status="open",
            repair_round=stage.repair_round,
            opened_at=_now(),
        )
        self.repo.add_review_cycle(cycle)
        stage.current_review_cycle_id = cycle.id
        stage.current_candidate_set_id = cs.id
        stage.status = "reviewing"
        stage.waiting_reason = "awaiting_validation"
        stage.updated_at = _now()
        sub.stop_evidence_refs = sorted(stoprefs)
        sub.manifest_hash = manifest
        append_fact(
            self.session,
            run.id,
            "stage.candidate_frozen",
            {"stageId": stage.id, "candidateId": cs.id, "manifestHash": manifest},
        )
        self.session.flush()
        for role in definition.gate_policy.required_review_roles:
            reviewers = list(
                self.session.scalars(
                    select(AgentInstanceRow).where(
                        AgentInstanceRow.run_id == run.id,
                        AgentInstanceRow.role_id == role,
                        AgentInstanceRow.assignment_version == run.assignment_version,
                    )
                ).all()
            )
            if len(reviewers) != 1:
                raise StageError(
                    "illegal_reviewer", "A unique current reviewer instance is required"
                )
            self.open_review_request(
                stage_id=stage.id,
                review_cycle_id=cycle.id,
                review_role_id=role,
                legal_responder_refs=[reviewers[0].id],
            )
        return self._to_cycle(cycle)

    def open_review_request(
        self,
        *,
        stage_id: str,
        review_cycle_id: str,
        review_role_id: str,
        legal_responder_refs: list[str],
        scope: str = "semantic_audit",
    ) -> str:
        stage, run, plan, spec, definition = self._bound(stage_id)
        cycle = self.repo.get_review_cycle(review_cycle_id)
        if cycle is None or cycle.id != stage.current_review_cycle_id or cycle.status != "open":
            raise StageError("stale_cycle", "Review cycle is not current")
        if review_role_id not in definition.gate_policy.required_review_roles:
            raise StageError("illegal_reviewer", "Review role is not required by approved gate")
        for ref in legal_responder_refs:
            instance = self.session.get(AgentInstanceRow, ref)
            if (
                instance is None
                or instance.run_id != run.id
                or instance.role_id != review_role_id
                or instance.assignment_version != run.assignment_version
            ):
                raise StageError("illegal_reviewer", "Reviewer binding invalid")
        req = ReviewRequestRow(
            id=_id("rreq"),
            review_cycle_id=cycle.id,
            stage_id=stage.id,
            candidate_set_id=cycle.candidate_set_id,
            review_role_id=review_role_id,
            legal_responder_refs=legal_responder_refs,
            scope=scope,
            status="pending",
            created_at=_now(),
        )
        self.repo.add_review_request(req)
        return req.id

    def submit_review(
        self,
        stage_id: str,
        body: ReviewReportRequest,
        *,
        reviewer_instance_id: str,
        trusted: bool = False,
    ) -> str:
        if not trusted:
            raise StageError("illegal_reviewer", "Trusted instance receipt required")
        stage, run, plan, spec, definition = self._bound(stage_id)
        req = self.repo.get_review_request(body.review_request_id)
        cycle = self.repo.get_review_cycle(body.review_cycle_id)
        if (
            req is None
            or cycle is None
            or req.stage_id != stage.id
            or req.review_cycle_id != cycle.id
            or stage.current_review_cycle_id != cycle.id
            or cycle.status != "open"
            or reviewer_instance_id not in req.legal_responder_refs
            or body.manifest_hash != cycle.candidate_set_hash
            or body.contract_hash != cycle.contract_hash
            or body.candidate_set_id != cycle.candidate_set_id
            or (
                body.candidate_id is not None
                and body.candidate_id
                not in {
                    m["candidateId"]
                    for m in required(self.session, CandidateSetRow, cycle.candidate_set_id).members
                }
            )
            or (req.deadline_at and req.deadline_at <= _now())
        ):
            raise StageError(
                "review_scope_mismatch", "Review identity, request or candidate binding invalid"
            )
        existing = [
            r for r in self.repo.list_review_reports(cycle.id) if r.review_request_id == req.id
        ]
        if existing:
            prior = existing[0]
            if prior.verdict == body.verdict and prior.findings == [
                f.model_dump(mode="json", by_alias=True) for f in body.findings
            ]:
                return prior.id
            raise StageError("idempotency_conflict", "Conflicting review")
        if req.status != "pending":
            raise StageError("review_not_pending", req.status)
        row = ReviewReportRow(
            id=_id("rr"),
            review_request_id=req.id,
            review_cycle_id=cycle.id,
            reviewer_instance_id=reviewer_instance_id,
            stage_id=stage.id,
            candidate_set_id=cycle.candidate_set_id,
            manifest_hash=cycle.candidate_set_hash,
            contract_hash=cycle.contract_hash,
            findings=[f.model_dump(mode="json", by_alias=True) for f in body.findings],
            verdict=body.verdict,
            created_at=_now(),
        )
        self.repo.add_review_report(row)
        req.status = "answered"
        self._schedule_evaluation(stage, row.id)
        return row.id

    def submit_user_decision(
        self, stage_id: str, body: StageUserDecisionRequest, *, decided_by: str
    ) -> StageUserDecision:
        stage, run, plan, spec, definition = self._bound(stage_id)
        cycle = self.repo.get_review_cycle(body.review_cycle_id)
        if (
            cycle is None
            or cycle.id != stage.current_review_cycle_id
            or cycle.status != "open"
            or body.request_id != f"user:{cycle.id}"
            or (
                not definition.gate_policy.require_user_decision
                and not (
                    definition.gate_policy.conflict_resolution == "escalate_user"
                    and {r.verdict for r in self.repo.list_review_reports(cycle.id)}
                    >= {"approve", "request_changes"}
                )
            )
            or cycle.candidate_set_hash != body.candidate_set_hash
            or cycle.contract_hash != body.contract_hash
            or cycle.gate_policy_version != body.gate_policy_version
        ):
            raise StageError(
                "decision_version_mismatch", "Decision does not bind current user request"
            )
        existing = self.repo.get_user_decision_by_request(body.request_id)
        if existing:
            if existing.decision != body.decision or existing.review_cycle_id != cycle.id:
                raise StageError("idempotency_conflict", "Conflicting user decision")
            return self._to_user_decision(existing)
        row = StageUserDecisionRow(
            id=_id("sud"),
            request_id=body.request_id,
            stage_id=stage.id,
            review_cycle_id=cycle.id,
            candidate_set_hash=cycle.candidate_set_hash,
            contract_hash=cycle.contract_hash,
            gate_policy_version=cycle.gate_policy_version,
            decision=body.decision,
            note=body.note,
            decided_by=decided_by,
            decided_at=_now(),
        )
        self.repo.add_user_decision(row)
        self._schedule_evaluation(stage, row.id)
        return self._to_user_decision(row)

    def _facts(self, stage_id: str) -> Any:
        stage, run, plan, spec, definition = self._bound(stage_id)
        cycle = self.repo.get_review_cycle(stage.current_review_cycle_id or "")
        cs = self.repo.get_candidate_set(stage.current_candidate_set_id or "")
        if (
            cycle is None
            or cs is None
            or cycle.status != "open"
            or cycle.candidate_set_id != cs.id
            or cycle.contract_hash != plan.contract_hash
            or cycle.gate_policy_version != stage.gate_policy_version
            or cycle.candidate_set_hash != cs.manifest_hash
        ):
            raise StageError("stale_cycle", "Current immutable review facts are unavailable")
        if cs.manifest_hash != _hash(
            {
                "members": cs.members,
                "contract": plan.contract_hash,
                "plan": plan.plan_hash,
                "environment": [cs.environment_fingerprint],
                "stops": sorted(cs.stop_evidence_refs),
            }
        ):
            raise StageError("candidate_invalid", "Candidate set fingerprint changed")
        reports = []
        all_passed = True
        failed = False
        for member in cs.members:
            candidate, package, output, attempt = self._check_member(member, run, spec, definition)
            if (
                member.get("contentHash") != candidate.content_hash
                or candidate.environment_hash != cs.environment_fingerprint
            ):
                raise StageError("candidate_invalid", "Candidate member fingerprint mismatch")
            valid = list(
                self.session.scalars(
                    select(ArtifactValidationReportRow).where(
                        ArtifactValidationReportRow.candidate_id == candidate.id
                    )
                ).all()
            )
            required = [v for v in output.validators if v.required]
            covered = {criterion for validator in required for criterion in validator.criterion_ids}
            if not {c.id for c in output.criteria if c.required} <= covered:
                all_passed = False
            if any(v.config for v in required):
                # Legacy validation reports have no parameter fingerprint: cannot reuse as proof.
                all_passed = False
            if output.required and not required:
                all_passed = False
            for validator in required:
                matching = [
                    r
                    for r in valid
                    if r.candidate_id == candidate.id
                    and r.validator_id == validator.id
                    and r.validator_version == validator.version
                    and r.content_hash == candidate.content_hash
                    and r.contract_hash == candidate.contract_hash
                    and r.environment_hash == candidate.environment_hash
                    and r.source == "validator_registry"
                    and r.evidence_refs
                ]
                if not matching:
                    all_passed = False
                    continue
                latest = max(matching, key=lambda r: r.created_at)
                reports.append(latest.id)
                failed = failed or latest.status == "failed"
                all_passed = all_passed and latest.status == "passed"
        requests = self.repo.list_review_requests(cycle.id)
        reviews = self.repo.list_review_reports(cycle.id)
        required_roles = set(definition.gate_policy.required_review_roles)
        missing_roles = required_roles - {r.review_role_id for r in requests}
        good = [
            r
            for r in reviews
            if r.manifest_hash == cs.manifest_hash
            and r.contract_hash == cs.contract_hash
            and any(
                q.id == r.review_request_id and r.reviewer_instance_id in q.legal_responder_refs
                for q in requests
            )
        ]
        user = self.repo.latest_user_decision_for_cycle(cycle.id)
        from decimal import Decimal

        from agentforge.storage.models import BudgetAccountRow

        accounts = list(
            self.session.scalars(
                select(BudgetAccountRow).where(
                    BudgetAccountRow.scope_type == "run", BudgetAccountRow.scope_id == run.id
                )
            ).all()
        )
        exhausted = any(
            a.status != "open"
            or (a.token_limit is not None and a.settled_tokens + a.reserved_tokens >= a.token_limit)
            or (
                a.hard_limit is not None
                and Decimal(a.settled_amount) + Decimal(a.reserved_amount) >= Decimal(a.hard_limit)
            )
            for a in accounts
        )
        inp = GateEvaluationInput(
            stage_id=stage.id,
            plan_version=stage.plan_version,
            assignment_version=stage.assignment_version or 0,
            gate_policy=definition.gate_policy,
            candidate_set=self._to_candidate_set(cs),
            contract_hash=plan.contract_hash,
            validation_report_refs=reports,
            validation_passed_required=all_passed,
            validation_has_required_failure=failed,
            review_request_ids=[r.id for r in requests]
            + [f"missing-role:{r}" for r in sorted(missing_roles)],
            answered_review_request_ids=[r.review_request_id for r in good if r.review_request_id],
            review_verdicts=[cast(Any, r.verdict) for r in good],
            user_decision=self._to_user_decision(user) if user else None,
            user_decision_required=definition.gate_policy.require_user_decision
            or (
                definition.gate_policy.conflict_resolution == "escalate_user"
                and {r.verdict for r in good} >= {"approve", "request_changes"}
            ),
            repair_round=stage.repair_round,
            stop_verified=bool(cs.stop_evidence_refs),
            budget_exhausted=exhausted,
        )
        return stage, cycle, cs, inp

    def evaluate(self, stage_id: str) -> GateEvaluationResult:
        return evaluate_gate(self._facts(stage_id)[3])

    def commit_release(
        self,
        stage_id: str,
        evaluation: GateEvaluationResult | None = None,
        *,
        actor: str = "stage_controller",
    ) -> Any:
        bound = self._bound(stage_id)[0]
        prior = next(
            (
                g
                for g in self.repo.list_gate_decisions(stage_id)
                if g.review_cycle_id == bound.current_review_cycle_id and g.result == "released"
            ),
            None,
        )
        if prior is not None:
            return self._to_gate(prior)
        stage, cycle, cs, inp = self._facts(stage_id)
        actual = evaluate_gate(inp)
        if actual.outcome != "release":
            raise StageError("not_releasable", actual.reason)
        if evaluation is not None and evaluation.model_dump() != actual.model_dump():
            raise StageError(
                "stale_evaluation", "Caller evaluation differs from current locked facts"
            )
        if self.repo.list_gate_decisions(stage_id) and any(
            g.review_cycle_id == cycle.id and g.result == "released"
            for g in self.repo.list_gate_decisions(stage_id)
        ):
            raise StageError("conflicting_release", "Cycle already released")
        allowed = actual.allowed_release_work_package_ids
        if not allowed or not set(allowed) <= {m["workPackageId"] for m in cs.members}:
            raise StageError("not_releasable", "Invalid release scope")
        decision = GateDecisionRow(
            id=_id("gd"),
            stage_id=stage.id,
            review_cycle_id=cycle.id,
            candidate_set_id=cs.id,
            manifest_hash=cs.manifest_hash,
            contract_hash=cs.contract_hash,
            gate_policy_version=cycle.gate_policy_version,
            validation_refs=inp.validation_report_refs,
            review_refs=[r.id for r in self.repo.list_review_reports(cycle.id)],
            user_decision_ref=inp.user_decision.decision_id if inp.user_decision else None,
            allowed_consume_work_package_ids=allowed,
            result="released",
            reason=actual.reason,
            decided_at=_now(),
        )
        self.repo.add_gate_decision(decision)
        release = ReleaseRecordRow(
            id=_id("rel"),
            stage_id=stage.id,
            review_cycle_id=cycle.id,
            gate_decision_id=decision.id,
            scope_kind=inp.gate_policy.policy,
            work_package_ids=allowed,
            candidate_versions=[m for m in cs.members if m["workPackageId"] in allowed],
            shared_interface_still_blocked=False,
            created_at=_now(),
        )
        self.repo.add_release(release)
        append_fact(
            self.session,
            stage.run_id,
            "stage.released",
            {
                "stageId": stage.id,
                "candidateId": cs.id,
                "manifestHash": cs.manifest_hash,
                "gateResult": "released",
            },
        )
        stage.status = "released" if cs.required_work_packages_complete else "reviewing"
        stage.write_access_closed = cs.required_work_packages_complete
        stage.waiting_reason = "none"
        stage.concurrency_version += 1
        stage.updated_at = _now()
        cycle.status = "decided"
        cycle.closed_at = _now()
        self.session.add(
            Outbox(
                id=_id("obx"),
                run_id=stage.run_id,
                intent_type="stage.released",
                target="eligibility",
                payload={"operationId": release.id, "releaseId": release.id, "stageId": stage.id},
                idempotency_key=f"released:{release.id}",
                delivery_status="pending",
                attempt_count=0,
            )
        )
        from agentforge.driver.signals import record_wake_signal

        record_wake_signal(
            self.session,
            run_id=stage.run_id,
            source_kind="stage_gate",
            source_ref=decision.id,
            binding={"stageId": stage.id, "releaseId": release.id},
            priority=40,
        )
        self.session.flush()
        return self._to_gate(decision)

    def apply_evaluation(
        self, stage_id: str, evaluation: GateEvaluationResult | None = None, **_unused: Any
    ) -> Any:
        stage = self._bound(stage_id)[0]
        previous_cycle = (
            self.repo.get_review_cycle(stage.current_review_cycle_id)
            if stage.current_review_cycle_id
            else None
        )
        if previous_cycle and previous_cycle.status == "decided":
            decision = next(
                (
                    g
                    for g in self.repo.list_gate_decisions(stage_id)
                    if g.review_cycle_id == previous_cycle.id and g.result == "released"
                ),
                None,
            )
            if decision:
                return {"gateDecision": self._to_gate(decision), "duplicate": True}
        if previous_cycle and previous_cycle.status == "superseded" and stage.status == "rework":
            order = next(
                (
                    r
                    for r in self.repo.list_rework_orders(stage_id)
                    if r.source_review_cycle_id == previous_cycle.id
                ),
                None,
            )
            if order:
                return {"reworkOrder": self._to_rework(order), "duplicate": True}
        stage, cycle, cs, inp = self._facts(stage_id)
        actual = evaluate_gate(inp)
        if evaluation is not None and evaluation.model_dump() != actual.model_dump():
            raise StageError("stale_evaluation", "Evaluation no longer matches")
        if actual.outcome == "release":
            return {"gateDecision": self.commit_release(stage_id, actual)}
        if actual.outcome == "rework":
            if stage.repair_round >= inp.gate_policy.max_repair_rounds:
                raise StageError("repair_rounds_exhausted", "Rework limit")
            digest = actual.rework_problem_digest or _hash(actual.model_dump())
            existing = self.repo.find_rework(cycle_id=cycle.id, problem_digest=digest)
            if existing:
                return {"reworkOrder": self._to_rework(existing), "duplicate": True}
            # Explicitly keep writes closed until a fresh attempt/workspace is prepared by D09/D07.
            stage.status = "rework"
            stage.write_access_closed = True
            cycle.status = "superseded"
            cycle.closed_at = _now()
            self.session.add(
                ReworkOrderRow(
                    id=_id("rw"),
                    stage_id=stage.id,
                    source_review_cycle_id=cycle.id,
                    source_candidate_set_id=cs.id,
                    target_work_package_id=stage.required_work_packages[0],
                    target_role_id=next(
                        w.owner_role_id
                        for w in PlanSpec.model_validate(
                            required(
                                self.session, Plan, (stage.run_id, stage.plan_version)
                            ).spec_json
                        ).work_packages
                        if w.id == stage.required_work_packages[0]
                    ),
                    problem_digest=digest,
                    problems=[{"reason": actual.reason}],
                    remaining_rounds=inp.gate_policy.max_repair_rounds - stage.repair_round,
                    status="open",
                    created_at=_now(),
                )
            )
        elif actual.outcome == "blocked":
            stage.status = "blocked"
        stage.waiting_reason = (
            "awaiting_review" if "review" in actual.reason else "awaiting_validation"
        )
        return {"status": actual.outcome, "evaluation": actual}

    def _schedule_evaluation(self, stage: Any, cause: Any) -> Any:
        self.session.add(
            Outbox(
                id=_id("obx"),
                run_id=stage.run_id,
                intent_type="stage.evaluate",
                target="stage",
                payload={"operationId": cause, "stageId": stage.id},
                idempotency_key=f"evaluate:{cause}",
                delivery_status="pending",
                attempt_count=0,
            )
        )

    def accept_rework(self, rework_order_id: str, *, workspace_id: str) -> None:
        order = self.repo.get_rework(rework_order_id)
        if order is None:
            raise StageError("rework_not_found", rework_order_id)
        stage, run, plan, spec, definition = self._bound(order.stage_id)
        if order.status == "accepted":
            return
        if stage.status != "rework" or order.status != "open":
            raise StageError("illegal_status", "Rework is not open")
        policy = definition.gate_policy
        if stage.repair_round >= policy.max_repair_rounds:
            raise StageError("repair_rounds_exhausted", "Repair limit")
        workspace = self.session.get(
            GitWorkspaceRow, workspace_id, with_for_update=True, populate_existing=True
        )
        if (
            workspace is None
            or workspace.project_id != run.project_id
            or workspace.baseline_id != run.baseline_id
            or workspace.role_id != order.target_role_id
            or workspace.status != "active"
        ):
            raise StageError(
                "workspace_unbound", "Prepare a new authorized worktree for the original role"
            )
        members = required(self.session, CandidateSetRow, order.source_candidate_set_id).members
        if any(
            required(self.session, ArtifactCandidateRow, m["candidateId"]).workspace_id
            == workspace_id
            for m in members
        ):
            raise StageError("workspace_unbound", "Frozen candidate workspaces cannot be edited")
        instances = list(
            self.session.scalars(
                select(AgentInstanceRow).where(
                    AgentInstanceRow.run_id == run.id,
                    AgentInstanceRow.role_id == order.target_role_id,
                    AgentInstanceRow.assignment_version == run.assignment_version,
                )
            ).all()
        )
        if len(instances) != 1 or instances[0].lifecycle != "closed":
            raise StageError(
                "stop_evidence_missing",
                "Close the stopped original session before starting a replacement",
            )
        instance = instances[0]
        instance.workspace_id = workspace.id
        instance.native_session_id = None
        instance.session_ref = None
        instance.current_attempt_id = None
        instance.lifecycle = "created"
        instance.status = "created"
        instance.activity = "idle"
        instance.generation = run.active_generation
        op = InstanceOperationRow(
            id=_id("iop"),
            instance_id=instance.id,
            kind="create_session",
            status="prepared",
            idempotency_key=f"rework-session:{order.id}",
            payload_json={"reworkOrderId": order.id},
        )
        self.session.add(op)
        self.session.add(
            Outbox(
                id=_id("obx"),
                run_id=run.id,
                intent_type="instance.create_session",
                target="instance",
                payload={"operationId": op.id, "instanceId": instance.id},
                idempotency_key=f"create:{op.id}",
                delivery_status="pending",
                attempt_count=0,
            )
        )
        order.status = "accepted"
        stage.repair_round += 1
        stage.status = "ready"
        stage.waiting_reason = "none"
        stage.write_access_closed = False
        stage.updated_at = _now()
        # Archive old submitted attempts; new executions must submit fresh attempts/candidates.
        stage.current_review_cycle_id = None
        stage.current_candidate_set_id = None

    # --- mappers ---

    @staticmethod
    def _to_stage(row: StageRow) -> Stage:
        return Stage.model_validate(
            {
                "stageId": row.id,
                "runId": row.run_id,
                "planVersion": row.plan_version,
                "assignmentVersion": row.assignment_version,
                "gatePolicyVersion": row.gate_policy_version,
                "dependencies": list(row.dependencies or []),
                "requiredWorkPackages": list(row.required_work_packages or []),
                "gatePolicy": row.gate_policy,
                "status": row.status,
                "waitingReason": row.waiting_reason or "none",
                "concurrencyVersion": row.concurrency_version or 1,
                "repairRound": row.repair_round or 0,
                "currentReviewCycleId": row.current_review_cycle_id,
                "currentCandidateSetId": row.current_candidate_set_id,
                "currentCandidateId": row.current_candidate_id,
            }
        )

    @staticmethod
    def _to_submission(row: StageSubmissionRow) -> StageSubmission:
        return StageSubmission.model_validate(
            {
                "submissionId": row.id,
                "stageId": row.stage_id,
                "submitterRef": row.submitter_ref or "unknown",
                "workPackageIds": list(row.work_package_ids or ["unknown"]),
                "attemptRefs": list(row.attempt_refs or []),
                "outputLocationRefs": list(row.output_location_refs or []),
                "candidateRefs": list(row.candidate_refs or []),
                "note": row.note,
                "operationId": row.operation_id,
                "planVersion": row.plan_version,
                "assignmentVersion": row.assignment_version,
                "generation": row.generation,
                "submittedAt": row.submitted_at,
                "stopEvidenceRefs": list(row.stop_evidence_refs or []),
                "candidateId": row.candidate_id,
                "manifestHash": row.manifest_hash,
            }
        )

    @staticmethod
    def _to_candidate_set(row: CandidateSetRow) -> CandidateSet:
        members = [
            WorkPackageCandidateRef.model_validate(m) if isinstance(m, dict) else m
            for m in (row.members or [])
        ]
        return CandidateSet(
            candidate_set_id=row.id,
            stage_id=row.stage_id,
            submission_id=row.submission_id,
            members=members,
            required_work_packages_complete=bool(row.required_work_packages_complete),
            upstream_input_versions=list(row.upstream_input_versions or []),
            baseline_or_integration_ref=row.baseline_or_integration_ref,
            stop_evidence_refs=list(row.stop_evidence_refs or []),
            contract_hash=row.contract_hash,
            environment_fingerprint=row.environment_fingerprint,
            manifest_hash=row.manifest_hash,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_cycle(row: ReviewCycleRow) -> ReviewCycle:
        return ReviewCycle.model_validate(
            {
                "reviewCycleId": row.id,
                "stageId": row.stage_id,
                "submissionId": row.submission_id,
                "candidateSetId": row.candidate_set_id,
                "candidateSetHash": row.candidate_set_hash,
                "contractHash": row.contract_hash,
                "gatePolicyVersion": row.gate_policy_version,
                "environmentFingerprint": row.environment_fingerprint,
                "status": row.status,
                "repairRound": row.repair_round or 0,
                "openedAt": row.opened_at,
                "closedAt": row.closed_at,
            }
        )

    @staticmethod
    def _to_gate(row: GateDecisionRow) -> GateDecision:
        return GateDecision.model_validate(
            {
                "decisionId": row.id,
                "stageId": row.stage_id,
                "reviewCycleId": row.review_cycle_id,
                "candidateSetId": row.candidate_set_id,
                "candidateId": row.candidate_id,
                "manifestHash": row.manifest_hash,
                "contractHash": row.contract_hash,
                "gatePolicyVersion": row.gate_policy_version,
                "validationRefs": list(row.validation_refs or []),
                "reviewRefs": list(row.review_refs or []),
                "userDecisionRef": row.user_decision_ref,
                "result": row.result,
                "allowedConsumeWorkPackageIds": list(row.allowed_consume_work_package_ids or []),
                "reason": row.reason,
                "decidedAt": row.decided_at,
            }
        )

    @staticmethod
    def _to_rework(row: ReworkOrderRow) -> ReworkOrder:
        return ReworkOrder.model_validate(
            {
                "reworkOrderId": row.id,
                "stageId": row.stage_id,
                "sourceReviewCycleId": row.source_review_cycle_id,
                "sourceCandidateSetId": row.source_candidate_set_id,
                "targetWorkPackageId": row.target_work_package_id,
                "targetRoleId": row.target_role_id,
                "problemDigest": row.problem_digest,
                "problems": row.problems or [{"reason": "unspecified"}],
                "allowedModifyScopes": list(row.allowed_modify_scopes or []),
                "checksToRerun": list(row.checks_to_rerun or []),
                "remainingRounds": row.remaining_rounds,
                "involvesPlanOrInterfaceChange": bool(row.involves_plan_or_interface_change),
                "status": row.status,
                "createdAt": row.created_at,
            }
        )

    @staticmethod
    def _to_user_decision(row: StageUserDecisionRow) -> StageUserDecision:
        return StageUserDecision.model_validate(
            {
                "decisionId": row.id,
                "requestId": row.request_id,
                "stageId": row.stage_id,
                "reviewCycleId": row.review_cycle_id,
                "candidateSetHash": row.candidate_set_hash,
                "contractHash": row.contract_hash,
                "gatePolicyVersion": row.gate_policy_version,
                "decision": row.decision,
                "note": row.note,
                "decidedBy": row.decided_by,
                "decidedAt": row.decided_at,
            }
        )
