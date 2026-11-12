"""D10.5: pure GateEvaluator — no I/O, force-pass, or side effects."""

from __future__ import annotations

from datetime import UTC, datetime

from agentforge.contracts.stages import (
    CandidateSet,
    GateEvaluationInput,
    StageGatePolicy,
    StageUserDecision,
)
from agentforge.stages.gate_evaluator import evaluate_gate

NOW = datetime(2026, 11, 12, tzinfo=UTC)
H = "a" * 64
E = "b" * 64
M = "c" * 64


def _cs(*, complete: bool = True) -> CandidateSet:
    return CandidateSet.model_validate(
        {
            "candidateSetId": "cs_1",
            "stageId": "stage_impl",
            "members": [
                {
                    "workPackageId": "wp_1",
                    "candidateId": "cand_1",
                    "artifactVersion": 1,
                    "required": True,
                }
            ],
            "requiredWorkPackagesComplete": complete,
            "contractHash": H,
            "environmentFingerprint": E,
            "manifestHash": M,
            "createdAt": NOW.isoformat(),
            "stopEvidenceRefs": ["stop_1"],
        }
    )


def _inp(**overrides):
    base = {
        "stageId": "stage_impl",
        "planVersion": 1,
        "assignmentVersion": 1,
        "gatePolicy": StageGatePolicy().model_dump(mode="json", by_alias=True),
        "candidateSet": _cs().model_dump(mode="json", by_alias=True),
        "contractHash": H,
        "validationPassedRequired": True,
        "stopVerified": True,
    }
    base.update(overrides)
    return GateEvaluationInput.model_validate(base)


def test_stop_not_verified_blocks() -> None:
    result = evaluate_gate(_inp(stopVerified=False))
    assert result.outcome == "blocked"
    assert result.reason == "stop_not_verified"


def test_required_validation_failure_forces_rework() -> None:
    result = evaluate_gate(_inp(validationHasRequiredFailure=True, validationPassedRequired=True))
    assert result.outcome == "rework"
    assert result.reason == "required_validation_failed"
    assert result.rework_problem_digest is not None


def test_pending_reviewer_waits() -> None:
    result = evaluate_gate(_inp(reviewRequestIds=["rreq_1"], answeredReviewRequestIds=[]))
    assert result.outcome == "waiting"
    assert result.reason == "required_reviewer_pending"
    assert result.missing_refs == ["rreq_1"]


def test_request_changes_rework_until_rounds_exhausted() -> None:
    result = evaluate_gate(
        _inp(
            reviewRequestIds=["rreq_1"],
            answeredReviewRequestIds=["rreq_1"],
            reviewVerdicts=["request_changes"],
            repairRound=0,
        )
    )
    assert result.outcome == "rework"
    exhausted = evaluate_gate(
        _inp(
            reviewRequestIds=["rreq_1"],
            answeredReviewRequestIds=["rreq_1"],
            reviewVerdicts=["request_changes"],
            repairRound=3,
            gatePolicy=StageGatePolicy(max_repair_rounds=3).model_dump(mode="json", by_alias=True),
        )
    )
    assert exhausted.outcome == "blocked"
    assert exhausted.reason == "repair_rounds_exhausted"


def test_user_decision_version_mismatch_blocks() -> None:
    decision = StageUserDecision.model_validate(
        {
            "decisionId": "ud_1",
            "requestId": "ureq_1",
            "stageId": "stage_impl",
            "reviewCycleId": "rc_1",
            "candidateSetHash": "d" * 64,
            "contractHash": H,
            "gatePolicyVersion": 1,
            "decision": "approve",
            "decidedBy": "user:local",
            "decidedAt": NOW.isoformat(),
        }
    )
    result = evaluate_gate(
        _inp(
            userDecisionRequired=True,
            userDecision=decision.model_dump(mode="json", by_alias=True),
        )
    )
    assert result.outcome == "blocked"
    assert result.reason == "user_decision_version_mismatch"


def test_release_when_all_required_satisfied() -> None:
    result = evaluate_gate(
        _inp(
            reviewRequestIds=["rreq_1"],
            answeredReviewRequestIds=["rreq_1"],
            reviewVerdicts=["approve"],
        )
    )
    assert result.outcome == "release"
    assert result.allowed_release_work_package_ids == ["wp_1"]


def test_stage_atomic_incomplete_waits() -> None:
    result = evaluate_gate(
        _inp(
            candidateSet=_cs(complete=False).model_dump(mode="json", by_alias=True),
            reviewRequestIds=[],
            answeredReviewRequestIds=[],
            reviewVerdicts=[],
        )
    )
    assert result.outcome == "waiting"
    assert result.reason == "required_work_packages_incomplete"
