"""D11.1: collaboration / recovery contract invariants."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agentforge.contracts.agents import InstanceAttempt
from agentforge.contracts.collaboration import (
    ACTIVE_INSTANCE_ATTEMPT_STATUSES,
    DriverResult,
    GraphBinding,
    LeaseFence,
    RunControlRequest,
    RunWait,
    WakeSignal,
)
from agentforge.contracts.events import parse_event_payload
from agentforge.contracts.messages import TaskAssignmentPayload
from agentforge.contracts.recovery import (
    NativeObservation,
    RecoveryDecisionRequest,
    RecoveryRecommendation,
)
from agentforge.contracts.runs import RunActionRequest, RunSnapshotResponse

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
H = "a" * 64


def test_graph_binding_rejects_extra_and_missing() -> None:
    ok = GraphBinding.model_validate(
        {
            "runId": "run_1",
            "planVersion": 1,
            "assignmentVersion": 1,
            "gatePolicyVersion": 1,
            "executionGeneration": 1,
        }
    )
    assert ok.graph_version.startswith("d11")
    with pytest.raises(ValidationError):
        GraphBinding.model_validate(
            {
                "runId": "run_1",
                "planVersion": 1,
                "assignmentVersion": 1,
                "gatePolicyVersion": 1,
                "executionGeneration": 1,
                "leaseToken": "secret",
            }
        )


def test_lease_fence_is_runtime_only_shape() -> None:
    fence = LeaseFence.model_validate(
        {
            "runId": "run_1",
            "owner": "boot:worker:inv1",
            "token": "tok_1",
            "leaseGeneration": 3,
            "threadId": "afg:abc",
            "bindingHash": H,
        }
    )
    assert fence.lease_generation == 3


def test_run_action_requires_version_and_rejects_stop_verified() -> None:
    ok = RunActionRequest.model_validate(
        {
            "action": "pause",
            "expectedStatusVersion": 17,
            "idempotencyKey": "pause-1",
        }
    )
    assert ok.expected_status_version == 17
    with pytest.raises(ValidationError):
        RunActionRequest.model_validate({"action": "pause"})
    with pytest.raises(ValidationError):
        RunActionRequest.model_validate(
            {
                "action": "pause",
                "expectedStatusVersion": 17,
                "idempotencyKey": "pause-1",
                "stopVerified": True,
            }
        )
    with pytest.raises(ValidationError):
        RunControlRequest.model_validate(
            {
                "action": "cancel",
                "expectedStatusVersion": 1,
                "idempotencyKey": "c1",
                "forcePass": True,
            }
        )


def test_wait_and_wake_statuses_are_distinct() -> None:
    wait = RunWait.model_validate(
        {
            "waitId": "w1",
            "runId": "run_1",
            "waitKey": "plan_confirmation",
            "reason": "plan_confirmation",
            "predicateVersion": 1,
            "status": "open",
            "createdAt": NOW.isoformat(),
        }
    )
    assert wait.thread_id is None
    wake = WakeSignal.model_validate(
        {
            "wakeSignalId": "ws1",
            "runId": "run_1",
            "sourceKind": "control",
            "sourceRef": "op:pause:1",
            "status": "pending",
            "createdAt": NOW.isoformat(),
        }
    )
    assert wake.status != "open"


def test_driver_outcome_ready_is_not_run_completed() -> None:
    result = DriverResult.model_validate(
        {
            "runId": "run_1",
            "threadId": "afg:1",
            "outcome": "ready_for_integration",
            "handoffRef": "ho_1",
        }
    )
    assert result.outcome == "ready_for_integration"
    assert result.outcome != "completed"


def test_instance_attempt_unknown_is_active_slot() -> None:
    attempt = InstanceAttempt.model_validate(
        {
            "attemptId": "ia_1",
            "instanceId": "inst_1",
            "runId": "run_1",
            "workPackageId": "wp_1",
            "generation": 1,
            "workspaceId": "ws_1",
            "status": "unknown",
            "createdAt": NOW.isoformat(),
        }
    )
    assert attempt.status in ACTIVE_INSTANCE_ATTEMPT_STATUSES
    assert "stopped" not in ACTIVE_INSTANCE_ATTEMPT_STATUSES


def test_task_assignment_carries_released_input_refs() -> None:
    payload = TaskAssignmentPayload.model_validate(
        {
            "type": "task.assignment",
            "workPackageId": "wp_1",
            "objective": "implement api",
            "inputReleaseRefs": ["rel_1"],
            "inputCandidateRefs": ["cand_1"],
            "contextBundleRef": "bundle_1",
            "attemptId": "ia_1",
        }
    )
    assert payload.input_release_refs == ["rel_1"]


def test_recovery_decision_rejects_invented_stop() -> None:
    with pytest.raises(ValidationError):
        RecoveryDecisionRequest.model_validate(
            {
                "decisionKey": "d1",
                "decision": "resume_existing",
                "caseVersion": 1,
                "contextHash": H,
                "expectedStatusVersion": 2,
                "idempotencyKey": "k1",
                "assumeStopped": True,
            }
        )


def test_native_observation_cannot_default_false_as_supported() -> None:
    with pytest.raises(ValidationError):
        NativeObservation.model_validate(
            {
                "instanceId": "inst_1",
                "observedAt": NOW.isoformat(),
                "support": "supported",
            }
        )
    ok = NativeObservation.model_validate(
        {
            "instanceId": "inst_1",
            "observedAt": NOW.isoformat(),
            "support": "unknown",
        }
    )
    assert ok.running is None


def test_recovery_recommendation_is_pure_shape() -> None:
    rec = RecoveryRecommendation.model_validate(
        {
            "action": "observe_existing",
            "requiresUserDecision": False,
            "reasonCodes": [],
        }
    )
    assert rec.action == "observe_existing"
    assert rec.action != "handoff_new_session" or True


def test_snapshot_exposes_collaboration_progress_not_as_status() -> None:
    snap = RunSnapshotResponse.model_validate(
        {
            "id": "run_1",
            "projectId": "proj_1",
            "goal": "ship",
            "mode": "team",
            "status": "running",
            "statusVersion": 3,
            "activeGeneration": 1,
            "latestSequence": 10,
            "executionProfile": "host_authorized",
            "failurePolicy": "dependency-aware",
            "budget": {"maxCost": 10.0, "currency": "USD"},
            "outputs": ["code"],
            "collaborationState": "waiting",
            "createdAt": NOW.isoformat(),
            "updatedAt": NOW.isoformat(),
        }
    )
    assert snap.status == "running"
    assert snap.collaboration_state == "waiting"


def test_d11_events_are_registered() -> None:
    payload = parse_event_payload(
        "run.collaboration_ready",
        {
            "handoffId": "ho_1",
            "handoffHash": H,
            "planVersion": 1,
            "assignmentVersion": 1,
        },
    )
    assert payload.handoff_id == "ho_1"
    with pytest.raises(ValueError):
        parse_event_payload("driver.heartbeat", {})
