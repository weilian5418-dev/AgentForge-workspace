"""D11.7: reserved dispatch, released inputs, submission bridge."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentforge.contracts.collaboration import LeaseFence
from agentforge.driver.dispatch import DispatchService
from agentforge.driver.inputs import ReleasedInputResolver
from agentforge.driver.submissions import SubmissionBridge
from agentforge.instances.errors import InstanceError
from agentforge.storage.models import (
    AgentInstanceRow,
    ArtifactCandidateRow,
    CollaborationMessageRow,
    InstanceAttemptRow,
    Plan,
    Project,
    ReleaseRecordRow,
    Run,
    StageRow,
)


class _Mem:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.store: dict[tuple[type, Any], Any] = {}

    def add(self, row: Any) -> None:
        self.added.append(row)
        key = getattr(row, "id", None)
        if key is not None:
            self.store[(type(row), key)] = row
        if isinstance(row, Plan):
            self.store[(Plan, (row.run_id, row.version))] = row

    def flush(self) -> None:
        return None

    def get(self, model: type, key: Any, **_k: Any) -> Any | None:
        return self.store.get((model, key))

    def scalar(self, stmt: Any) -> Any | None:
        return None

    def scalars(self, stmt: Any) -> Any:
        class _R:
            def __init__(self, items: list[Any]) -> None:
                self._items = items

            def all(self) -> list[Any]:
                return self._items

        # Best-effort: return matching stored rows by model type in FROM.
        return _R([])


def _fence() -> LeaseFence:
    return LeaseFence(run_id="run_1", owner="o", token="t", lease_generation=1, thread_id="afg:1")


def test_released_input_rejects_unpublished_only_candidate() -> None:
    session = _Mem()
    run = MagicMock(id="run_1", project_id="proj_1")
    project = MagicMock(id="proj_1")
    session.store[(Run, "run_1")] = run
    session.store[(Project, "proj_1")] = project
    candidate = MagicMock(
        id="cand_1",
        project_id="proj_1",
        state="published",
        content_hash="a" * 64,
        contract_id="c",
        contract_version=1,
        contract_hash="b" * 64,
    )
    session.store[(ArtifactCandidateRow, "cand_1")] = candidate
    # No ReleaseRecord — resolve must fail.
    with pytest.raises(InstanceError) as exc:
        ReleasedInputResolver(session).resolve(  # type: ignore[arg-type]
            run_id="run_1", release_ids=["rel_missing"]
        )
    assert exc.value.code == "release_not_found"


def test_released_input_accepts_release_members() -> None:
    session = _Mem()
    run = MagicMock(id="run_1", project_id="proj_1")
    project = MagicMock(id="proj_1")
    session.store[(Run, "run_1")] = run
    session.store[(Project, "proj_1")] = project
    candidate = MagicMock(
        id="cand_1",
        project_id="proj_1",
        state="published",
        content_hash="a" * 64,
        contract_id="c",
        contract_version=1,
        contract_hash="b" * 64,
    )
    session.store[(ArtifactCandidateRow, "cand_1")] = candidate
    release = MagicMock(
        id="rel_1",
        candidate_versions=[
            {
                "candidateId": "cand_1",
                "workPackageId": "wp_up",
                "contentHash": "a" * 64,
                "artifactVersion": 1,
            }
        ],
    )
    session.store[(ReleaseRecordRow, "rel_1")] = release
    bundle = ReleasedInputResolver(session).resolve(  # type: ignore[arg-type]
        run_id="run_1",
        release_ids=["rel_1"],
        required_work_package_ids={"wp_up"},
    )
    assert bundle.candidate_refs == ["cand_1"]
    assert bundle.digest


def test_submission_bridge_rejects_natural_language_fact() -> None:
    session = _Mem()
    msg = MagicMock(
        id="msg_1",
        message_type="fact.turn_finished",
        run_id="run_1",
        stage_id=None,
        sender_ref="inst_1",
        payload={"summary": "done"},
    )
    session.store[(CollaborationMessageRow, "msg_1")] = msg
    with pytest.raises(InstanceError, match="not a structured submission"):
        SubmissionBridge(session).accept("msg_1")  # type: ignore[arg-type]


def test_submission_bridge_accepts_bound_stage_submission() -> None:
    session = _Mem()
    run = MagicMock(
        id="run_1",
        status="running",
        active_generation=1,
    )
    attempt = MagicMock(
        id="ia_1",
        run_id="run_1",
        generation=1,
        stage_id="stage_1",
        instance_id="inst_1",
        work_package_id="wp_1",
        status="running",
        submission_id=None,
    )
    instance = MagicMock(id="inst_1", native_session_id="ns_1")
    msg = MagicMock(
        id="msg_sub",
        message_type="stage.submission",
        run_id="run_1",
        stage_id="stage_1",
        sender_ref="inst_1",
        payload={
            "type": "stage.submission",
            "stageId": "stage_1",
            "candidateRef": "cand_1",
            "attemptRefs": ["ia_1"],
        },
    )
    session.store[(Run, "run_1")] = run
    session.store[(InstanceAttemptRow, "ia_1")] = attempt
    session.store[(AgentInstanceRow, "inst_1")] = instance
    session.store[(CollaborationMessageRow, "msg_sub")] = msg

    stages = MagicMock()
    stages.submit.return_value = MagicMock(status="accepted", operation_id="sop_1")
    result = SubmissionBridge(session, stage_service=stages).accept("msg_sub")  # type: ignore[arg-type]
    assert result["status"] == "accepted"
    assert attempt.status == "submitted"
    stages.submit.assert_called_once()
    body = stages.submit.call_args.args[1]
    assert body.idempotency_key == "msg:msg_sub"


def test_dispatch_returns_existing_operation_for_same_key() -> None:
    session = _Mem()
    run = MagicMock(
        id="run_1",
        project_id="proj_1",
        status="running",
        plan_version=1,
        assignment_version=1,
        gate_policy_version=1,
        active_generation=1,
        cancel_requested_at=None,
    )
    session.store[(Run, "run_1")] = run
    existing = MagicMock(
        id="sop_old",
        status="accepted",
        request_hash="deadbeef",
        result_refs=["ia_old"],
        binding_json={"attemptId": "ia_old", "messageId": "msg_old"},
    )
    with (
        patch("agentforge.driver.dispatch.assert_driver_fence"),
        patch("agentforge.driver.dispatch.EligibilityService.assert_may_dispatch"),
        patch("agentforge.driver.dispatch.PlanSpec") as plan_spec,
        patch.object(DispatchService, "_select_instance") as select_inst,
        patch.object(DispatchService, "_upstream_stage_ids", return_value=[]),
        patch("agentforge.driver.dispatch.find_release_ids_for_upstream", return_value=[]),
        patch.object(ReleasedInputResolver, "resolve") as resolve,
        patch("agentforge.driver.dispatch.sha256_hex", return_value="deadbeef"),
        patch(
            "agentforge.driver.dispatch.DriverRepository.find_operation",
            return_value=existing,
        ),
    ):
        wp = MagicMock(
            id="wp_1",
            stage_id="s1",
            owner_role_id="role_1",
            objective="do",
            depends_on=[],
        )
        plan_spec.model_validate.return_value = MagicMock(work_packages=[wp], stages=[])
        session.store[(Plan, ("run_1", 1))] = MagicMock(
            status="approved", spec_json={"schemaVersion": "1"}
        )
        session.store[(StageRow, "run_1:p1:s1")] = MagicMock(id="run_1:p1:s1")
        select_inst.return_value = MagicMock(id="inst_1", workspace_id="ws", native_session_id="ns")
        resolve.return_value = MagicMock(
            release_refs=[],
            candidate_refs=[],
            digest="d",
            bundle_id="ctx",
            as_dict=lambda: {},
        )
        result = DispatchService(session).reserve_and_create_message(  # type: ignore[arg-type]
            _fence(), "wp_1"
        )
    assert result["attemptId"] == "ia_old"
    assert result["operationId"] == "sop_old"
