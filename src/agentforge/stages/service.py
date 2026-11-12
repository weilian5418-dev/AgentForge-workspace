"""StageController facade: submit → stop/freeze → review → evaluate → release/rework."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from agentforge.contracts.runs import OperationAccepted
from agentforge.contracts.stages import (
    CandidateSet,
    GateDecision,
    GateEvaluationInput,
    GateEvaluationResult,
    ReleaseRecord,
    ReviewCycle,
    ReviewReportRequest,
    ReviewVerdict,
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
from agentforge.stages.transitions import assert_transition, can_transition
from agentforge.storage.models import (
    CandidateSetRow,
    GateDecisionRow,
    Outbox,
    ReleaseRecordRow,
    ReviewCycleRow,
    ReviewReportRow,
    ReviewRequestRow,
    ReworkOrderRow,
    StageRow,
    StageSubmissionRow,
    StageUserDecisionRow,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _hash(parts: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


class StageService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = StageRepository(session)

    # --- queries ---

    def list_stages(self, run_id: str) -> list[Stage]:
        return [self._to_stage(r) for r in self.repo.list_stages(run_id)]

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
            "reviewCycle": None if cycle is None else self._to_cycle(cycle),
            "candidateSet": None if cs is None else self._to_candidate_set(cs),
            "writeAccessClosed": bool(row.write_access_closed),
        }

    def list_submissions(self, stage_id: str) -> list[StageSubmission]:
        return [self._to_submission(r) for r in self.repo.list_submissions(stage_id)]

    def list_review_cycles(self, stage_id: str) -> list[ReviewCycle]:
        return [self._to_cycle(r) for r in self.repo.list_review_cycles(stage_id)]

    def list_gate_decisions(self, stage_id: str) -> list[GateDecision]:
        return [self._to_gate(r) for r in self.repo.list_gate_decisions(stage_id)]

    def list_rework_orders(self, stage_id: str) -> list[ReworkOrder]:
        return [self._to_rework(r) for r in self.repo.list_rework_orders(stage_id)]

    # --- D10.3 submit / close write / stop / freeze ---

    def submit(
        self,
        stage_id: str,
        body: StageSubmissionRequest,
        *,
        submitter_ref: str,
        plan_version: int | None = None,
        assignment_version: int | None = None,
        generation: int | None = None,
        stop_verified: bool = False,
        stop_evidence_refs: list[str] | None = None,
        contract_hash: str | None = None,
        environment_fingerprint: str | None = None,
        member_refs: list[dict[str, Any]] | None = None,
    ) -> OperationAccepted:
        stage = self.repo.get_stage(stage_id)
        if stage is None:
            raise StageError("stage_not_found", stage_id)
        if stage.status not in ("executing", "rework", "ready", "blocked"):
            if stage.status not in ("submission_requested", "stopping"):
                if not can_transition(stage.status, "submission_requested"):  # type: ignore[arg-type]
                    raise StageError("illegal_status", f"cannot submit from {stage.status}")

        key = body.idempotency_key
        if key:
            existing = self.repo.find_submission_by_idempotency(
                stage_id=stage_id, idempotency_key=key
            )
            if existing is not None:
                # Same key + different content → conflict
                if set(existing.work_package_ids or []) != set(body.work_package_ids):
                    raise StageError("idempotency_conflict", "same key, different content")
                return OperationAccepted(
                    operation_id=existing.operation_id or existing.id,
                    status="accepted",
                    run_id=stage.run_id,
                )

        op_id = _id("sop")
        sub_id = _id("sub")
        row = StageSubmissionRow(
            id=sub_id,
            stage_id=stage_id,
            submitter_ref=submitter_ref,
            work_package_ids=list(body.work_package_ids),
            attempt_refs=list(body.attempt_refs),
            output_location_refs=list(body.output_location_refs or []),
            candidate_refs=[body.candidate_ref] if body.candidate_ref else [],
            stop_evidence_refs=[],
            candidate_id=body.candidate_ref,
            note=body.note,
            operation_id=op_id,
            plan_version=plan_version or stage.plan_version,
            assignment_version=assignment_version or stage.assignment_version,
            generation=generation,
            idempotency_key=key,
            submitted_at=_now(),
            created_at=_now(),
        )
        self.repo.add_submission(row)

        # Close new development write access for this submission scope.
        stage.write_access_closed = True
        if stage.status in ("executing", "rework", "ready"):
            assert_transition(stage.status, "submission_requested")  # type: ignore[arg-type]
            stage.status = "submission_requested"
        stage.waiting_reason = "awaiting_stop_verification"
        stage.updated_at = _now()
        stage.concurrency_version = int(stage.concurrency_version or 1) + 1

        self.session.add(
            Outbox(
                id=_id("obx"),
                run_id=stage.run_id,
                event_id=None,
                intent_type="stage.stop_verify",
                target="quiescence_coordinator",
                payload={"submissionId": sub_id, "stageId": stage_id, "operationId": op_id},
                idempotency_key=f"stop_verify:{sub_id}",
                delivery_status="pending",
                attempt_count=0,
                created_at=_now(),
                updated_at=_now(),
            )
        )

        # Optional immediate freeze path when caller already has D09 stop evidence.
        if stop_verified:
            self.complete_stop_and_freeze(
                sub_id,
                stop_evidence_refs=stop_evidence_refs or ["ev_stop"],
                contract_hash=contract_hash or ("c" * 64),
                environment_fingerprint=environment_fingerprint or ("e" * 64),
                member_refs=member_refs,
            )

        return OperationAccepted(operation_id=op_id, status="accepted", run_id=stage.run_id)

    def complete_stop_and_freeze(
        self,
        submission_id: str,
        *,
        stop_evidence_refs: list[str],
        contract_hash: str,
        environment_fingerprint: str,
        member_refs: list[dict[str, Any]] | None = None,
        required_complete: bool | None = None,
    ) -> ReviewCycle:
        """After D09 stop evidence: freeze candidate set and open review cycle."""
        sub = self.repo.get_submission(submission_id)
        if sub is None:
            raise StageError("submission_not_found", submission_id)
        stage = self.repo.get_stage(sub.stage_id)
        if stage is None:
            raise StageError("stage_not_found", sub.stage_id)

        existing_cycle = self.repo.cycle_by_submission(submission_id)
        if existing_cycle is not None:
            return self._to_cycle(existing_cycle)

        if not stop_evidence_refs:
            stage.status = "blocked"
            stage.waiting_reason = "awaiting_stop_verification"
            stage.updated_at = _now()
            raise StageError("stop_evidence_missing", "cannot freeze without stop proof")

        # Parent ended but child still writing → do not freeze.
        # Callers pass empty evidence in that case; we already require non-empty.

        if stage.status == "submission_requested":
            assert_transition("submission_requested", "stopping")
            stage.status = "stopping"
        if stage.status == "stopping":
            assert_transition("stopping", "frozen")
            stage.status = "frozen"

        sub.stop_evidence_refs = list(stop_evidence_refs)
        members = member_refs or [
            {
                "workPackageId": wp,
                "candidateId": (sub.candidate_refs or [f"cand_{wp}"])[0]
                if sub.candidate_refs
                else f"cand_{wp}",
                "artifactVersion": 1,
                "required": True,
            }
            for wp in (sub.work_package_ids or [])
        ]
        required_ids = list(stage.required_work_packages or [])
        submitted = set(sub.work_package_ids or [])
        if required_complete is None:
            complete = not required_ids or set(required_ids).issubset(submitted)
        else:
            complete = required_complete

        manifest = _hash(
            {
                "members": members,
                "contract": contract_hash,
                "env": environment_fingerprint,
                "stops": stop_evidence_refs,
            }
        )
        cs_id = _id("cs")
        cs = CandidateSetRow(
            id=cs_id,
            stage_id=stage.id,
            submission_id=submission_id,
            members=members,
            required_work_packages_complete=complete,
            stop_evidence_refs=list(stop_evidence_refs),
            contract_hash=contract_hash,
            environment_fingerprint=environment_fingerprint,
            manifest_hash=manifest,
            created_at=_now(),
        )
        self.repo.add_candidate_set(cs)
        sub.manifest_hash = manifest

        cycle_id = _id("rc")
        cycle = ReviewCycleRow(
            id=cycle_id,
            stage_id=stage.id,
            submission_id=submission_id,
            candidate_set_id=cs_id,
            candidate_set_hash=manifest,
            contract_hash=contract_hash,
            gate_policy_version=int(
                stage.gate_policy_version or (stage.gate_policy or {}).get("version") or 1
            ),
            environment_fingerprint=environment_fingerprint,
            status="open",
            repair_round=int(stage.repair_round or 0),
            opened_at=_now(),
        )
        self.repo.add_review_cycle(cycle)

        assert_transition("frozen", "reviewing")
        stage.status = "reviewing"
        stage.waiting_reason = "awaiting_validation"
        stage.current_review_cycle_id = cycle_id
        stage.current_candidate_set_id = cs_id
        stage.updated_at = _now()
        return self._to_cycle(cycle)

    # --- D10.4 reviews ---

    def open_review_request(
        self,
        *,
        stage_id: str,
        review_cycle_id: str,
        review_role_id: str,
        legal_responder_refs: list[str],
        scope: str = "semantic_audit",
    ) -> str:
        cycle = self.repo.get_review_cycle(review_cycle_id)
        if cycle is None or cycle.stage_id != stage_id:
            raise StageError("review_cycle_not_found", review_cycle_id)
        req_id = _id("rreq")
        self.repo.add_review_request(
            ReviewRequestRow(
                id=req_id,
                review_cycle_id=review_cycle_id,
                stage_id=stage_id,
                candidate_set_id=cycle.candidate_set_id,
                review_role_id=review_role_id,
                legal_responder_refs=list(legal_responder_refs),
                scope=scope,
                status="pending",
                created_at=_now(),
            )
        )
        stage = self.repo.get_stage(stage_id)
        if stage is not None:
            stage.waiting_reason = "awaiting_review"
            stage.updated_at = _now()
        return req_id

    def submit_review(
        self,
        stage_id: str,
        body: ReviewReportRequest,
        *,
        reviewer_instance_id: str,
    ) -> str:
        req = self.repo.get_review_request(body.review_request_id)
        if req is None:
            raise StageError("review_request_not_found", body.review_request_id)
        if req.stage_id != stage_id or req.review_cycle_id != body.review_cycle_id:
            raise StageError("review_scope_mismatch", "request does not match stage/cycle")
        if reviewer_instance_id not in (req.legal_responder_refs or []):
            raise StageError("illegal_reviewer", "reviewer not in legalResponderRefs")
        if req.status != "pending":
            raise StageError("review_not_pending", req.status)

        cycle = self.repo.get_review_cycle(body.review_cycle_id)
        if cycle is None:
            raise StageError("review_cycle_not_found", body.review_cycle_id)
        if cycle.candidate_set_hash != body.manifest_hash:
            # Old review against new candidate → reject
            raise StageError("candidate_version_mismatch", "review targets stale candidate set")

        report_id = _id("rr")
        self.repo.add_review_report(
            ReviewReportRow(
                id=report_id,
                review_request_id=body.review_request_id,
                review_cycle_id=body.review_cycle_id,
                reviewer_instance_id=reviewer_instance_id,
                stage_id=stage_id,
                candidate_set_id=body.candidate_set_id or cycle.candidate_set_id,
                candidate_id=body.candidate_id,
                manifest_hash=body.manifest_hash,
                contract_hash=body.contract_hash,
                findings=[f.model_dump(mode="json", by_alias=True) for f in body.findings],
                verdict=body.verdict,
                created_at=_now(),
            )
        )
        req.status = "answered"
        return report_id

    def submit_user_decision(
        self,
        stage_id: str,
        body: StageUserDecisionRequest,
        *,
        decided_by: str,
    ) -> StageUserDecision:
        existing = self.repo.get_user_decision_by_request(body.request_id)
        if existing is not None:
            return self._to_user_decision(existing)
        cycle = self.repo.get_review_cycle(body.review_cycle_id)
        if cycle is None or cycle.stage_id != stage_id:
            raise StageError("review_cycle_not_found", body.review_cycle_id)
        if (
            cycle.candidate_set_hash != body.candidate_set_hash
            or cycle.contract_hash != body.contract_hash
            or cycle.gate_policy_version != body.gate_policy_version
        ):
            raise StageError(
                "decision_version_mismatch",
                "user decision versions do not match current review cycle",
            )
        row = StageUserDecisionRow(
            id=_id("sud"),
            request_id=body.request_id,
            stage_id=stage_id,
            review_cycle_id=body.review_cycle_id,
            candidate_set_hash=body.candidate_set_hash,
            contract_hash=body.contract_hash,
            gate_policy_version=body.gate_policy_version,
            decision=body.decision,
            note=body.note,
            decided_by=decided_by,
            decided_at=_now(),
        )
        self.repo.add_user_decision(row)
        return self._to_user_decision(row)

    # --- D10.5 evaluate + atomic release ---

    def evaluate(
        self,
        stage_id: str,
        *,
        validation_passed_required: bool,
        validation_has_required_failure: bool = False,
        validation_report_refs: list[str] | None = None,
        stop_verified: bool = True,
        budget_exhausted: bool = False,
        user_decision_required: bool | None = None,
    ) -> GateEvaluationResult:
        stage = self.repo.get_stage(stage_id)
        if stage is None:
            raise StageError("stage_not_found", stage_id)
        if not stage.current_review_cycle_id or not stage.current_candidate_set_id:
            raise StageError("no_active_cycle", "stage has no active review cycle")
        cycle = self.repo.get_review_cycle(stage.current_review_cycle_id)
        cs = self.repo.get_candidate_set(stage.current_candidate_set_id)
        if cycle is None or cs is None:
            raise StageError("cycle_or_set_missing", "review cycle or candidate set missing")

        policy = StageGatePolicy.model_validate(stage.gate_policy)
        reqs = self.repo.list_review_requests(cycle.id)
        reports = self.repo.list_review_reports(cycle.id)
        answered = [r.review_request_id for r in reports if r.review_request_id]
        verdicts: list[ReviewVerdict] = [r.verdict for r in reports]  # type: ignore[misc]

        user_row = self.repo.latest_user_decision_for_cycle(cycle.id)
        user_dec = None if user_row is None else self._to_user_decision(user_row)
        require_user = (
            policy.require_user_decision
            if user_decision_required is None
            else user_decision_required
        )

        inp = GateEvaluationInput(
            stage_id=stage_id,
            plan_version=stage.plan_version,
            assignment_version=stage.assignment_version or 1,
            gate_policy=policy,
            candidate_set=self._to_candidate_set(cs),
            contract_hash=cs.contract_hash,
            validation_report_refs=list(validation_report_refs or []),
            validation_passed_required=validation_passed_required,
            validation_has_required_failure=validation_has_required_failure,
            review_request_ids=[r.id for r in reqs],
            answered_review_request_ids=[a for a in answered if a],
            review_verdicts=verdicts,
            user_decision=user_dec,
            user_decision_required=require_user,
            repair_round=int(stage.repair_round or 0),
            budget_exhausted=budget_exhausted,
            stop_verified=stop_verified,
        )
        return evaluate_gate(inp)

    def commit_release(
        self,
        stage_id: str,
        evaluation: GateEvaluationResult,
        *,
        actor: str = "stage_controller",
    ) -> GateDecision:
        """Short atomic release transaction (single session commit by caller)."""
        if evaluation.outcome != "release":
            raise StageError("not_releasable", f"outcome={evaluation.outcome}")
        stage = self.repo.get_stage(stage_id)
        if stage is None:
            raise StageError("stage_not_found", stage_id)
        cycle = self.repo.get_review_cycle(stage.current_review_cycle_id or "")
        cs = self.repo.get_candidate_set(stage.current_candidate_set_id or "")
        if cycle is None or cs is None:
            raise StageError("cycle_or_set_missing", "missing cycle/set")

        # Re-read: no conflicting released decision for cycle
        for gd in self.repo.list_gate_decisions(stage_id):
            if gd.review_cycle_id == cycle.id and gd.result == "released":
                raise StageError("conflicting_release", "cycle already has release decision")

        policy = StageGatePolicy.model_validate(stage.gate_policy)
        scope_kind = policy.policy
        decision_id = _id("gd")
        allowed = list(evaluation.allowed_release_work_package_ids)
        gd = GateDecisionRow(
            id=decision_id,
            stage_id=stage_id,
            review_cycle_id=cycle.id,
            candidate_set_id=cs.id,
            candidate_id=None,
            manifest_hash=cs.manifest_hash,
            contract_hash=cs.contract_hash,
            gate_policy_version=policy.version,
            validation_refs=[],
            review_refs=[r.id for r in self.repo.list_review_reports(cycle.id)],
            allowed_consume_work_package_ids=allowed,
            result="released",
            reason=evaluation.reason,
            decided_at=_now(),
        )
        self.repo.add_gate_decision(gd)

        release_id = _id("rel")
        members = [
            m
            for m in cs.members
            if isinstance(m, dict) and m.get("workPackageId") in allowed
        ] or list(cs.members)
        self.repo.add_release(
            ReleaseRecordRow(
                id=release_id,
                stage_id=stage_id,
                review_cycle_id=cycle.id,
                gate_decision_id=decision_id,
                scope_kind=scope_kind,
                work_package_ids=allowed,
                candidate_versions=members,
                shared_interface_still_blocked=scope_kind == "dependency_branch"
                and not cs.required_work_packages_complete,
                created_at=_now(),
            )
        )

        if scope_kind == "stage_atomic" or cs.required_work_packages_complete:
            assert_transition("reviewing", "released")
            stage.status = "released"
            stage.waiting_reason = "none"
        # Branch release keeps stage reviewing/rework when shared interfaces blocked.
        cycle.status = "decided"
        cycle.closed_at = _now()
        stage.updated_at = _now()
        stage.write_access_closed = True

        self.session.add(
            Outbox(
                id=_id("obx"),
                run_id=stage.run_id,
                event_id=None,
                intent_type="stage.released",
                target="execution_eligibility",
                payload={
                    "releaseId": release_id,
                    "gateDecisionId": decision_id,
                    "workPackageIds": allowed,
                    "actor": actor,
                },
                idempotency_key=f"release:{release_id}",
                delivery_status="pending",
                attempt_count=0,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        return self._to_gate(gd)

    # --- D10.6 rework ---

    def apply_evaluation(
        self,
        stage_id: str,
        evaluation: GateEvaluationResult,
        *,
        target_work_package_id: str | None = None,
        target_role_id: str = "role_dev",
        problems: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if evaluation.outcome == "release":
            return {"gateDecision": self.commit_release(stage_id, evaluation)}
        if evaluation.outcome == "waiting":
            stage = self.repo.get_stage(stage_id)
            if stage is not None:
                stage.waiting_reason = {
                    "required_reviewer_pending": "awaiting_review",
                    "validation_incomplete": "awaiting_validation",
                    "user_decision_required": "awaiting_user_decision",
                    "review_conflict_escalate_user": "awaiting_user_decision",
                    "review_needs_input": "awaiting_review",
                }.get(evaluation.reason, stage.waiting_reason)
                stage.updated_at = _now()
            return {"status": "waiting", "evaluation": evaluation}
        if evaluation.outcome == "blocked":
            stage = self.repo.get_stage(stage_id)
            if stage is not None:
                stage.status = "blocked"
                stage.waiting_reason = (
                    "budget_exhausted"
                    if evaluation.reason == "budget_exhausted"
                    else "awaiting_permission"
                    if "auth" in evaluation.reason or "permission" in evaluation.reason
                    else stage.waiting_reason
                )
                stage.updated_at = _now()
            return {"status": "blocked", "evaluation": evaluation}

        # rework
        stage = self.repo.get_stage(stage_id)
        if stage is None:
            raise StageError("stage_not_found", stage_id)
        cycle = self.repo.get_review_cycle(stage.current_review_cycle_id or "")
        if cycle is None:
            raise StageError("review_cycle_not_found", "no cycle")
        digest = evaluation.rework_problem_digest or _hash({"reason": evaluation.reason})
        existing = self.repo.find_rework(cycle_id=cycle.id, problem_digest=digest)
        if existing is not None:
            return {"reworkOrder": self._to_rework(existing), "duplicate": True}

        policy = StageGatePolicy.model_validate(stage.gate_policy)
        remaining = max(0, policy.max_repair_rounds - int(stage.repair_round or 0))
        if remaining <= 0:
            stage.status = "blocked"
            stage.waiting_reason = "budget_exhausted"
            raise StageError("repair_rounds_exhausted", "escalate user")

        wp = target_work_package_id or (stage.required_work_packages or ["wp_1"])[0]
        order = ReworkOrderRow(
            id=_id("rw"),
            stage_id=stage_id,
            source_review_cycle_id=cycle.id,
            source_candidate_set_id=cycle.candidate_set_id,
            target_work_package_id=wp,
            target_role_id=target_role_id,
            problem_digest=digest,
            problems=problems or [{"reason": evaluation.reason}],
            remaining_rounds=remaining - 1,
            status="open",
            created_at=_now(),
        )
        self.repo.add_rework_order(order)
        # Round increments only when rework accepted and new attempt established.
        assert_transition("reviewing", "rework")
        stage.status = "rework"
        stage.waiting_reason = "none"
        stage.write_access_closed = False  # new writable workspace via D07 later
        stage.updated_at = _now()
        cycle.status = "superseded"
        cycle.closed_at = _now()
        return {"reworkOrder": self._to_rework(order), "duplicate": False}

    def accept_rework(self, rework_order_id: str) -> None:
        """Formally accept rework and establish new attempt — increments repair_round."""
        order = self.repo.get_rework(rework_order_id)
        if order is None:
            raise StageError("rework_not_found", rework_order_id)
        stage = self.repo.get_stage(order.stage_id)
        if stage is None:
            raise StageError("stage_not_found", order.stage_id)
        order.status = "accepted"
        stage.repair_round = int(stage.repair_round or 0) + 1
        assert_transition("rework", "executing")
        stage.status = "executing"
        stage.updated_at = _now()

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
            members=members,  # type: ignore[arg-type]
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
                "allowedConsumeWorkPackageIds": list(
                    row.allowed_consume_work_package_ids or []
                ),
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
                "involvesPlanOrInterfaceChange": bool(
                    row.involves_plan_or_interface_change
                ),
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
