"""D10.1: stage state machine, candidate set, review cycle, gate contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agentforge.contracts.stages import (
    CandidateSet,
    GateEvaluationInput,
    GateEvaluationResult,
    ReleaseRecord,
    ReviewCycle,
    ReviewReportRequest,
    ReworkOrder,
    Stage,
    StageGatePolicy,
    StageSubmissionRequest,
    StageUserDecision,
    WorkPackageCandidateRef,
)
from agentforge.stages.transitions import (
    MAIN_PATH,
    assert_transition,
    can_transition,
    ready_requires,
    review_cycle_fingerprint_parts,
)

NOW = datetime(2026, 11, 12, tzinfo=UTC)
H = "a" * 64


def test_main_path_transitions_are_legal() -> None:
    for i in range(len(MAIN_PATH) - 1):
        assert can_transition(MAIN_PATH[i], MAIN_PATH[i + 1])
    assert can_transition("reviewing", "rework")
    assert can_transition("rework", "executing")
    assert not can_transition("released", "executing")
    with pytest.raises(ValueError):
        assert_transition("frozen", "released")


def test_ready_not_inferred_from_completed_string() -> None:
    assert ready_requires(
        upstream_required_versions_released=True,
        plan_still_valid=True,
        stage_eligible_to_start=True,
    )
    assert not ready_requires(
        upstream_required_versions_released=False,
        plan_still_valid=True,
        stage_eligible_to_start=True,
    )


def test_stage_waiting_reason_separate_from_status() -> None:
    stage = Stage.model_validate(
        {
            "stageId": "stage_impl",
            "planVersion": 1,
            "gatePolicy": {"policy": "stage_atomic", "version": 1},
            "status": "blocked",
            "waitingReason": "awaiting_stop_verification",
        }
    )
    assert stage.status == "blocked"
    assert stage.waiting_reason == "awaiting_stop_verification"
    with pytest.raises(ValidationError):
        Stage.model_validate(
            {
                "stageId": "stage_impl",
                "planVersion": 1,
                "gatePolicy": {"policy": "stage_atomic", "version": 1},
                "status": "ready",
                "waitingReason": "awaiting_review",
            }
        )


def test_submission_request_rejects_trusted_flags() -> None:
    ok = StageSubmissionRequest.model_validate(
        {
            "workPackageIds": ["wp_1"],
            "attemptRefs": ["att_1"],
            "candidateRef": "cand_1",
            "note": "done",
        }
    )
    assert ok.work_package_ids == ["wp_1"]
    with pytest.raises(ValidationError):
        StageSubmissionRequest.model_validate(
            {
                "workPackageIds": ["wp_1"],
                "attemptRefs": ["att_1"],
                "stopped": True,
            }
        )
    with pytest.raises(ValidationError):
        StageSubmissionRequest.model_validate(
            {
                "workPackageIds": ["wp_1"],
                "attemptRefs": ["att_1"],
                "manifestHash": H,
            }
        )


def test_candidate_set_and_review_cycle_fingerprint() -> None:
    members = [
        WorkPackageCandidateRef.model_validate(
            {
                "workPackageId": "wp_1",
                "candidateId": "cand_1",
                "artifactVersion": 1,
                "required": True,
            }
        )
    ]
    cs = CandidateSet.model_validate(
        {
            "candidateSetId": "cs_1",
            "stageId": "stage_impl",
            "members": [m.model_dump(mode="json", by_alias=True) for m in members],
            "requiredWorkPackagesComplete": True,
            "contractHash": H,
            "environmentFingerprint": "b" * 64,
            "manifestHash": "c" * 64,
            "createdAt": NOW.isoformat(),
            "stopEvidenceRefs": ["stop_1"],
        }
    )
    parts = review_cycle_fingerprint_parts(
        candidate_set_hash=cs.manifest_hash,
        contract_hash=cs.contract_hash,
        gate_policy_version=1,
        environment_fingerprint=cs.environment_fingerprint,
    )
    assert parts[2] == 1
    cycle = ReviewCycle.model_validate(
        {
            "reviewCycleId": "rc_1",
            "stageId": "stage_impl",
            "submissionId": "sub_1",
            "candidateSetId": cs.candidate_set_id,
            "candidateSetHash": cs.manifest_hash,
            "contractHash": cs.contract_hash,
            "gatePolicyVersion": 1,
            "environmentFingerprint": cs.environment_fingerprint,
            "status": "open",
            "openedAt": NOW.isoformat(),
        }
    )
    assert cycle.status == "open"


def test_gate_evaluation_outcomes_are_distinct() -> None:
    waiting = GateEvaluationResult.model_validate(
        {"outcome": "waiting", "reason": "review pending", "missingRefs": ["req_1"]}
    )
    release = GateEvaluationResult.model_validate(
        {
            "outcome": "release",
            "reason": "all required satisfied",
            "allowedReleaseWorkPackageIds": ["wp_1"],
        }
    )
    assert waiting.outcome != release.outcome
    assert waiting.outcome != "release"


def test_review_report_requires_request_and_cycle() -> None:
    req = ReviewReportRequest.model_validate(
        {
            "reviewRequestId": "rr_1",
            "reviewCycleId": "rc_1",
            "candidateId": "cand_1",
            "manifestHash": H,
            "contractHash": "b" * 64,
            "verdict": "approve",
        }
    )
    assert req.review_request_id == "rr_1"
    with pytest.raises(ValidationError):
        ReviewReportRequest.model_validate(
            {
                "candidateId": "cand_1",
                "manifestHash": H,
                "contractHash": "b" * 64,
                "verdict": "approve",
            }
        )


def test_user_decision_binds_exact_versions() -> None:
    decision = StageUserDecision.model_validate(
        {
            "decisionId": "ud_1",
            "requestId": "ureq_1",
            "stageId": "stage_impl",
            "reviewCycleId": "rc_1",
            "candidateSetHash": H,
            "contractHash": "b" * 64,
            "gatePolicyVersion": 1,
            "decision": "approve",
            "decidedBy": "user:local",
            "decidedAt": NOW.isoformat(),
        }
    )
    assert decision.gate_policy_version == 1


def test_release_record_scopes_branch_without_whole_stage() -> None:
    record = ReleaseRecord.model_validate(
        {
            "releaseId": "rel_1",
            "stageId": "stage_impl",
            "reviewCycleId": "rc_1",
            "gateDecisionId": "gd_1",
            "scopeKind": "dependency_branch",
            "workPackageIds": ["wp_api"],
            "candidateVersions": [
                {
                    "workPackageId": "wp_api",
                    "candidateId": "cand_api",
                    "artifactVersion": 2,
                }
            ],
            "sharedInterfaceStillBlocked": True,
            "createdAt": NOW.isoformat(),
        }
    )
    assert record.scope_kind == "dependency_branch"
    assert record.shared_interface_still_blocked is True


def test_rework_order_and_gate_input_contracts() -> None:
    order = ReworkOrder.model_validate(
        {
            "reworkOrderId": "rw_1",
            "stageId": "stage_impl",
            "sourceReviewCycleId": "rc_1",
            "sourceCandidateSetId": "cs_1",
            "targetWorkPackageId": "wp_1",
            "targetRoleId": "role_dev",
            "problemDigest": H,
            "problems": [{"reason": "failing test"}],
            "remainingRounds": 2,
            "status": "open",
            "createdAt": NOW.isoformat(),
        }
    )
    assert order.remaining_rounds == 2
    policy = StageGatePolicy()
    cs = CandidateSet.model_validate(
        {
            "candidateSetId": "cs_1",
            "stageId": "stage_impl",
            "members": [
                {
                    "workPackageId": "wp_1",
                    "candidateId": "cand_1",
                    "artifactVersion": 1,
                }
            ],
            "requiredWorkPackagesComplete": True,
            "contractHash": H,
            "environmentFingerprint": "b" * 64,
            "manifestHash": "c" * 64,
            "createdAt": NOW.isoformat(),
        }
    )
    gin = GateEvaluationInput.model_validate(
        {
            "stageId": "stage_impl",
            "planVersion": 1,
            "assignmentVersion": 1,
            "gatePolicy": policy.model_dump(mode="json", by_alias=True),
            "candidateSet": cs.model_dump(mode="json", by_alias=True),
            "contractHash": H,
            "validationPassedRequired": False,
            "stopVerified": False,
            "userDecisionRequired": True,
        }
    )
    assert gin.user_decision_required is True
    assert gin.stop_verified is False
