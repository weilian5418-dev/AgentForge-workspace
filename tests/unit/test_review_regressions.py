"""Regression tests for the seven authorization, dispatch and recovery findings."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentforge.adapter_host.host import AdapterHost
from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.git_workspaces import GitStopEvidence
from agentforge.contracts.runner import RunnerExecuteRequest, RunnerTicketIssueRequest
from agentforge.git.service import GitWorkspaceError, GitWorkspaceService
from agentforge.permissions.service import (
    apply_forward_result,
    claim_pending_forward,
    process_pending_forward,
    try_consume_once,
)
from agentforge.queue.constants import INTENT_APPROVAL_FORWARD
from agentforge.queue.dispatcher import ClaimSnapshot, _enqueue
from agentforge.runner.executor import execute_runner
from agentforge.runner.service import RunnerError, RunnerService
from agentforge.runtime.execution_context import (
    ExecutionContextError,
    bind_execution_context,
    recovery_all_ok,
    verify_recovery,
)
from agentforge.storage.models import ApprovalMappingRow, GitWorkspaceRow, RunnerOperationRow


class MemoryLedger:
    """Explicit test repository: never selected by a production exception fallback."""

    def __init__(self):
        self.rows = {}
        self.active = 0
        self.commits = 0

    def add(self, row):
        self.rows[(type(row), row.id)] = row

    def get(self, model, key, **kwargs):
        return self.rows.get((model, key))

    def flush(self):
        pass

    def execute(self, statement):
        assert "pg_advisory_xact_lock" in str(statement)

    def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        return SimpleNamespace(
            all=lambda: [r for (model, _), r in self.rows.items() if model is entity]
        )

    @contextmanager
    def transaction(self, _engine):
        self.active += 1
        try:
            yield SimpleNamespace(session=self)
        except BaseException:
            raise
        else:
            self.commits += 1
        finally:
            self.active -= 1


@pytest.fixture
def ledger(monkeypatch):
    ledger = MemoryLedger()
    monkeypatch.setattr("agentforge.storage.uow.unit_of_work_connection", ledger.transaction)
    monkeypatch.setattr("agentforge.runner.executor.unit_of_work_connection", ledger.transaction)
    return ledger


def pending_approval(ledger):
    row = ApprovalMappingRow(
        id="approval",
        status="forwarding",
        user_decision="approve",
        native_confirmed=False,
        action_digest="a" * 64,
        action="read",
        native_request_id="native",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        mapping_json={
            "targetSummary": "workspace",
            "parameterSummary": "read file",
            "forwardPending": True,
            "forwardOutboxId": "dispatch",
            "adapterId": "test",
            "sessionRef": "session",
            "semantic": "once",
            "bridgeNativeDecision": "allow",
            "bridgeMappedOptions": {"once": True},
        },
    )
    ledger.add(row)
    return row


def test_approval_io_happens_after_claim_commit(ledger, monkeypatch):
    row = pending_approval(ledger)

    def native(**kwargs):
        assert ledger.active == 0
        assert ledger.commits == 1
        assert row.status == "delivery_unknown"
        assert kwargs["mapped_options"] == {"once": True}
        return {"status": "acked", "confirmationRef": "receipt"}

    monkeypatch.setattr(
        "agentforge.permissions.service.get_adapter",
        lambda _: SimpleNamespace(respond_approval=native),
    )
    result = process_pending_forward(object(), row.id, operation_id="dispatch")
    assert result.status == "confirmed"
    assert ledger.commits == 2
    assert process_pending_forward(object(), row.id, operation_id="dispatch").status == "confirmed"


@pytest.mark.parametrize("invalid", ["expired", "revoked", "stale_job"])
def test_invalid_pending_approval_never_sent(ledger, monkeypatch, invalid):
    row = pending_approval(ledger)
    adapter = Mock()
    monkeypatch.setattr("agentforge.permissions.service.get_adapter", lambda _: adapter)
    if invalid == "expired":
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    if invalid == "revoked":
        row.mapping_json = {**row.mapping_json, "revokedAt": datetime.now(UTC).isoformat()}
    process_pending_forward(
        object(), row.id, operation_id="old" if invalid == "stale_job" else "dispatch"
    )
    adapter.respond_approval.assert_not_called()


def test_forward_process_death_does_not_allow_second_send(ledger, monkeypatch):
    row = pending_approval(ledger)
    adapter = Mock()
    adapter.respond_approval.side_effect = SystemExit(
        "simulated process death after native receipt"
    )
    monkeypatch.setattr("agentforge.permissions.service.get_adapter", lambda _: adapter)
    with pytest.raises(SystemExit):
        process_pending_forward(object(), row.id)
    assert ledger.commits == 1
    assert row.status == "delivery_unknown"
    assert process_pending_forward(object(), row.id).status == "delivery_unknown"
    assert adapter.respond_approval.call_count == 1


def test_forward_transport_exception_is_unknown_and_not_replayed(ledger, monkeypatch):
    row = pending_approval(ledger)
    adapter = Mock()
    adapter.respond_approval.side_effect = TimeoutError("synthetic timeout")
    monkeypatch.setattr("agentforge.permissions.service.get_adapter", lambda _: adapter)
    for _ in range(2):
        assert process_pending_forward(object(), row.id).status == "delivery_unknown"
    assert adapter.respond_approval.call_count == 1


def test_late_ack_cannot_restore_revoked_authorization(ledger):
    row = pending_approval(ledger)
    claim = claim_pending_forward(ledger, row.id)
    row.mapping_json = {**row.mapping_json, "revokedAt": datetime.now(UTC).isoformat()}
    result = apply_forward_result(ledger, claim, {"status": "acked", "confirmationRef": "late"})
    assert result.native_confirmed is False
    assert result.status == "rejected"


def test_atomic_consume_database_failure_never_falls_back():
    row = ApprovalMappingRow(
        id="a",
        status="confirmed",
        user_decision="approve",
        native_confirmed=True,
        native_request_id="n",
        mapping_json={"semantic": "once"},
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    session = Mock()
    session.execute.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        try_consume_once(session, row, operation_id="op")
    assert row.status == "confirmed"
    assert "consumedAt" not in row.mapping_json


def test_approval_outbox_uses_registered_queue_task():
    task = Mock()
    app = SimpleNamespace(tasks={"agentforge.forward_approval": task})
    _enqueue(
        app,
        ClaimSnapshot(
            "outbox",
            "owner",
            "token",
            INTENT_APPROVAL_FORWARD,
            {"operationId": "dispatch", "approvalId": "approval"},
        ),
    )
    task.configure.return_value.defer.assert_called_once_with(
        approval_id="approval", operation_id="dispatch", task_version=1
    )


def test_actual_queue_blueprint_registers_approval_task():
    from agentforge.core.config import Settings
    from agentforge.queue.app import build_queue_app

    app = build_queue_app(
        Settings(queue_database_url="postgresql+psycopg://unused@127.0.0.1/unused")
    )
    assert "agentforge.forward_approval" in app.tasks


def issue_runner(ledger, root):
    ticket = RunnerService(ledger).issue(
        RunnerTicketIssueRequest.model_validate(
            {
                "profileId": "render",
                "workspaceId": "workspace",
                "workspaceRoot": str(root),
                "scope": "file:write:output.txt",
                "params": {"path": "output.txt", "content": "done"},
                "attemptId": "attempt",
                "expiresAt": datetime.now(UTC) + timedelta(minutes=10),
            }
        )
    )
    return RunnerExecuteRequest(operationId=ticket.operation_id, actionDigest=ticket.action_digest)


def test_runner_side_effect_is_outside_db_transaction(ledger, monkeypatch, tmp_path):
    request = issue_runner(ledger, tmp_path)
    from agentforge.runner.executor import perform_runner_dispatch

    def dispatch(claim):
        assert ledger.active == 0
        assert ledger.commits == 1
        return perform_runner_dispatch(claim)

    monkeypatch.setattr("agentforge.runner.executor.perform_runner_dispatch", dispatch)
    assert execute_runner(object(), request).status == "succeeded"
    assert (tmp_path / "output.txt").read_text() == "done"
    assert ledger.commits == 2


def test_runner_crash_after_write_leaves_durable_non_replayable_claim(
    ledger, monkeypatch, tmp_path
):
    request = issue_runner(ledger, tmp_path)

    def dispatch(claim):
        assert ledger.active == 0
        (tmp_path / "output.txt").write_text("written-before-crash")
        raise SystemExit("process death")

    monkeypatch.setattr("agentforge.runner.executor.perform_runner_dispatch", dispatch)
    with pytest.raises(SystemExit):
        execute_runner(object(), request)
    with pytest.raises(RunnerError, match="Prior execution may have started"):
        execute_runner(object(), request)
    assert (tmp_path / "output.txt").read_text() == "written-before-crash"


def test_client_file_and_ordinary_terminal_op_cannot_forge_stop(ledger, tmp_path):
    workspace = GitWorkspaceRow(
        id="w", project_id="p", status="active", attempt_id="a", worktree_path=str(tmp_path)
    )
    operation = RunnerOperationRow(
        id="op",
        workspace_id="w",
        workspace_root=str(tmp_path),
        attempt_id="a",
        generation=1,
        profile_id="read_snippet",
        status="succeeded",
        result_json={},
    )
    ledger.add(workspace)
    ledger.add(operation)
    (tmp_path / "fake.json").write_text('{"stopped":true}')
    claim = GitStopEvidence(
        source="runner",
        operationId="op",
        workspaceId="w",
        attemptId="a",
        generation=1,
        evidenceSha256="a" * 64,
        evidencePath="fake.json",
        writableScope="anything",
        writesQuiesced=True,
        checkedAt=datetime.now(UTC),
    )
    service = GitWorkspaceService(ledger, ArtifactStore(tmp_path / "objects"), tmp_path / "state")
    with pytest.raises(GitWorkspaceError, match="authoritative Runner"):
        service._assert_stop_evidence(workspace, claim)


def context(tmp_path, **overrides):
    args = dict(
        profile_id="p",
        operation_id="op",
        instance_id="i",
        workspace_id="w",
        workspace_root=str(tmp_path),
        environment_id="env",
        generation=1,
        session_ref="s",
    )
    args.update(overrides)
    return bind_execution_context(**args)


def test_session_only_is_not_full_recovery(tmp_path):
    checks = verify_recovery(context(tmp_path), native_session_ok=True)
    assert not recovery_all_ok(checks)
    assert recovery_all_ok(checks, required_facets=frozenset({"session"}))


def test_duplicate_recovery_facets_not_accepted(tmp_path):
    checks = verify_recovery(
        context(tmp_path), native_session_ok=True, workspace_identity_ok=True, file_version_ok=True
    )
    assert recovery_all_ok(checks)
    assert not recovery_all_ok(checks + [checks[0]])


@pytest.mark.parametrize(
    "args",
    [
        {"workspace_root": ""},
        {"workspace_root": "relative"},
        {"expected_attempt_id": "required", "attempt_id": None},
    ],
)
def test_context_rejects_implicit_root_or_missing_expected_attempt(tmp_path, args):
    with pytest.raises(ExecutionContextError):
        context(tmp_path, **args)


def test_host_rejects_contextless_legacy_operation():
    host = AdapterHost()
    host.register_profile("p")
    with pytest.raises(PermissionError, match="ExecutionContext is required"):
        host.assert_allowed(
            profile_id="p",
            instance_id="unbound",
            workspace_id="unrelated",
            operation_id="arbitrary",
        )


def test_stop_record_does_not_overclaim_external_agent_execution(ledger, tmp_path):
    from agentforge.storage.models import AgentInstanceRow

    request = issue_runner(ledger, tmp_path)
    row = ledger.get(RunnerOperationRow, request.operation_id)
    row.attempt_id = "attempt"
    assert execute_runner(object(), request).status == "succeeded"
    ledger.add(AgentInstanceRow(id="agent", workspace_id="workspace", status="unknown"))
    with pytest.raises(RunnerError, match="Native Agent execution is not confirmed"):
        RunnerService(ledger).record_stop_evidence(row.id)


def test_pending_write_ticket_cannot_run_after_freeze(ledger, tmp_path):
    request = issue_runner(ledger, tmp_path)
    ledger.add(
        GitWorkspaceRow(id="workspace", worktree_path=str(tmp_path), status="snapshot_frozen")
    )
    with pytest.raises(RunnerError, match="not open for writes"):
        execute_runner(object(), request)
    assert not (tmp_path / "output.txt").exists()


def test_d06_alternate_freeze_path_rejects_forged_stop(ledger, tmp_path):
    from agentforge.artifacts.service import ArtifactBusError, ArtifactService
    from agentforge.contracts.artifact_bus import FreezeCandidateRequest

    ledger.add(
        RunnerOperationRow(
            id="op",
            workspace_id="w",
            workspace_root=str(tmp_path),
            attempt_id="a",
            generation=1,
            profile_id="read_snippet",
            status="succeeded",
            result_json={},
        )
    )
    (tmp_path / "source.txt").write_text("candidate")
    request = FreezeCandidateRequest.model_validate(
        {
            "projectId": "p",
            "workspaceId": "w",
            "workspaceRoot": str(tmp_path),
            "relativePath": "source.txt",
            "contractId": "c",
            "contractVersion": 1,
            "environmentHash": "a" * 64,
            "stopEvidence": {
                "source": "runner",
                "operationId": "op",
                "workspaceId": "w",
                "attemptId": "a",
                "generation": 1,
                "evidenceSha256": "b" * 64,
                "writableScope": "w/",
                "writesQuiesced": True,
                "checkedAt": datetime.now(UTC),
            },
        }
    )
    with pytest.raises(ArtifactBusError, match="authoritative Runner"):
        ArtifactService(ledger, ArtifactStore(tmp_path / "objects")).freeze_candidate(request)


def test_trusted_stop_must_match_scope_and_remain_current(ledger, tmp_path):
    from agentforge.contracts.git_workspaces import GitStopEvidence
    from agentforge.runner.quiescence import StopProofError, validate_stop_proof

    request = issue_runner(ledger, tmp_path)
    execute_runner(object(), request)
    ticket = RunnerService(ledger).record_stop_evidence(request.operation_id)
    proof = ticket.result["stopEvidence"]
    claim = GitStopEvidence.model_validate(
        {
            key: proof[key]
            for key in (
                "source",
                "operationId",
                "workspaceId",
                "attemptId",
                "generation",
                "evidenceSha256",
                "writableScope",
                "writesQuiesced",
                "checkedAt",
            )
        }
    )
    validate_stop_proof(
        ledger,
        workspace_id="workspace",
        workspace_root=str(tmp_path),
        expected_attempt_id="attempt",
        claim=claim,
    )
    with pytest.raises(StopProofError, match="scope"):
        validate_stop_proof(
            ledger,
            workspace_id="workspace",
            workspace_root=str(tmp_path),
            expected_attempt_id="attempt",
            claim=claim.model_copy(update={"writable_scope": "different"}),
        )
    ledger.add(
        RunnerOperationRow(
            id="later", workspace_id="workspace", status="succeeded", updated_at=datetime.now(UTC)
        )
    )
    with pytest.raises(StopProofError, match="executed again"):
        validate_stop_proof(
            ledger,
            workspace_id="workspace",
            workspace_root=str(tmp_path),
            expected_attempt_id="attempt",
            claim=claim,
        )
