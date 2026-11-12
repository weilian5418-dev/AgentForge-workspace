"""Authority regressions and the real filesystem collaboration path (native adapter simulated)."""

from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from agentforge.contracts.agents import ExecutionRequest
from agentforge.contracts.messages import MessageCreateRequest
from agentforge.contracts.stages import StageSubmissionRequest
from agentforge.core.config import Settings
from agentforge.core.service_identity import issue_ticket, verify_ticket
from agentforge.instances.preparation import ExecutionPreparationService
from agentforge.instances.worker import process_delivery, process_instance_operation
from agentforge.messaging.service import MessageService
from agentforge.queue.app import build_queue_app
from agentforge.queue.dispatcher import ClaimSnapshot, _enqueue
from agentforge.stages.service import StageService
from agentforge.stages.worker import process_submission
from agentforge.storage import models as m
from tests.support.collaboration import seed


@pytest.mark.parametrize(
    "intent,extra",
    [
        ("instance.create_session", {"instanceId": "i"}),
        ("message.deliver", {"deliveryId": "d"}),
        ("stage.stop_verify", {"submissionId": "s"}),
        ("stage.released", {"releaseId": "r"}),
        ("stage.evaluate", {"stageId": "stage"}),
    ],
)
def test_all_collaboration_outbox_types_have_registered_route(intent, extra):
    task = Mock()
    app = SimpleNamespace(tasks={"agentforge.collaboration_operation": task})
    payload = {"operationId": "operation", **extra}
    _enqueue(app, ClaimSnapshot("outbox", "owner", "token", intent, payload))
    assert task.configure.call_args.kwargs["queue"] == "agentforge-control"
    task.configure.return_value.defer.assert_called_once_with(
        intent_type=intent, payload=payload, task_version=1
    )


def test_blueprint_registers_collaboration_task_without_connecting():
    app = build_queue_app(
        Settings(queue_database_url="postgresql+psycopg://unused@127.0.0.1/unused")
    )
    assert "agentforge.collaboration_operation" in app.tasks


def test_internal_ticket_is_scoped_not_a_client_identity_header():
    settings = Settings(internal_signing_key="x" * 48)
    token = issue_ticket(
        settings, subject="reviewer", audience="stage-review", request_id="request"
    )
    assert (
        verify_ticket(settings, token, audience="stage-review", request_id="request") == "reviewer"
    )
    for audience, request in [("native-event", "request"), ("stage-review", "different")]:
        with pytest.raises(ValueError):
            verify_ticket(settings, token, audience=audience, request_id=request)
    with pytest.raises(ValueError):
        verify_ticket(settings, "reviewer", audience="stage-review", request_id="request")


def test_plain_user_cannot_forge_internal_native_source(settings):
    from agentforge.api.app import create_app

    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/v1/internal/native-events",
            headers={
                "Authorization": "Bearer " + settings.api_token.get_secret_value(),
                "X-Internal-Service": "anything",
            },
            json={
                "instanceId": "i",
                "dedupeKey": "d",
                "authentic": True,
                "eventType": "fact.idle",
                "source": "adapter",
            },
        )
    assert response.status_code == 403


