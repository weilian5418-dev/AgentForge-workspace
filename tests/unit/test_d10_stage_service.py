"""D10.3–D10.7: submit/freeze/review/evaluate/release/rework acceptance (mem session)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from agentforge.contracts.stages import (
    ReviewReportRequest,
    StageSubmissionRequest,
    StageUserDecisionRequest,
)
from agentforge.stages.errors import StageError
from agentforge.stages.service import StageService
from agentforge.storage.models import (
    Outbox,
    StageRow,
)


NOW = datetime(2026, 11, 12, tzinfo=UTC)
H = "a" * 64
E = "e" * 64
C = "c" * 64


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _MemSession:
    def __init__(self) -> None:
        self.store: dict[tuple[type, Any], Any] = {}
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)
        key = getattr(row, "id", None)
        if key is not None:
            self.store[(type(row), key)] = row

    def flush(self) -> None:
        return None

    def get(self, model: type, key: Any) -> Any | None:
        return self.store.get((model, key))

    def scalar(self, stmt: Any) -> Any | None:
        text = str(stmt).lower()
        rows = list(self.added)
        if "stage_submissions" in text and "idempotency" in text:
            from agentforge.storage.models import StageSubmissionRow

            for r in rows:
                if isinstance(r, StageSubmissionRow) and r.idempotency_key:
                    return r
            return None
        if "review_cycles" in text and "submission" in text:
            from agentforge.storage.models import ReviewCycleRow

            for r in rows:
                if isinstance(r, ReviewCycleRow):
                    return r
            return None
        if "stage_user_decisions" in text and "request_id" in text:
            from agentforge.storage.models import StageUserDecisionRow

            for r in rows:
                if isinstance(r, StageUserDecisionRow):
                    return r
            return None
        if "rework_orders" in text:
            from agentforge.storage.models import ReworkOrderRow

            for r in rows:
                if isinstance(r, ReworkOrderRow):
                    return r
            return None
        return None

    def scalars(self, stmt: Any) -> _Result:
        text = str(stmt).lower()
        from agentforge.storage.models import (
            GateDecisionRow,
            ReleaseRecordRow,
            ReviewCycleRow,
            ReviewReportRow,
            ReviewRequestRow,
            ReworkOrderRow,
            StageSubmissionRow,
            StageUserDecisionRow,
        )

        mapping = {
            "stages": StageRow,
            "stage_submissions": StageSubmissionRow,
            "review_cycles": ReviewCycleRow,
            "review_requests": ReviewRequestRow,
            "review_reports": ReviewReportRow,
            "gate_decisions": GateDecisionRow,
            "rework_orders": ReworkOrderRow,
            "release_records": ReleaseRecordRow,
            "stage_user_decisions": StageUserDecisionRow,
        }
        for needle, cls in mapping.items():
            if needle in text:
                return _Result([r for r in self.added if isinstance(r, cls)])
        return _Result([])


def _seed_stage(
    session: _MemSession,
    *,
    status: str = "executing",
    require_user: bool = False,
    max_rounds: int = 3,
) -> StageRow:
    row = StageRow(
        id="stage_impl",
        run_id="run_1",
        plan_version=1,
        assignment_version=1,
        gate_policy_version=1,
        gate_policy={
            "policy": "stage_atomic",
            "version": 1,
            "requireUserDecision": require_user,
            "maxRepairRounds": max_rounds,
            "requiredReviewRoles": [],
            "conflictResolution": "escalate_user",
        },
        status=status,
        waiting_reason="none",
        concurrency_version=1,
        repair_round=0,
        required_work_packages=["wp_1"],
        dependencies=[],
        write_access_closed=False,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(row)
    return row


def test_submit_closes_write_and_emits_stop_verify() -> None:
    session = _MemSession()
    _seed_stage(session)
    svc = StageService(session)  # type: ignore[arg-type]
    accepted = svc.submit(
        "stage_impl",
        StageSubmissionRequest.model_validate(
            {
                "workPackageIds": ["wp_1"],
                "attemptRefs": ["att_1"],
                "idempotencyKey": "idem-1",
            }
        ),
        submitter_ref="cli",
    )
    stage = session.get(StageRow, "stage_impl")
    assert stage is not None
    assert stage.status == "submission_requested"
    assert stage.write_access_closed is True
    assert stage.waiting_reason == "awaiting_stop_verification"
    assert accepted.status == "accepted"
    assert any(
        isinstance(o, Outbox) and o.intent_type == "stage.stop_verify" for o in session.added
    )


def test_submit_idempotent_same_key() -> None:
    session = _MemSession()
    _seed_stage(session)
    svc = StageService(session)  # type: ignore[arg-type]
    body = StageSubmissionRequest.model_validate(
        {
            "workPackageIds": ["wp_1"],
            "attemptRefs": ["att_1"],
            "idempotencyKey": "idem-1",
        }
    )
    a1 = svc.submit("stage_impl", body, submitter_ref="cli")
    a2 = svc.submit("stage_impl", body, submitter_ref="cli")
    assert a1.operation_id == a2.operation_id


def test_freeze_requires_stop_evidence() -> None:
    session = _MemSession()
    _seed_stage(session)
    svc = StageService(session)  # type: ignore[arg-type]
    svc.submit(
        "stage_impl",
        StageSubmissionRequest.model_validate(
            {"workPackageIds": ["wp_1"], "attemptRefs": ["att_1"]}
        ),
        submitter_ref="cli",
    )
    from agentforge.storage.models import StageSubmissionRow

    sub = next(r for r in session.added if isinstance(r, StageSubmissionRow))
    with pytest.raises(StageError) as exc:
        svc.complete_stop_and_freeze(
            sub.id,
            stop_evidence_refs=[],
            contract_hash=C,
            environment_fingerprint=E,
        )
    assert exc.value.code == "stop_evidence_missing"


def test_happy_path_release() -> None:
    session = _MemSession()
    _seed_stage(session)
    svc = StageService(session)  # type: ignore[arg-type]
    svc.submit(
        "stage_impl",
        StageSubmissionRequest.model_validate(
            {
                "workPackageIds": ["wp_1"],
                "attemptRefs": ["att_1"],
                "candidateRef": "cand_1",
            }
        ),
        submitter_ref="cli",
        stop_verified=True,
        stop_evidence_refs=["stop_1"],
        contract_hash=C,
        environment_fingerprint=E,
    )
    stage = session.get(StageRow, "stage_impl")
    assert stage is not None
    assert stage.status == "reviewing"
    assert stage.current_review_cycle_id
    assert stage.current_candidate_set_id

    cycle_id = stage.current_review_cycle_id
    req_id = svc.open_review_request(
        stage_id="stage_impl",
        review_cycle_id=cycle_id,
        review_role_id="role_reviewer",
        legal_responder_refs=["inst_reviewer"],
    )
    from agentforge.storage.models import ReviewCycleRow

    cycle = session.get(ReviewCycleRow, cycle_id)
    assert cycle is not None
    svc.submit_review(
        "stage_impl",
        ReviewReportRequest.model_validate(
            {
                "reviewRequestId": req_id,
                "reviewCycleId": cycle_id,
                "candidateId": "cand_1",
                "manifestHash": cycle.candidate_set_hash,
                "contractHash": C,
                "verdict": "approve",
            }
        ),
        reviewer_instance_id="inst_reviewer",
    )
    evaluation = svc.evaluate(
        "stage_impl",
        validation_passed_required=True,
        stop_verified=True,
        user_decision_required=False,
    )
    assert evaluation.outcome == "release"
    applied = svc.apply_evaluation("stage_impl", evaluation)
    assert "gateDecision" in applied
    stage = session.get(StageRow, "stage_impl")
    assert stage is not None
    assert stage.status == "released"
    assert any(
        isinstance(o, Outbox) and o.intent_type == "stage.released" for o in session.added
    )


def test_illegal_reviewer_rejected() -> None:
    session = _MemSession()
    _seed_stage(session)
    svc = StageService(session)  # type: ignore[arg-type]
    svc.submit(
        "stage_impl",
        StageSubmissionRequest.model_validate(
            {"workPackageIds": ["wp_1"], "attemptRefs": ["att_1"]}
        ),
        submitter_ref="cli",
        stop_verified=True,
        stop_evidence_refs=["stop_1"],
        contract_hash=C,
        environment_fingerprint=E,
    )
    stage = session.get(StageRow, "stage_impl")
    assert stage is not None
    req_id = svc.open_review_request(
        stage_id="stage_impl",
        review_cycle_id=stage.current_review_cycle_id or "",
        review_role_id="role_reviewer",
        legal_responder_refs=["inst_ok"],
    )
    from agentforge.storage.models import ReviewCycleRow

    cycle = session.get(ReviewCycleRow, stage.current_review_cycle_id)
    assert cycle is not None
    with pytest.raises(StageError) as exc:
        svc.submit_review(
            "stage_impl",
            ReviewReportRequest.model_validate(
                {
                    "reviewRequestId": req_id,
                    "reviewCycleId": cycle.id,
                    "candidateId": "cand_1",
                    "manifestHash": cycle.candidate_set_hash,
                    "contractHash": C,
                    "verdict": "approve",
                }
            ),
            reviewer_instance_id="inst_intruder",
        )
    assert exc.value.code == "illegal_reviewer"


def test_stale_review_rejected_on_candidate_hash() -> None:
    session = _MemSession()
    _seed_stage(session)
    svc = StageService(session)  # type: ignore[arg-type]
    svc.submit(
        "stage_impl",
        StageSubmissionRequest.model_validate(
            {"workPackageIds": ["wp_1"], "attemptRefs": ["att_1"]}
        ),
        submitter_ref="cli",
        stop_verified=True,
        stop_evidence_refs=["stop_1"],
        contract_hash=C,
        environment_fingerprint=E,
    )
    stage = session.get(StageRow, "stage_impl")
    assert stage is not None
    req_id = svc.open_review_request(
        stage_id="stage_impl",
        review_cycle_id=stage.current_review_cycle_id or "",
        review_role_id="role_reviewer",
        legal_responder_refs=["inst_ok"],
    )
    with pytest.raises(StageError) as exc:
        svc.submit_review(
            "stage_impl",
            ReviewReportRequest.model_validate(
                {
                    "reviewRequestId": req_id,
                    "reviewCycleId": stage.current_review_cycle_id,
                    "candidateId": "cand_1",
                    "manifestHash": "f" * 64,
                    "contractHash": C,
                    "verdict": "approve",
                }
            ),
            reviewer_instance_id="inst_ok",
        )
    assert exc.value.code == "candidate_version_mismatch"


def test_rework_dedup_and_accept() -> None:
    session = _MemSession()
    _seed_stage(session)
    svc = StageService(session)  # type: ignore[arg-type]
    svc.submit(
        "stage_impl",
        StageSubmissionRequest.model_validate(
            {"workPackageIds": ["wp_1"], "attemptRefs": ["att_1"]}
        ),
        submitter_ref="cli",
        stop_verified=True,
        stop_evidence_refs=["stop_1"],
        contract_hash=C,
        environment_fingerprint=E,
    )
    stage = session.get(StageRow, "stage_impl")
    assert stage is not None
    req_id = svc.open_review_request(
        stage_id="stage_impl",
        review_cycle_id=stage.current_review_cycle_id or "",
        review_role_id="role_reviewer",
        legal_responder_refs=["inst_ok"],
    )
    from agentforge.storage.models import ReviewCycleRow

    cycle = session.get(ReviewCycleRow, stage.current_review_cycle_id)
    assert cycle is not None
    svc.submit_review(
        "stage_impl",
        ReviewReportRequest.model_validate(
            {
                "reviewRequestId": req_id,
                "reviewCycleId": cycle.id,
                "candidateId": "cand_1",
                "manifestHash": cycle.candidate_set_hash,
                "contractHash": C,
                "verdict": "request_changes",
            }
        ),
        reviewer_instance_id="inst_ok",
    )
    evaluation = svc.evaluate(
        "stage_impl",
        validation_passed_required=True,
        stop_verified=True,
        user_decision_required=False,
    )
    assert evaluation.outcome == "rework"
    first = svc.apply_evaluation("stage_impl", evaluation, target_work_package_id="wp_1")
    assert first["duplicate"] is False
    # Same digest must not create a second open order.
    second = svc.apply_evaluation("stage_impl", evaluation, target_work_package_id="wp_1")
    assert second["duplicate"] is True
    order = first["reworkOrder"]
    svc.accept_rework(order.rework_order_id)
    stage = session.get(StageRow, "stage_impl")
    assert stage is not None
    assert stage.status == "executing"
    assert stage.repair_round == 1
    assert stage.write_access_closed is False


def test_user_decision_version_bind() -> None:
    session = _MemSession()
    _seed_stage(session, require_user=True)
    svc = StageService(session)  # type: ignore[arg-type]
    svc.submit(
        "stage_impl",
        StageSubmissionRequest.model_validate(
            {"workPackageIds": ["wp_1"], "attemptRefs": ["att_1"]}
        ),
        submitter_ref="cli",
        stop_verified=True,
        stop_evidence_refs=["stop_1"],
        contract_hash=C,
        environment_fingerprint=E,
    )
    stage = session.get(StageRow, "stage_impl")
    assert stage is not None
    from agentforge.storage.models import ReviewCycleRow

    cycle = session.get(ReviewCycleRow, stage.current_review_cycle_id)
    assert cycle is not None
    with pytest.raises(StageError) as exc:
        svc.submit_user_decision(
            "stage_impl",
            StageUserDecisionRequest.model_validate(
                {
                    "requestId": "ureq_1",
                    "reviewCycleId": cycle.id,
                    "candidateSetHash": "f" * 64,
                    "contractHash": C,
                    "gatePolicyVersion": 1,
                    "decision": "approve",
                }
            ),
            decided_by="user:api",
        )
    assert exc.value.code == "decision_version_mismatch"


def test_cli_stage_submit_has_no_force_pass() -> None:
    import inspect

    from agentforge.cli import main as cli_main

    params = inspect.signature(cli_main.stage_submit).parameters
    assert "force_pass" not in params
    assert not any(name.replace("_", "-") == "force-pass" for name in params)
