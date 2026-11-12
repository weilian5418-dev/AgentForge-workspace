"""Pure GateEvaluator — no I/O, models, runners, or adapters."""

from __future__ import annotations

import hashlib
import json

from agentforge.contracts.stages import (
    GateEvaluationInput,
    GateEvaluationResult,
    ReviewVerdict,
)


def _digest_problems(verdicts: list[ReviewVerdict], findings_digest: str = "") -> str:
    payload = json.dumps({"verdicts": verdicts, "findings": findings_digest}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate_gate(inp: GateEvaluationInput) -> GateEvaluationResult:
    """Decide waiting / rework / blocked / release from fixed facts only."""
    policy = inp.gate_policy

    if not inp.stop_verified:
        return GateEvaluationResult(
            outcome="blocked",
            reason="stop_not_verified",
            missing_refs=[],
        )
    if inp.budget_exhausted:
        return GateEvaluationResult(outcome="blocked", reason="budget_exhausted")
    if inp.blocking_facts:
        return GateEvaluationResult(
            outcome="blocked",
            reason=inp.blocking_facts[0],
        )
    if not inp.candidate_set.required_work_packages_complete and policy.policy == "stage_atomic":
        return GateEvaluationResult(
            outcome="waiting",
            reason="required_work_packages_incomplete",
        )

    if inp.validation_has_required_failure:
        if inp.repair_round >= policy.max_repair_rounds:
            return GateEvaluationResult(outcome="blocked", reason="repair_rounds_exhausted")
        # Required failure cannot be overridden by a later optional pass.
        return GateEvaluationResult(
            outcome="rework",
            reason="required_validation_failed",
            rework_problem_digest=_digest_problems(inp.review_verdicts, "validation_failed"),
        )
    if not inp.validation_passed_required:
        return GateEvaluationResult(
            outcome="waiting",
            reason="validation_incomplete",
            missing_refs=list(inp.validation_report_refs),
        )

    required_reviews = set(inp.review_request_ids)
    answered = set(inp.answered_review_request_ids)
    if required_reviews - answered:
        return GateEvaluationResult(
            outcome="waiting",
            reason="required_reviewer_pending",
            missing_refs=sorted(required_reviews - answered),
        )

    if any(v == "needs_input" for v in inp.review_verdicts):
        return GateEvaluationResult(outcome="waiting", reason="review_needs_input")
    if len(inp.review_verdicts) >= 2:
        unique = set(inp.review_verdicts)
        if len(unique) > 1 and "approve" in unique and "request_changes" in unique:
            if policy.conflict_resolution == "escalate_user" and inp.user_decision is None:
                return GateEvaluationResult(
                    outcome="waiting",
                    reason="review_conflict_escalate_user",
                )
            if policy.conflict_resolution == "unanimous":
                return GateEvaluationResult(
                    outcome="rework",
                    reason="review_not_unanimous",
                    rework_problem_digest=_digest_problems(inp.review_verdicts),
                )

    conflict_approved = (
        policy.conflict_resolution == "escalate_user"
        and inp.user_decision is not None
        and inp.user_decision.decision == "approve"
        and inp.user_decision.candidate_set_hash == inp.candidate_set.manifest_hash
        and inp.user_decision.contract_hash == inp.contract_hash
        and inp.user_decision.gate_policy_version == policy.version
    )
    majority_approves = (
        policy.conflict_resolution == "majority"
        and inp.review_verdicts.count("approve") > len(inp.review_verdicts) / 2
    )
    if (
        any(v == "request_changes" for v in inp.review_verdicts)
        and not majority_approves
        and not conflict_approved
    ):
        if inp.repair_round >= policy.max_repair_rounds:
            return GateEvaluationResult(
                outcome="blocked",
                reason="repair_rounds_exhausted",
            )
        return GateEvaluationResult(
            outcome="rework",
            reason="review_request_changes",
            rework_problem_digest=_digest_problems(inp.review_verdicts),
        )

    if inp.user_decision_required:
        if inp.user_decision is None:
            return GateEvaluationResult(
                outcome="waiting",
                reason="user_decision_required",
            )
        # Bound versions must match candidate set under review.
        if (
            inp.user_decision.candidate_set_hash != inp.candidate_set.manifest_hash
            or inp.user_decision.contract_hash != inp.contract_hash
            or inp.user_decision.gate_policy_version != policy.version
        ):
            return GateEvaluationResult(
                outcome="blocked",
                reason="user_decision_version_mismatch",
            )
        if inp.user_decision.decision == "reject":
            return GateEvaluationResult(outcome="blocked", reason="user_rejected")
        if inp.user_decision.decision == "needs_revision":
            return GateEvaluationResult(
                outcome="rework",
                reason="user_needs_revision",
                rework_problem_digest=_digest_problems(["request_changes"]),
            )

    allowed = [
        m.work_package_id
        for m in inp.candidate_set.members
        if m.required or policy.policy == "dependency_branch"
    ]
    if policy.policy == "stage_atomic":
        allowed = [m.work_package_id for m in inp.candidate_set.members if m.required]
        if not inp.candidate_set.required_work_packages_complete:
            return GateEvaluationResult(
                outcome="waiting",
                reason="stage_atomic_incomplete",
            )

    return GateEvaluationResult(
        outcome="release",
        reason="all_required_satisfied",
        allowed_release_work_package_ids=sorted(set(allowed)),
    )
