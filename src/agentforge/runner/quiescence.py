"""Shared workspace locking and conservative checks for writable executions."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentforge.storage.models import (
    AgentInstanceRow,
    GitWorkspaceRow,
    RunnerOperationRow,
    TaskAttempt,
)


class WorkspaceBusyError(RuntimeError):
    pass


def lock_workspace(session: Session, workspace_id: str) -> GitWorkspaceRow | None:
    # Stable for Git and non-Git workspaces, including before a registry row exists.
    key = int.from_bytes(hashlib.sha256(workspace_id.encode()).digest()[:8], "big", signed=True)
    session.execute(select(func.pg_advisory_xact_lock(key)))
    return session.get(GitWorkspaceRow, workspace_id, with_for_update=True, populate_existing=True)


def assert_workspace_quiescent(
    session: Session, workspace_id: str, *, except_operation: str
) -> None:
    # Caller holds the same workspace lock used by Runner issue/claim/freeze.
    operations = session.scalars(
        select(RunnerOperationRow).where(
            RunnerOperationRow.workspace_id == workspace_id,
            RunnerOperationRow.status.in_(("issued", "running", "needs_reconciliation")),
        )
    ).all()
    if any(
        op.workspace_id == workspace_id
        and op.id != except_operation
        and op.status in {"issued", "running", "needs_reconciliation"}
        for op in operations
    ):
        raise WorkspaceBusyError("Runner operation is still active or unresolved")
    instances = session.scalars(
        select(AgentInstanceRow).where(AgentInstanceRow.workspace_id == workspace_id)
    ).all()
    if any(
        instance.workspace_id == workspace_id and instance.status not in {"stopped", "closed"}
        for instance in instances
    ):
        raise WorkspaceBusyError("Native Agent execution is not confirmed stopped")
    workspace = session.get(GitWorkspaceRow, workspace_id)
    if workspace is not None and workspace.attempt_id:
        attempt = session.get(TaskAttempt, workspace.attempt_id)
        if attempt is not None and attempt.status in {
            "leased",
            "running",
            "awaiting_stop_verification",
        }:
            raise WorkspaceBusyError("Attempt is still active or awaiting stop verification")


class StopProofError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_stop_proof(
    session: Session,
    *,
    workspace_id: str,
    workspace_root: str,
    expected_attempt_id: str | None,
    claim: Any,
) -> None:
    """Common D06/D07 trust boundary. Caller holds lock_workspace through freeze."""
    if claim.source == "adapter_host":
        from agentforge.storage.models import InstanceOperationRow

        operation = session.get(InstanceOperationRow, claim.operation_id)
        instance = session.get(AgentInstanceRow, operation.instance_id) if operation else None
        proof = (operation.payload_json or {}).get("stopProof") if operation else None
        if (
            operation is None
            or operation.kind != "request_stop"
            or operation.status != "succeeded"
            or instance is None
            or instance.lifecycle != "stopped"
            or instance.workspace_id != workspace_id
            or instance.current_attempt_id != claim.attempt_id
            or not isinstance(proof, dict)
        ):
            raise StopProofError("untrusted_stop_source", "No bound native stop operation")
        if (
            proof.get("workspaceId") != workspace_id
            or proof.get("attemptId") != claim.attempt_id
            or proof.get("generation") != claim.generation
            or proof.get("writesQuiesced") is not True
            or proof.get("sessionRef") != instance.session_ref
            or proof.get("checkedAt") != claim.checked_at.isoformat()
        ):
            raise StopProofError("stop_evidence_mismatch", "Native proof binding mismatch")
        checked = claim.checked_at
        if checked > datetime.now(UTC) or datetime.now(UTC) - checked > timedelta(seconds=60):
            raise StopProofError("stop_evidence_mismatch", "Native stop proof stale")
        digest = hashlib.sha256(
            json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if digest != claim.evidence_sha256:
            raise StopProofError("stop_evidence_mismatch", "Native stop digest mismatch")
        assert_workspace_quiescent(session, workspace_id, except_operation="")
        return
    if claim.source != "runner" or not claim.writes_quiesced:
        raise StopProofError("writes_not_quiesced", "Stop proof must confirm quiescence")
    op = session.get(RunnerOperationRow, claim.operation_id)
    if op is None:
        raise StopProofError("stop_evidence_missing", "Runner operation is missing")
    proof = (op.result_json or {}).get("stopEvidence")
    receipt = (op.result_json or {}).get("executionReceipt")
    if not isinstance(proof, dict) or not isinstance(receipt, dict):
        raise StopProofError("untrusted_stop_source", "No authoritative Runner stop record")
    if (
        op.status not in {"succeeded", "failed"}
        or not receipt.get("runnerSynchronousOnly")
        or receipt.get("operationId") != op.id
        or proof.get("operationId") != op.id
        or proof.get("coverage") != "runner_synchronous_only"
        or not proof.get("receiptToken")
        or proof.get("receiptToken") != receipt.get("dispatchToken")
    ):
        raise StopProofError("stop_not_terminal", "Execution is not confirmed quiescent")
    root = str(Path(workspace_root).resolve(strict=True))
    if (
        claim.workspace_id != workspace_id
        or op.workspace_id != workspace_id
        or proof.get("workspaceId") != workspace_id
        or str(Path(op.workspace_root).resolve(strict=True)) != root
        or proof.get("workspaceRoot") != root
    ):
        raise StopProofError("stop_workspace_mismatch", "Stop record is for a different workspace")
    if (
        not op.attempt_id
        or op.attempt_id != claim.attempt_id
        or proof.get("attemptId") != claim.attempt_id
        or (expected_attempt_id is not None and expected_attempt_id != claim.attempt_id)
    ):
        raise StopProofError("attempt_mismatch", "Stop attempt does not match")
    if op.generation != claim.generation or proof.get("generation") != claim.generation:
        raise StopProofError("generation_mismatch", "Stop generation does not match")
    if (
        proof.get("writableScope") != claim.writable_scope
        or proof.get("writesQuiesced") is not True
        or proof.get("writableScope") != op.scope
    ):
        raise StopProofError("writes_not_quiesced", "Stop scope is not confirmed")
    try:
        checked = datetime.fromisoformat(proof["checkedAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StopProofError("stop_evidence_mismatch", "Invalid stop timestamp") from exc
    now = datetime.now(UTC)
    if (
        checked.tzinfo is None
        or checked != claim.checked_at
        or checked > now
        or now - checked > timedelta(seconds=60)
    ):
        raise StopProofError("stop_evidence_mismatch", "Stop check is stale or mismatched")
    payload = {key: value for key, value in proof.items() if key != "evidenceSha256"}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if digest != proof.get("evidenceSha256") or digest != claim.evidence_sha256:
        raise StopProofError("stop_evidence_mismatch", "Stop digest does not match Runner ledger")
    try:
        assert_workspace_quiescent(session, workspace_id, except_operation=op.id)
    except WorkspaceBusyError as exc:
        raise StopProofError("workspace_still_writable", str(exc)) from exc
    operations = session.scalars(
        select(RunnerOperationRow).where(RunnerOperationRow.workspace_id == workspace_id)
    ).all()
    if any(
        other.workspace_id == workspace_id
        and other.id != op.id
        and other.updated_at is not None
        and other.updated_at > checked
        for other in operations
    ):
        raise StopProofError(
            "stop_evidence_mismatch", "Workspace executed again after stop verification"
        )


def assert_stage_write_open(session: Session, workspace_id: str) -> None:
    from agentforge.storage.models import InstanceAttemptRow, Run, StageRow

    attempts = session.scalars(
        select(InstanceAttemptRow).where(InstanceAttemptRow.workspace_id == workspace_id)
    ).all()
    for attempt in attempts:
        instance = session.get(AgentInstanceRow, attempt.instance_id)
        if instance is None or instance.current_attempt_id != attempt.id:
            continue
        run = session.get(Run, attempt.run_id, with_for_update=True, populate_existing=True)
        stage = (
            session.get(StageRow, attempt.stage_id, with_for_update=True, populate_existing=True)
            if attempt.stage_id
            else None
        )
        if (
            run is None
            or attempt.generation != run.active_generation
            or stage is None
            or stage.plan_version != run.plan_version
            or stage.write_access_closed
            or stage.status not in {"pending", "ready", "executing", "stopping", "reviewing"}
        ):
            raise WorkspaceBusyError("Stage is not admitting development writes")
        from agentforge.storage.models import StageSubmissionRow

        submissions = session.scalars(
            select(StageSubmissionRow).where(
                StageSubmissionRow.stage_id == stage.id,
                StageSubmissionRow.generation == run.active_generation,
            )
        ).all()
        if any(attempt.id in sub.attempt_refs for sub in submissions):
            raise WorkspaceBusyError("Submitted attempt may no longer write")