def test_filesystem_pipeline_without_boolean_gate_shortcuts(tmp_path, monkeypatch):
    s, spec = seed(tmp_path / "project")
    monkeypatch.setattr(
        "agentforge.projects.grants.require_active_grant",
        lambda session, ref: session.get(m.DirectoryGrantRow, ref),
    )
    for module in ["agentforge.instances.worker", "agentforge.stages.worker"]:
        monkeypatch.setattr(module + ".unit_of_work_connection", s.transaction)
    monkeypatch.setattr("agentforge.instances.worker.native_lane", lambda *args: nullcontext())
    stage = next(r for r in s.added if isinstance(r, m.StageRow))
    wp = spec.work_packages[0]
    plan = s.get(m.Plan, ("run", 1))
    ExecutionPreparationService(s).prepare(
        "run",
        ExecutionRequest(
            planHash=plan.plan_hash, planVersion=1, assignmentVersion=1, gatePolicyVersion=1
        ),
        actor="test",
        idempotency_key="execute",
    )
    operation = next(r for r in s.added if isinstance(r, m.InstanceOperationRow))
    adapter = Mock()

    def create(**kw):
        assert s.active == 0
        return {
            "status": "succeeded",
            "sessionRef": "native",
            "workspaceId": kw["workspace_id"],
            "cwd": kw["context"]["workspaceRoot"],
        }

    def submit(**kw):
        assert s.active == 0
        output = Path(kw["payload"]["context"]["workspaceRoot"]) / wp.deliverables[0].relative_path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("actual candidate from simulated native execution")
        return {"status": "received", "receiptRef": "native-receipt"}

    adapter.create_session.side_effect = create
    adapter.submit_task.side_effect = submit
    adapter.request_stop.return_value = {"status": "accepted"}
    instance = s.get(m.AgentInstanceRow, operation.instance_id)
    adapter.inspect_execution.side_effect = lambda **kw: {
        "running": False,
        "childrenRunning": False,
        "writesQuiesced": True,
        "sessionRef": "native",
        "workspaceId": instance.workspace_id,
    }
    monkeypatch.setattr("agentforge.instances.worker.get_adapter", lambda _: adapter)
    process_instance_operation(object(), operation.id)
    MessageService(s).create(
        "run",
        MessageCreateRequest.model_validate(
            {
                "idempotencyKey": "task",
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
    assert process_delivery(object(), delivery.id)["status"] == "received"
    StageService(s).submit(
        stage.id,
        StageSubmissionRequest(
            workPackageIds=[wp.id],
            attemptRefs=[instance.current_attempt_id],
            idempotencyKey="submit",
        ),
        submitter_ref="user:test",
    )
    submission = next(r for r in s.added if isinstance(r, m.StageSubmissionRow))
    settings = Settings(state_dir=tmp_path / "state")
    assert process_submission(object(), settings, submission.id)["status"] == "released"
    decision = next(r for r in s.added if isinstance(r, m.GateDecisionRow))
    assert decision.validation_refs
    assert stage.status == "released"
    assert any(isinstance(r, m.ArtifactCandidateRow) for r in s.added)
    assert not s.active


def test_message_idempotency_does_not_duplicate_deliveries(tmp_path):
    s, spec = seed(tmp_path / "p")
    role = spec.roles[0]
    instance = m.AgentInstanceRow(
        id="agent",
        run_id="run",
        role_id=role.role_id,
        assignment_version=1,
        lifecycle="active",
        activity="idle",
        status="idle",
    )
    s.add(instance)
    body = MessageCreateRequest.model_validate(
        {
            "idempotencyKey": "same",
            "recipientRefs": ["agent"],
            "payload": {"type": "clarification.request", "question": "What is the current status?"},
        }
    )
    service = MessageService(s)
    assert (
        service.create("run", body, sender_ref="user:test").message_id
        == service.create("run", body, sender_ref="user:test").message_id
    )
    assert len([r for r in s.added if isinstance(r, m.MessageDeliveryRow)]) == 1


def test_model_planning_uses_gateway_and_rejects_late_result(tmp_path, monkeypatch):
    import agentforge.planning.executor as executor
    from agentforge.contracts.plans import PlanProposalRequest
    from agentforge.models.gateway import ModelGateway
    from agentforge.planning.executor import propose_plan
    from agentforge.planning.service import PlanningError

    ledger, spec = seed(tmp_path / "project")
    run = ledger.get(m.Run, "run")
    run.status = "planning"
    run.planning_provider_profile_id = "provider"
    ledger.add(m.ModelAuthorizationGrantRow(id="grant-model", purpose="planning"))
    monkeypatch.setattr(executor, "unit_of_work_connection", ledger.transaction)

    class GatewaySession:
        def __enter__(self):
            return ledger

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(executor, "Session", lambda _: GatewaySession())
    monkeypatch.setattr(ledger, "commit", lambda: None, raising=False)
    monkeypatch.setattr(
        "agentforge.planning.context.require_active_grant",
        lambda session, ref: session.get(m.DirectoryGrantRow, ref),
    )
    requests = []

    def invoke(self, request):
        assert ledger.active == 0
        requests.append(request)
        run.status_version += 1
        return SimpleNamespace(
            call_id="call",
            status="succeeded",
            result_text=spec.model_dump_json(by_alias=True),
            context_manifest_id="actual-manifest",
        )

    monkeypatch.setattr(ModelGateway, "invoke", invoke)
    with pytest.raises(PlanningError, match="Late model result"):
        propose_plan(
            object(),
            Settings(state_dir=tmp_path / "state"),
            "run",
            PlanProposalRequest(source="model", grantId="grant-model", idempotencyKey="proposal"),
        )
    assert requests[0].purpose == "planning" and requests[0].baseline_ids == ["baseline"]
    assert requests[0].grant_id == "grant-model"
    assert ledger.get(m.Run, "run").plan_version == 1


def test_protocol_omission_and_terminal_impersonation_fail(tmp_path):
    from agentforge.instances.errors import InstanceError
    from agentforge.messaging.protocol import ProtocolRouter

    s, spec = seed(tmp_path / "p")
    request = m.ProtocolRequestRow(
        id="request",
        instance_id="agent",
        native_session_id="native",
        attempt_id="attempt",
        generation=1,
        plan_version=1,
        assignment_version=1,
        stage_id="stage",
        status="matched",
        matched_response_ref="same",
        allowed_response_types=["control.stop_response"],
        legal_responder_refs=["agent"],
    )
    s.add(request)
    with pytest.raises(InstanceError):
        ProtocolRouter(s).match_response(
            request_id="request",
            response_type="control.stop_response",
            responder_ref="attacker",
            instance_id="agent",
            response_ref="same",
        )
    with pytest.raises(InstanceError, match="session mismatch"):
        ProtocolRouter(s).match_response(
            request_id="request",
            response_type="control.stop_response",
            responder_ref="agent",
            instance_id="agent",
            response_ref="same",
        )


def test_gate_conflict_policy_not_shadowed():
    from agentforge.contracts.stages import (
        CandidateSet,
        GateEvaluationInput,
        StageGatePolicy,
        WorkPackageCandidateRef,
    )
    from agentforge.stages.gate_evaluator import evaluate_gate

    cs = CandidateSet(
        candidateSetId="cs",
        stageId="s",
        members=[WorkPackageCandidateRef(workPackageId="wp", candidateId="c", artifactVersion=1)],
        requiredWorkPackagesComplete=True,
        contractHash="c" * 64,
        environmentFingerprint="e" * 64,
        manifestHash="a" * 64,
        createdAt=datetime.now(UTC),
    )
    inp = GateEvaluationInput(
        stageId="s",
        planVersion=1,
        assignmentVersion=1,
        gatePolicy=StageGatePolicy(conflictResolution="escalate_user"),
        candidateSet=cs,
        contractHash="c" * 64,
        validationPassedRequired=True,
        reviewRequestIds=["a", "b"],
        answeredReviewRequestIds=["a", "b"],
        reviewVerdicts=["approve", "request_changes"],
        stopVerified=True,
    )
    assert evaluate_gate(inp).outcome == "waiting"


def test_plain_user_cannot_impersonate_reviewer_header(settings):
    from agentforge.api.app import create_app

    payload = {
        "reviewRequestId": "request",
        "reviewCycleId": "cycle",
        "candidateSetId": "set",
        "manifestHash": "a" * 64,
        "contractHash": "c" * 64,
        "findings": [],
        "verdict": "approve",
    }
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/v1/stages/s/reviews",
            headers={
                "Authorization": "Bearer " + settings.api_token.get_secret_value(),
                "X-Reviewer-Instance-Id": "real-reviewer-id",
            },
            json=payload,
        )
        assert response.status_code == 403


def test_same_protocol_response_reference_with_changed_content_is_conflict(tmp_path):
    from agentforge.messaging.protocol import ProtocolRouter

    s, spec = seed(tmp_path / "p")
    s.add(
        m.ProtocolRequestRow(
            id="request",
            instance_id="agent",
            status="pending",
            allowed_response_types=["control.stop_response"],
            legal_responder_refs=["agent"],
        )
    )
    router = ProtocolRouter(s)
    args = {
        "request_id": "request",
        "response_type": "control.stop_response",
        "responder_ref": "agent",
        "instance_id": "agent",
        "response_ref": "same",
    }
    assert router.match_response(**args, content={"accepted": True})["status"] == "matched"
    assert (
        router.match_response(**args, content={"accepted": True})["status"] == "duplicate_ignored"
    )
    assert router.match_response(**args, content={"accepted": False})["status"] == "conflict"


def test_control_queue_requires_driver_database_identity():
    from unittest.mock import MagicMock

    from agentforge.queue.control_identity import require_control_role

    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = "agentforge_worker"
    with pytest.raises(RuntimeError, match="dedicated agentforge_driver"):
        require_control_role(engine)


def test_model_dispatch_commit_error_is_not_swallowed(settings):
    from agentforge.models.gateway import ModelGateway

    session = Mock()
    session.commit.side_effect = RuntimeError("commit unavailable")
    with pytest.raises(RuntimeError, match="commit unavailable"):
        ModelGateway(session, settings)._commit_intent()


@pytest.mark.parametrize(
    "case", ["metadata", "authorized", "denied", "changed", "oversize", "binary"]
)
def test_planning_input_content_is_explicit_bounded_and_verified(tmp_path, monkeypatch, case):
    import hashlib
    import json

    import agentforge.planning.executor as executor
    from agentforge.contracts.plans import PlanProposalRequest
    from agentforge.models.gateway import ModelGateway
    from agentforge.planning.service import PlanningError

    ledger, spec = seed(tmp_path / "project")
    run = ledger.get(m.Run, "run")
    run.status = "planning"
    run.planning_provider_profile_id = "provider"
    run.input_refs = ["input"]
    spec = spec.model_copy(update={"input_ids": ["input"]})
    settings = Settings(state_dir=tmp_path / "state")
    root = settings.state_dir / "workspaces" / "project" / "inputs"
    root.mkdir(parents=True)
    path = root / "brief.txt"
    content = b"\xff" if case == "binary" else b"authorized brief"
    path.write_bytes(content)
    ledger.add(
        m.ProjectInputRow(
            id="input",
            project_id="project",
            status="available",
            input_version=1,
            sha256="0" * 64 if case == "changed" else hashlib.sha256(content).hexdigest(),
            original_name="brief.txt",
            storage_ref=str(path),
        )
    )
    ledger.add(
        m.ModelAuthorizationGrantRow(
            id="grant-model",
            purpose="planning",
            allowed_input_ids=[] if case == "denied" else ["input"],
        )
    )
    monkeypatch.setattr(executor, "unit_of_work_connection", ledger.transaction)
    monkeypatch.setattr(
        "agentforge.planning.context.require_active_grant",
        lambda session, ref: session.get(m.DirectoryGrantRow, ref),
    )
    monkeypatch.setattr(executor, "Session", lambda _: nullcontext(ledger))
    monkeypatch.setattr(ledger, "commit", lambda: None, raising=False)
    requests = []

    def invoke(self, request):
        assert ledger.active == 0
        requests.append(request)
        run.status_version += 1
        return SimpleNamespace(
            call_id="call",
            status="succeeded",
            result_text=spec.model_dump_json(by_alias=True),
            context_manifest_id="manifest",
        )

    monkeypatch.setattr(ModelGateway, "invoke", invoke)
    expected = {
        "denied": "model_authorization_required",
        "changed": "context_changed",
        "oversize": "input_too_large",
        "binary": "input_binary_unavailable",
    }.get(case, "status_version_conflict")
    with pytest.raises(PlanningError) as error:
        executor.propose_plan(
            object(),
            settings,
            "run",
            PlanProposalRequest.model_validate(
                {
                    "source": "model",
                    "grantId": "grant-model",
                    "idempotencyKey": "proposal",
                    "includeInputContent": case != "metadata",
                    "maxInputBytes": 2 if case == "oversize" else 65536,
                }
            ),
        )
    assert error.value.code == expected
    if case in {"metadata", "authorized"}:
        sent = json.loads(requests[0].messages[1]["content"])["inputs"][0]
        assert ("text" in sent) == (case == "authorized")
        if case == "authorized":
            assert sent["text"] == content.decode()
    else:
        assert not requests
