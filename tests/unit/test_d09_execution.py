"""D09 bound identities and committed native dispatch, using explicit test repository."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from agentforge.contracts.agents import ExecutionRequest
from agentforge.contracts.messages import MessageCreateRequest
from agentforge.instances.eligibility import EligibilityService
from agentforge.instances.errors import InstanceError
from agentforge.instances.preparation import ExecutionPreparationService
from agentforge.instances.worker import process_delivery, process_instance_operation
from agentforge.messaging.protocol import ProtocolRouter
from agentforge.messaging.service import MessageService
from agentforge.storage import models as m
from tests.support.collaboration import seed


@pytest.fixture
def setup(tmp_path, monkeypatch):
    s, spec = seed(tmp_path)
    monkeypatch.setattr(
        "agentforge.projects.grants.require_active_grant",
        lambda session, grant_id: session.get(m.DirectoryGrantRow, grant_id),
    )
    monkeypatch.setattr("agentforge.storage.uow.unit_of_work_connection", s.transaction)
    monkeypatch.setattr("agentforge.instances.worker.unit_of_work_connection", s.transaction)
    from contextlib import nullcontext

    monkeypatch.setattr("agentforge.instances.worker.native_lane", lambda *args: nullcontext())
    return s, spec


def prepare(s, spec):
    plan = s.get(m.Plan, ("run", 1))
    result = ExecutionPreparationService(s).prepare(
        "run",
        ExecutionRequest(
            planHash=plan.plan_hash, planVersion=1, assignmentVersion=1, gatePolicyVersion=1
        ),
        actor="test",
        idempotency_key="prepare",
    )
    op = next(r for r in s.added if isinstance(r, m.InstanceOperationRow))
    return result, op


def adapter_for(s, op):
    adapter = Mock()

    def create(**args):
        assert s.active == 0
        assert s.get(m.InstanceOperationRow, op.id).status == "unknown"
        context = args["context"]
        assert context["workspaceRoot"] != ""
        return {
            "status": "succeeded",
            "sessionRef": "native",
            "workspaceId": context["workspaceId"],
            "cwd": context["workspaceRoot"],
        }

    adapter.create_session.side_effect = create
    return adapter


def test_prepare_uses_real_workspace_and_config(setup):
    s, spec = setup
    result, op = prepare(s, spec)
    instance = s.get(m.AgentInstanceRow, op.instance_id)
    assert instance.workspace_id.startswith("workspace-")
    assert instance.generation == 1
    again = ExecutionPreparationService(s).prepare(
        "run",
        ExecutionRequest(
            planHash=s.get(m.Plan, ("run", 1)).plan_hash,
            planVersion=1,
            assignmentVersion=1,
            gatePolicyVersion=1,
        ),
        actor="test",
        idempotency_key="prepare",
    )
    assert again.operation_id == result.operation_id


def test_missing_workspace_blocks_before_session(setup):
    s, spec = setup
    ws = next(r for r in s.added if isinstance(r, m.GitWorkspaceRow))
    s.delete(ws)
    with pytest.raises(InstanceError, match="independent active workspace"):
        prepare(s, spec)


def test_unverified_profile_blocks_prepare(setup):
    s, spec = setup
    profile = next(r for r in s.added if isinstance(r, m.AgentProfileRow))
    profile.auth_status = "unknown"
    with pytest.raises(InstanceError, match="authentication"):
        prepare(s, spec)


def test_session_dispatch_outside_transaction_and_not_repeated(setup):
    s, spec = setup
    _, op = prepare(s, spec)
    adapter = adapter_for(s, op)
    assert (
        process_instance_operation(object(), op.id, adapter_factory=lambda _: adapter)["status"]
        == "succeeded"
    )
    assert (
        process_instance_operation(object(), op.id, adapter_factory=lambda _: adapter)["status"]
        == "succeeded"
    )
    assert adapter.create_session.call_count == 1
    instance = s.get(m.AgentInstanceRow, op.instance_id)
    assert instance.session_ref == "native"


def test_session_crash_after_native_receipt_never_recreates(setup):
    s, spec = setup
    _, op = prepare(s, spec)
    adapter = adapter_for(s, op)
    adapter.create_session.side_effect = SystemExit("native succeeded; process died")
    with pytest.raises(SystemExit):
        process_instance_operation(object(), op.id, adapter_factory=lambda _: adapter)
    assert (
        process_instance_operation(object(), op.id, adapter_factory=lambda _: adapter)["status"]
        == "unknown"
    )
    assert adapter.create_session.call_count == 1


def test_create_cannot_claim_success_with_wrong_native_cwd(setup):
    s, spec = setup
    _, op = prepare(s, spec)
    adapter = adapter_for(s, op)
    adapter.create_session.side_effect = lambda **kw: {
        "status": "succeeded",
        "sessionRef": "n",
        "workspaceId": kw["workspace_id"],
        "cwd": "wrong",
    }
    assert (
        process_instance_operation(object(), op.id, adapter_factory=lambda _: adapter)["status"]
        == "unknown"
    )


def test_missing_workpackage_and_closed_stage_refused(setup):
    s, spec = setup
    stage = next(r for r in s.added if isinstance(r, m.StageRow))
    wp = spec.work_packages[0]
    assert (
        EligibilityService(s)
        .check(run_id="run", stage_id=stage.id, work_package_id="missing")
        .blocked_reason
    )
    stage.write_access_closed = True
    assert (
        EligibilityService(s)
        .check(run_id="run", stage_id=stage.id, work_package_id=wp.id)
        .blocked_reason
        == "stage_writes_closed"
    )


def test_task_sent_through_submit_not_message_and_uses_current_attempt(setup):
    s, spec = setup
    _, op = prepare(s, spec)
    adapter = adapter_for(s, op)
    process_instance_operation(object(), op.id, adapter_factory=lambda _: adapter)
    instance = s.get(m.AgentInstanceRow, op.instance_id)
    stage = next(r for r in s.added if isinstance(r, m.StageRow))
    wp = spec.work_packages[0]
    MessageService(s).create(
        "run",
        MessageCreateRequest.model_validate(
            {
                "recipientRefs": [instance.id],
                "stageId": stage.id,
                "workPackageId": wp.id,
                "payload": {
                    "type": "task.assignment",
                    "workPackageId": wp.id,
                    "stageId": stage.id,
                    "objective": wp.objective,
                },
            }
        ),
        sender_ref="user:test",
    )
    delivery = next(r for r in s.added if isinstance(r, m.MessageDeliveryRow))

    def submit(**kw):
        assert s.active == 0
        assert kw["payload"]["context"]["attemptId"]
        return {"status": "received", "receiptRef": "receipt"}

    adapter.submit_task.side_effect = submit
    result = process_delivery(object(), delivery.id, adapter_factory=lambda _: adapter)
    assert result["status"] == "received"
    adapter.deliver_message.assert_not_called()
    assert (
        process_delivery(object(), delivery.id, adapter_factory=lambda _: adapter)["status"]
        == "received"
    )
    assert adapter.submit_task.call_count == 1


def test_protocol_missing_bound_context_refused(setup):
    s, spec = setup
    s.add(
        m.ProtocolRequestRow(
            id="p",
            instance_id="i",
            native_session_id="session",
            attempt_id="attempt",
            generation=1,
            status="pending",
            allowed_response_types=["control.stop_response"],
            legal_responder_refs=["i"],
            deadline_at=datetime.now(UTC) + timedelta(minutes=1),
        )
    )
    with pytest.raises(InstanceError, match="session mismatch"):
        ProtocolRouter(s).match_response(
            request_id="p",
            response_type="control.stop_response",
            responder_ref="i",
            instance_id="i",
            response_ref="r",
        )


def test_old_transaction_bound_coordinator_refused(setup):
    from agentforge.instances.session_coordinator import SessionCoordinator

    with pytest.raises(InstanceError, match="Engine"):
        SessionCoordinator(setup[0]).process_create_claims()


def test_next_stage_uses_predecessor_release_not_its_own(setup):
    s, spec = setup
    from agentforge.contracts.plans import PlanSpec

    original = spec.model_dump(mode="json", by_alias=True)
    first = original["stages"][0]["stageId"]
    first_wp = original["workPackages"][0]["id"]
    next_stage = {
        **original["stages"][0],
        "stageId": "next",
        "dependencies": [first],
        "requiredWorkPackages": ["next-wp"],
    }
    next_wp = {
        **original["workPackages"][0],
        "id": "next-wp",
        "stageId": "next",
        "dependsOn": [first_wp],
    }
    original["stages"].append(next_stage)
    original["workPackages"].append(next_wp)
    s.get(m.Plan, ("run", 1)).spec_json = PlanSpec.model_validate(original).model_dump(
        mode="json", by_alias=True
    )
    s.add(
        m.StageRow(
            id="run:p1:next",
            run_id="run",
            plan_version=1,
            assignment_version=1,
            gate_policy_version=1,
            gate_policy=next_stage["gatePolicy"],
            status="pending",
            write_access_closed=False,
        )
    )
    service = EligibilityService(s)
    assert (
        service.check(
            run_id="run", stage_id="run:p1:next", work_package_id="next-wp"
        ).blocked_reason
        == "upstream_gate_missing"
    )
    s.add(m.ArtifactCandidateRow(id="upstream", content_hash="a" * 64, state="published"))
    s.add(
        m.ReleaseRecordRow(
            id="release",
            stage_id="run:p1:" + first,
            work_package_ids=[first_wp],
            gate_decision_id="gate",
            candidate_versions=[{"candidateId": "upstream", "contentHash": "a" * 64}],
        )
    )
    assert (
        service.check(
            run_id="run", stage_id="run:p1:next", work_package_id="next-wp"
        ).blocked_reason
        is None
    )


def test_safe_boundary_wait_does_not_send_or_claim_unknown(setup):
    s, spec = setup
    _, op = prepare(s, spec)
    adapter = adapter_for(s, op)
    process_instance_operation(object(), op.id, adapter_factory=lambda _: adapter)
    instance = s.get(m.AgentInstanceRow, op.instance_id)
    MessageService(s).create(
        "run",
        MessageCreateRequest.model_validate(
            {
                "recipientRefs": [instance.id],
                "payload": {"type": "clarification.request", "question": "Need clarification"},
            }
        ),
        sender_ref="user:test",
    )
    delivery = next(r for r in s.added if isinstance(r, m.MessageDeliveryRow))
    delivery.delivery_mode = "next_safe_boundary"
    instance.activity = "busy"
    with pytest.raises(InstanceError, match="safe boundary"):
        process_delivery(object(), delivery.id, adapter_factory=lambda _: adapter)
    assert delivery.transmission_status == "pending"
    adapter.deliver_message.assert_not_called()
