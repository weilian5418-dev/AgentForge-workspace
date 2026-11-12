"""D11.9: recovery policy, normalize, handoff, apply fencing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentforge.contracts.recovery import (
    NativeObservation,
    OperationObservation,
    RecoveryDecisionRequest,
    WorkspaceObservation,
)
from agentforge.recovery.errors import RecoveryError
from agentforge.recovery.handoff import build_handoff_manifest
from agentforge.recovery.normalize import normalize_native_observation, normalize_resume_result
from agentforge.recovery.policy import RecoveryContext, decide_recovery
from agentforge.recovery.resume import classify_resume_result
from agentforge.recovery.service import RecoveryService
from agentforge.storage.models import (
    InstanceAttemptRow,
    Outbox,
    RecoveryCaseRow,
    Run,
)


def _ctx(**kw: Any) -> RecoveryContext:
    base = dict(
        run_id="run_1",
        case_id="rcv_1",
        context_hash="a" * 64,
        plan_version=1,
        assignment_version=1,
        generation=1,
        status_version=1,
    )
    base.update(kw)
    return RecoveryContext(**base)


def test_running_native_only_observes() -> None:
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
    rec = decide_recovery(_ctx(), (), native=native)
    assert rec.action == "observe_existing"
    assert rec.requires_user_decision is False


def test_missing_safety_facts_force_unknown_normalize() -> None:
    obs = normalize_native_observation(
        {"support": "supported", "running": False},  # missing children/writes
        instance_id="inst_1",
    )
    assert obs.support == "unknown"


def test_workspace_mismatch_blocks() -> None:
    ws = WorkspaceObservation.model_validate(
        {
            "workspaceId": "ws_1",
            "identityMatched": False,
            "support": "supported",
            "observedAt": datetime.now(UTC),
        }
    )
    rec = decide_recovery(_ctx(), (), workspace=ws)
    assert rec.action == "block_or_rework"
    assert "workspace_mismatch" in rec.reason_codes


def test_unknown_operation_awaits_reconciliation() -> None:
    op = OperationObservation.model_validate(
        {
            "operationId": "iop_1",
            "kind": "submit_task",
            "ledgerStatus": "unknown",
            "possiblyExecuted": True,
            "support": "unknown",
            "observedAt": datetime.now(UTC),
        }
    )
    rec = decide_recovery(_ctx(), (), operations=(op,))
    assert rec.action == "await_reconciliation"


def test_resume_adapter_cannot_silently_create_session() -> None:
    result = classify_resume_result(
        {"status": "succeeded", "createdSession": True, "sessionRef": "new"},
        expected_session_ref="old",
        expected_native_session_id="ns_old",
    )
    assert result.kind == "handoff_required"


def test_resume_different_session_ref_is_handoff() -> None:
    result = normalize_resume_result(
        {
            "kind": "resumed_existing",
            "sessionRef": "other",
            "nativeSessionId": "ns_old",
            "evidenceRef": "ev_1",
        },
        expected_session_ref="ns_session",
        expected_native_session_id="ns_old",
    )
    assert result.kind == "handoff_required"


def test_build_handoff_requires_stop() -> None:
    session = MagicMock()
    run = MagicMock(id="run_1", plan_version=1, assignment_version=1)
    attempt = MagicMock(
        id="ia_1",
        run_id="run_1",
        status="running",
        stop_verified_at=None,
        instance_id="inst_1",
        work_package_id="wp_1",
        stage_id="stage_1",
        native_session_id="ns_1",
        workspace_id="ws_1",
    )
    session.get.side_effect = lambda model, key, **_: {
        (Run, "run_1"): run,
        (InstanceAttemptRow, "ia_1"): attempt,
    }.get((model, key))
    with pytest.raises(RecoveryError) as exc:
        build_handoff_manifest(session, run_id="run_1", source_attempt_id="ia_1")
    assert exc.value.code == "pending_stop_verification"


def test_apply_decision_rejects_stale_context_hash() -> None:
    session = MagicMock()
    case = MagicMock(
        id="rcv_1",
        run_id="run_1",
        case_version=1,
        context_hash="a" * 64,
        status="observing",
        blocker_codes=[],
    )
    run = MagicMock(id="run_1", status_version=3)
    session.get.side_effect = lambda model, key, **_: {
        (RecoveryCaseRow, "rcv_1"): case,
        (Run, "run_1"): run,
    }.get((model, key))
    session.scalar.return_value = None
    svc = RecoveryService(session)
    body = RecoveryDecisionRequest.model_validate(
        {
            "decisionKey": "d1",
            "decision": "observe_existing",
            "caseVersion": 1,
            "contextHash": "b" * 64,
            "expectedStatusVersion": 3,
            "idempotencyKey": "idem",
        }
    )
    with pytest.raises(RecoveryError) as exc:
        svc.apply_decision("rcv_1", body, actor="platform:driver")
    assert exc.value.code == "context_changed"


def test_open_case_writes_outbox_without_adapter_io() -> None:
    session = MagicMock()
    added: list[Any] = []
    session.add.side_effect = added.append
    session.scalars.return_value.all.return_value = []
    session.scalar.return_value = None
    run = MagicMock(
        id="run_1",
        plan_version=1,
        assignment_version=1,
        gate_policy_version=1,
        active_generation=1,
        status_version=1,
        status="running",
        project_id="proj_1",
    )
    session.get.return_value = run

    with patch("agentforge.recovery.service.append_fact"), patch(
        "agentforge.recovery.service.db_now", side_effect=Exception("no sql")
    ):
        case = RecoveryService(session).open_case(
            "run_1", reason="unknown_attempt", request_operation_id="sop_1"
        )
    assert case.case_id
    assert len(case.context_hash) == 64
    assert any(isinstance(x, Outbox) for x in added)
    assert any(isinstance(x, RecoveryCaseRow) for x in added)
