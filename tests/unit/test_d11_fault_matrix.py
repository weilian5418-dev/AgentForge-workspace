"""D11.12 unit evidence for §16 fault matrix (mock tier — not product proof)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agentforge.contracts.recovery import NativeObservation
from agentforge.contracts.runs import RunActionRequest
from agentforge.driver.control import RunControlService, run_admits_new_development
from agentforge.driver.dispatch import DispatchService
from agentforge.driver.planner import choose_next_action
from agentforge.driver.snapshots import (
    AttemptSnap,
    CollaborationSnapshot,
    InstanceSnap,
    ReleaseSnap,
    StageSnap,
    WorkPackageSnap,
)
from agentforge.instances.errors import InstanceError
from agentforge.recovery.policy import RecoveryContext, decide_recovery
from agentforge.recovery.resume import classify_resume_result
from agentforge.runtime.drain import collect_drain_report
from agentforge.storage.models import Run


def _snap(**overrides) -> CollaborationSnapshot:
    base = dict(
        run_id="run_1",
        run_status="running",
        collaboration_state="running",
        status_version=1,
        plan_version=1,
        assignment_version=1,
        gate_policy_version=1,
        active_generation=1,
        last_sequence=1,
        plan_confirmed=True,
        stages=(),
        work_packages=(),
        attempts=(),
        instances=(),
        releases=(),
        open_waits=(),
        snapshot_hash="a" * 64,
    )
    base.update(overrides)
    return CollaborationSnapshot(**base)


def test_matrix_pause_blocks_new_dispatch_without_claiming_paused() -> None:
    """暂停与发送竞争：pause 后拒绝新派发，但 coordinate 未停稳前不报 paused。"""
    run = MagicMock(spec=Run, status="pause_requested", cancel_requested_at=None)
    assert run_admits_new_development(run) is False

    action = choose_next_action(_snap(run_status="pause_requested"))
    assert action.kind == "coordinate_stop"

    session = MagicMock()
    session.get.return_value = MagicMock(
        id="run_1", status="pause_requested", status_version=2
    )
    ctrl = RunControlService(session)
    with (
        patch.object(ctrl, "_ensure_stop_requests", return_value=["iop_1"]),
        patch.object(
            ctrl,
            "_list_unstopped",
            return_value=[{"instanceId": "inst_1", "lifecycle": "active"}],
        ),
        patch.object(ctrl, "_list_unknown_side_effects", return_value=[]),
        patch.object(ctrl, "_update_control_op"),
    ):
        result = ctrl.coordinate_pause("run_1")
    assert result["paused"] is False
    assert result["status"] == "pause_requested"


def test_matrix_cancel_stays_requested_when_children_running() -> None:
    session = MagicMock()
    session.get.return_value = MagicMock(
        id="run_1", status="cancel_requested", status_version=3
    )
    ctrl = RunControlService(session)
    with (
        patch.object(ctrl, "_revoke_unstarted_intents", return_value=[]),
        patch.object(ctrl, "_ensure_stop_requests", return_value=["iop_stop"]),
        patch.object(ctrl, "_list_unstopped", return_value=[]),
        patch.object(
            ctrl,
            "_list_unknown_side_effects",
            return_value=[{"kind": "instance_operation", "operationId": "iop_u"}],
        ),
        patch.object(ctrl, "_update_control_op"),
        patch("agentforge.driver.control.append_fact") as append,
    ):
        result = ctrl.coordinate_cancel("run_1")
    assert result["canceled"] is False
    assert result["status"] == "cancel_requested"
    append.assert_not_called()


def test_matrix_drain_lists_unknown_behind_failed_created() -> None:
    session = MagicMock()
    failed = MagicMock(
        id="inst_f", status="failed", lifecycle="failed", role_id="dev", session_ref=""
    )
    unknown_op = MagicMock(
        id="iop_u", instance_id="inst_f", kind="create_session", status="unknown"
    )
    session.scalars.return_value.all.side_effect = [
        [failed],
        [unknown_op],
        [],
        [],
        [],
        [],
    ]
    report = collect_drain_report(session)
    assert report["drainComplete"] is False
    assert report["unknownOperations"][0]["operationId"] == "iop_u"


def test_matrix_running_native_only_observes() -> None:
    native = NativeObservation.model_validate(
        {
            "instanceId": "inst_1",
            "running": True,
            "childrenRunning": False,
            "writesQuiesced": True,
            "observedAt": datetime.now(UTC),
            "support": "supported",
            "sessionRef": "s1",
            "nativeSessionId": "ns_1",
        }
    )
    ctx = RecoveryContext(
        run_id="run_1",
        case_id="rcv_1",
        context_hash="a" * 64,
        plan_version=1,
        assignment_version=1,
        generation=1,
        status_version=1,
    )
    rec = decide_recovery(ctx, (), native=native)
    assert rec.action == "observe_existing"


def test_matrix_children_running_awaits_stop() -> None:
    native = NativeObservation.model_validate(
        {
            "instanceId": "inst_1",
            "running": True,
            "childrenRunning": True,
            "writesQuiesced": False,
            "observedAt": datetime.now(UTC),
            "support": "supported",
            "sessionRef": "s1",
            "nativeSessionId": "ns_1",
        }
    )
    ctx = RecoveryContext(
        run_id="run_1",
        case_id="rcv_1",
        context_hash="a" * 64,
        plan_version=1,
        assignment_version=1,
        generation=1,
        status_version=1,
    )
    rec = decide_recovery(ctx, (), native=native)
    assert rec.action == "await_stop_verification"


def test_matrix_unsupported_resume_requires_handoff() -> None:
    result = classify_resume_result(
        {"status": "succeeded", "createdSession": True, "sessionRef": "new"},
        expected_session_ref="old",
        expected_native_session_id="ns_old",
    )
    assert result.kind == "handoff_required"


def test_matrix_unknown_attempt_blocks_redispatch() -> None:
    action = choose_next_action(
        _snap(
            attempts=(AttemptSnap("ia_1", "wp_a", "inst_1", "unknown", "stage_a"),),
            work_packages=(WorkPackageSnap("wp_a", "stage_a", "role_dev"),),
            stages=(StageSnap("stage_a", "executing", "none", False),),
            instances=(
                InstanceSnap("inst_1", "role_dev", "active", "idle", "ws_1", "ns_1"),
            ),
        )
    )
    assert action.kind == "open_recovery"


def test_matrix_busy_capacity_blocks_second_package() -> None:
    action = choose_next_action(
        _snap(
            stages=(StageSnap("stage_a", "executing", "none", False),),
            work_packages=(
                WorkPackageSnap("wp_a", "stage_a", "role_dev"),
                WorkPackageSnap("wp_b", "stage_a", "role_dev"),
            ),
            instances=(
                InstanceSnap("inst_1", "role_dev", "active", "busy", "ws_1", "ns_1"),
            ),
            attempts=(AttemptSnap("ia_1", "wp_a", "inst_1", "running", "stage_a"),),
        )
    )
    assert action.kind == "register_wait"
    assert action.wait_reason == "capacity"


def test_matrix_dispatch_rejects_pause_status() -> None:
    session = MagicMock()
    svc = DispatchService(session)
    run = MagicMock(
        id="run_1",
        status="pause_requested",
        cancel_requested_at=None,
        plan_version=1,
        assignment_version=1,
    )
    with pytest.raises(InstanceError) as exc:
        svc._assert_run_admits_dispatch(run)
    assert exc.value.code == "execution_blocked"


def test_matrix_handoff_not_completed() -> None:
    action = choose_next_action(
        _snap(
            work_packages=(WorkPackageSnap("wp_a", "stage_a", "role_dev", True),),
            releases=(ReleaseSnap("rel_1", "stage_a", ("wp_a",)),),
        )
    )
    assert action.kind == "prepare_handoff"


def test_matrix_resume_request_keeps_paused_fact() -> None:
    session = MagicMock()
    run = MagicMock(id="run_1", status="paused", status_version=4)
    session.get.return_value = run
    session.scalars.return_value.all.return_value = []
    body = RunActionRequest.model_validate(
        {
            "action": "resume",
            "expectedStatusVersion": 4,
            "idempotencyKey": "idem-r",
        }
    )
    with (
        patch("agentforge.driver.control.append_fact"),
        patch("agentforge.driver.control.record_wake_signal"),
        patch("agentforge.driver.control.RecoveryService") as Rcv,
    ):
        Rcv.return_value.open_case.return_value = MagicMock(case_id="rcv_1")
        RunControlService(session).request_action("run_1", body)
    assert run.status == "paused"
