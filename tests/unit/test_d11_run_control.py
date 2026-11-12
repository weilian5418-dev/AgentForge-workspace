"""D11.10: RunControl pause/cancel semantics and drain unknown ops."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agentforge.contracts.runs import RunActionRequest
from agentforge.driver.control import RunControlError, RunControlService, run_admits_new_development
from agentforge.runtime.control import admits_new_work, read_admission_db, set_admission
from agentforge.runtime.drain import collect_drain_report


def test_run_admits_new_development_blocks_pause_and_cancel() -> None:
    running = MagicMock(status="running", cancel_requested_at=None)
    assert run_admits_new_development(running) is True

    pause_req = MagicMock(status="pause_requested", cancel_requested_at=None)
    assert run_admits_new_development(pause_req) is False

    paused = MagicMock(status="paused", cancel_requested_at=None)
    assert run_admits_new_development(paused) is False

    cancel_flag = MagicMock(status="running", cancel_requested_at=datetime.now(UTC))
    assert run_admits_new_development(cancel_flag) is False


def test_pause_keeps_pause_requested_while_instance_active() -> None:
    session = MagicMock()
    run = MagicMock(
        id="run_1",
        status="pause_requested",
        status_version=2,
        cancel_requested_at=None,
    )
    session.get.return_value = run

    active = MagicMock(
        id="inst_1",
        lifecycle="active",
        status="running",
        session_ref="sess_1",
        native_session_id="ns_1",
        generation=1,
        current_attempt_id=None,
    )
    session.scalars.return_value.all.side_effect = [
        [active],  # _ensure_stop_requests
        None,  # existing stop op scalar path uses scalar; list uses scalars
    ]
    # _ensure_stop_requests uses scalars().all() then scalar for existing
    # _list_unstopped and _list_unknown use scalars().all()
    session.scalars.return_value.all.side_effect = [
        [active],  # ensure_stop instances
        [active],  # list_unstopped
        [],  # list_unknown instance_ids query uses scalars for instances then ops
    ]
    session.scalar.return_value = None

    with (
        patch("agentforge.driver.control.append_fact"),
        patch("agentforge.driver.control.record_wake_signal"),
    ):
        ctrl = RunControlService(session)
        # Simplify: stub helpers
        with (
            patch.object(ctrl, "_ensure_stop_requests", return_value=["iop_stop"]),
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
    assert run.status == "pause_requested"


def test_pause_flips_to_paused_when_quiescent() -> None:
    session = MagicMock()
    run = MagicMock(id="run_1", status="pause_requested", status_version=3)
    session.get.return_value = run

    with patch("agentforge.driver.control.append_fact") as append:
        ctrl = RunControlService(session)
        with (
            patch.object(ctrl, "_ensure_stop_requests", return_value=[]),
            patch.object(ctrl, "_list_unstopped", return_value=[]),
            patch.object(ctrl, "_list_unknown_side_effects", return_value=[]),
            patch.object(ctrl, "_update_control_op"),
        ):
            result = ctrl.coordinate_pause("run_1")

    assert result["paused"] is True
    assert result["status"] == "paused"
    assert run.status == "paused"
    assert run.status_version == 4
    append.assert_called_once()
    assert append.call_args.args[2] == "run.paused"


def test_cancel_stays_cancel_requested_with_unknown_ops() -> None:
    session = MagicMock()
    run = MagicMock(id="run_1", status="cancel_requested", status_version=5)
    session.get.return_value = run

    ctrl = RunControlService(session)
    with (
        patch.object(ctrl, "_revoke_unstarted_intents", return_value=[]),
        patch.object(ctrl, "_ensure_stop_requests", return_value=["iop_1"]),
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
    assert run.status == "cancel_requested"
    append.assert_not_called()


def test_request_pause_sets_pause_requested_not_paused() -> None:
    session = MagicMock()
    run = MagicMock(
        id="run_1",
        status="running",
        status_version=1,
        cancel_requested_at=None,
        updated_at=None,
    )
    session.get.return_value = run
    session.scalars.return_value.all.return_value = []

    body = RunActionRequest.model_validate(
        {
            "action": "pause",
            "expectedStatusVersion": 1,
            "idempotencyKey": "idem-pause-1",
            "reason": "operator",
        }
    )
    with (
        patch("agentforge.driver.control.append_fact") as append,
        patch("agentforge.driver.control.record_wake_signal") as wake,
    ):
        accepted = RunControlService(session).request_action("run_1", body)

    assert accepted.status == "accepted"
    assert run.status == "pause_requested"
    assert run.status_version == 2
    assert append.call_args.args[2] == "run.pause_requested"
    wake.assert_called_once()


def test_resume_opens_recovery_without_flipping_running() -> None:
    session = MagicMock()
    run = MagicMock(id="run_1", status="paused", status_version=4)
    session.get.return_value = run
    session.scalars.return_value.all.return_value = []
    case = MagicMock(case_id="rcv_1")

    body = RunActionRequest.model_validate(
        {
            "action": "resume",
            "expectedStatusVersion": 4,
            "idempotencyKey": "idem-resume-1",
        }
    )
    with (
        patch("agentforge.driver.control.append_fact"),
        patch("agentforge.driver.control.record_wake_signal"),
        patch("agentforge.driver.control.RecoveryService") as Rcv,
    ):
        Rcv.return_value.open_case.return_value = case
        accepted = RunControlService(session).request_action("run_1", body)

    assert accepted.status == "accepted"
    assert run.status == "paused"
    Rcv.return_value.open_case.assert_called_once()


def test_drain_lists_unknown_behind_failed_created() -> None:
    session = MagicMock()

    failed = MagicMock(
        id="inst_f",
        status="failed",
        lifecycle="failed",
        role_id="dev",
        session_ref="",
    )
    created = MagicMock(
        id="inst_c",
        status="created",
        lifecycle="created",
        role_id="dev",
        session_ref="",
    )
    unknown_op = MagicMock(
        id="iop_u",
        instance_id="inst_f",
        kind="create_session",
        status="unknown",
    )

    # collect_drain_report issues multiple scalars().all() calls in order
    session.scalars.return_value.all.side_effect = [
        [failed, created],  # instances — both skipped as pending by lifecycle
        [unknown_op],  # unknown instance ops
        [],  # deliveries
        [],  # attempts
        [],  # runners
        [],  # approvals
    ]

    report = collect_drain_report(session)
    assert report["drainComplete"] is False
    assert report["verification"] == "pending"
    assert report["pendingAgentInstances"] == []
    assert len(report["unknownOperations"]) == 1
    assert report["unknownOperations"][0]["operationId"] == "iop_u"


def test_set_admission_persists_db_and_file(tmp_path) -> None:
    from agentforge.core.config import Settings

    settings = Settings(
        _env_file=None,
        api_token="test-token-" + "x" * 48,
        state_dir=tmp_path / "state",
    )
    session = MagicMock()
    session.get.return_value = None

    result = set_admission(session, settings, "drain", reason="test")
    assert result["admission"] == "drain"
    assert result["mode"] == "draining"
    session.add.assert_called_once()
    assert admits_new_work("drain") is False
    assert settings.control_path.exists()

    row = MagicMock(mode="draining")
    session.get.return_value = row
    assert read_admission_db(session) == "drain"


def test_request_action_status_version_conflict() -> None:
    session = MagicMock()
    run = MagicMock(id="run_1", status="running", status_version=9)
    session.get.return_value = run
    body = RunActionRequest.model_validate(
        {"action": "pause", "expectedStatusVersion": 1, "idempotencyKey": "k"}
    )
    with pytest.raises(RunControlError) as exc:
        RunControlService(session).request_action("run_1", body)
    assert exc.value.code == "status_version_conflict"
