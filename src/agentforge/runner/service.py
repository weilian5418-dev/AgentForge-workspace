"""Controlled Runner service: issue ticket → validate → execute profile (D05)."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from agentforge.contracts.runner import (
    RunnerExecuteRequest,
    RunnerTicket,
    RunnerTicketIssueRequest,
)
from agentforge.permissions.action import action_descriptor_digest, descriptor_from_runner
from agentforge.permissions.service import (
    ApprovalError,
    assert_action_executable,
    try_consume_once,
)
from agentforge.runner.paths import RunnerPathError, resolve_workspace_root
from agentforge.runner.profiles import get_profile
from agentforge.runner.quiescence import (
    WorkspaceBusyError,
    assert_workspace_quiescent,
    lock_workspace,
)
from agentforge.runner.tickets import compute_runner_digest
from agentforge.storage.models import ApprovalMappingRow, RunnerOperationRow


class RunnerError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


def _to_ticket(row: RunnerOperationRow) -> RunnerTicket:
    return RunnerTicket(
        operation_id=row.id,
        profile_id=row.profile_id,
        workspace_id=row.workspace_id,
        node_id=row.node_id,
        attempt_id=row.attempt_id,
        generation=row.generation,
        action_digest=row.action_digest,
        scope=row.scope,
        status=row.status,  # type: ignore[arg-type]
        expires_at=row.expires_at,
        approval_id=row.approval_id,
        require_effective_approval=bool(row.require_effective_approval),
        params=dict(row.params_json or {}),
        result=dict(row.result_json or {}),
        process_ref=row.process_ref,
        message=row.message,
    )


def _expire_if_needed(row: RunnerOperationRow) -> bool:
    if row.status == "issued" and row.expires_at <= _now():
        row.status = "expired"
        row.message = "Ticket expired"
        row.updated_at = _now()
        return True
    return False


@dataclass(frozen=True)
class RunnerDispatch:
    operation_id: str
    token: str
    profile_id: str
    workspace_root: str
    params: dict[str, Any]


class RunnerService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def issue(self, body: RunnerTicketIssueRequest) -> RunnerTicket:
        try:
            get_profile(body.profile_id)
        except KeyError as exc:
            raise RunnerError("unknown_profile", f"Unknown profileId: {body.profile_id}") from exc
        if body.expires_at <= _now():
            raise RunnerError("already_expired", "expiresAt must be in the future")
        if any(str(key).startswith("__") for key in body.params):
            raise RunnerError("invalid_params", "Reserved Runner parameter name")
        workspace = lock_workspace(self.session, body.workspace_id)
        if workspace is not None:
            if str(resolve_workspace_root(body.workspace_root)) != str(
                resolve_workspace_root(workspace.worktree_path)
            ):
                raise RunnerError(
                    "workspace_mismatch", "Ticket root differs from registered workspace"
                )
            if get_profile(body.profile_id).writes and workspace.status != "active":
                raise RunnerError("workspace_frozen", "Workspace is not open for writes")
        # Validate workspace exists up-front.
        try:
            resolve_workspace_root(body.workspace_root)
        except RunnerPathError as exc:
            raise RunnerError(exc.code, exc.message) from exc

        digest = compute_runner_digest(
            profile_id=body.profile_id,
            workspace_id=body.workspace_id,
            node_id=body.node_id,
            attempt_id=body.attempt_id,
            generation=body.generation,
            scope=body.scope,
            params=body.params,
        )
        execution_desc = descriptor_from_runner(
            profile_id=body.profile_id,
            workspace_id=body.workspace_id,
            workspace_root=body.workspace_root,
            scope=body.scope,
            params=dict(body.params),
            attempt_id=body.attempt_id,
            generation=body.generation,
            environment_id=body.environment_id,
            instance_id=body.instance_id,
            session_ref=body.session_ref,
        )
        execution_digest = action_descriptor_digest(execution_desc)
        if body.approval_id:
            approval = self.session.get(ApprovalMappingRow, body.approval_id)
            if approval is None:
                raise RunnerError("approval_not_found", "approvalId not found")
            if approval.action_digest != execution_digest:
                raise RunnerError(
                    "approval_action_mismatch",
                    "Approval ActionDescriptor digest does not match runner ticket action",
                )
        params = dict(body.params)
        params["__executionDigest"] = execution_digest
        params["__executionContext"] = {
            "environmentId": body.environment_id,
            "instanceId": body.instance_id,
            "sessionRef": body.session_ref,
        }
        row = RunnerOperationRow(
            id=f"rop_{uuid.uuid4().hex[:20]}",
            profile_id=body.profile_id,
            workspace_id=body.workspace_id,
            workspace_root=body.workspace_root,
            node_id=body.node_id,
            attempt_id=body.attempt_id,
            generation=body.generation,
            action_digest=digest,
            scope=body.scope,
            params_json=params,
            status="issued",
            result_json={},
            process_ref=None,
            approval_id=body.approval_id,
            require_effective_approval=body.require_effective_approval,
            message="Ticket issued",
            expires_at=body.expires_at,
            created_at=_now(),
            updated_at=_now(),
        )
        self.session.add(row)
        self.session.flush()
        return _to_ticket(row)

    def get(self, operation_id: str) -> RunnerTicket:
        row = self.session.get(RunnerOperationRow, operation_id)
        if row is None:
            raise RunnerError("operation_not_found", "Runner operation not found")
        if _expire_if_needed(row):
            self.session.flush()
        return _to_ticket(row)

    def claim_execution(self, body: RunnerExecuteRequest) -> RunnerDispatch | RunnerTicket:
        row = self.session.get(RunnerOperationRow, body.operation_id)
        if row is None:
            raise RunnerError("operation_not_found", "Runner operation not found")
        workspace = lock_workspace(self.session, row.workspace_id)
        row = self.session.get(
            RunnerOperationRow, body.operation_id, with_for_update=True, populate_existing=True
        )
        assert row is not None
        _expire_if_needed(row)
        if row.status == "expired":
            raise RunnerError("expired", "Ticket expired")
        if row.action_digest != body.action_digest:
            raise RunnerError(
                "action_digest_mismatch",
                "actionDigest does not match ticket (same id different params rejected)",
            )

        # Idempotent replay of terminal success/blocked/failed with same digest.
        if row.status in {"succeeded", "blocked", "failed"}:
            return _to_ticket(row)
        if row.status == "needs_reconciliation":
            raise RunnerError(
                "needs_reconciliation",
                "Prior execution uncertain; reconcile before retry",
            )
        if row.status == "running":
            # Crash window: do not blind-retry.
            raise RunnerError(
                "needs_reconciliation",
                "Prior execution may have started; refusing blind retry",
            )

        if (
            workspace is not None
            and get_profile(row.profile_id).writes
            and workspace.status != "active"
        ):
            raise RunnerError("workspace_frozen", "Workspace is not open for writes")
        spec = get_profile(row.profile_id)
        if spec.docker_required:
            row.status = "blocked"
            row.result_json = {
                "status": "blocked",
                "detail": "Docker required; host fallback refused",
            }
            row.message = "blocked: docker unevidenced"
            row.updated_at = _now()
            self.session.flush()
            return _to_ticket(row)

        # Feasibility first; only then bind+consume once-grant with this operationId.
        if row.require_effective_approval:
            if not row.approval_id:
                raise RunnerError("approval_required", "Effective approval_id required")
            approval = self.session.get(ApprovalMappingRow, row.approval_id)
            if approval is None:
                raise RunnerError(
                    "approval_not_effective",
                    "D04 grant is not native-effective for this operation",
                )
            params = dict(row.params_json or {})
            exec_digest = str(params.get("__executionDigest") or "")
            context = params.get("__executionContext") or {}
            descriptor = descriptor_from_runner(
                profile_id=row.profile_id,
                workspace_id=row.workspace_id,
                workspace_root=row.workspace_root,
                scope=row.scope,
                params={k: v for k, v in params.items() if not str(k).startswith("__")},
                attempt_id=row.attempt_id,
                generation=row.generation,
                environment_id=context.get("environmentId"),
                instance_id=context.get("instanceId"),
                session_ref=context.get("sessionRef"),
            )
            live = action_descriptor_digest(descriptor)
            if not exec_digest or exec_digest != live or approval.action_digest != live:
                raise RunnerError(
                    "approval_action_mismatch",
                    "Final runner action does not match approved ActionDescriptor",
                )
            try:
                assert_action_executable(
                    approval,
                    action_digest=live,
                    attempt_id=row.attempt_id,
                    descriptor=descriptor,
                )
            except ApprovalError as exc:
                raise RunnerError(exc.code, exc.message) from exc
            if not try_consume_once(self.session, approval, operation_id=row.id):
                raise RunnerError(
                    "approval_consumed",
                    "Once-scoped grant already consumed by another operation",
                )

        token = uuid.uuid4().hex
        row.status = "running"
        row.process_ref = f"host:{row.id}"
        row.result_json = {"dispatchToken": token, "dispatchStartedAt": _now().isoformat()}
        row.updated_at = _now()
        self.session.flush()
        return RunnerDispatch(
            row.id,
            token,
            row.profile_id,
            row.workspace_root,
            {k: v for k, v in dict(row.params_json or {}).items() if not str(k).startswith("__")},
        )

    def finish_execution(self, dispatch: RunnerDispatch, result: dict[str, Any]) -> RunnerTicket:
        row = self.session.get(
            RunnerOperationRow, dispatch.operation_id, with_for_update=True, populate_existing=True
        )
        if (
            row is None
            or row.status != "running"
            or (row.result_json or {}).get("dispatchToken") != dispatch.token
        ):
            raise RunnerError("stale_execution", "Result belongs to an inactive dispatch")
        status = str(result.get("status") or "unknown")
        row.status = (
            status if status in {"succeeded", "failed", "blocked"} else "needs_reconciliation"
        )
        row.result_json = {
            **result,
            "executionReceipt": {
                "dispatchToken": dispatch.token,
                "operationId": row.id,
                "profileId": row.profile_id,
                "workspaceId": row.workspace_id,
                "attemptId": row.attempt_id,
                "generation": row.generation,
                "finishedAt": _now().isoformat(),
                "runnerSynchronousOnly": row.status in {"succeeded", "failed"},
            },
        }
        row.message = f"profile={row.profile_id} status={row.status}"
        row.updated_at = _now()
        self.session.flush()
        return _to_ticket(row)

    def record_stop_evidence(self, operation_id: str, *, reason: str | None = None) -> RunnerTicket:
        """Persist an authoritative proof for known synchronous Runner work only.

        No native-process stop guarantee is inferred from a terminal job or marker file.
        """
        row = self.session.get(RunnerOperationRow, operation_id)
        if row is None:
            raise RunnerError("operation_not_found", "Runner operation not found")
        lock_workspace(self.session, row.workspace_id)
        row = self.session.get(
            RunnerOperationRow, operation_id, with_for_update=True, populate_existing=True
        )
        assert row is not None
        receipt = (row.result_json or {}).get("executionReceipt")
        if (
            row.status not in {"succeeded", "failed"}
            or not isinstance(receipt, dict)
            or not receipt.get("runnerSynchronousOnly")
            or receipt.get("operationId") != row.id
        ):
            raise RunnerError("stop_unconfirmed", "No trusted synchronous execution receipt")
        try:
            assert_workspace_quiescent(self.session, row.workspace_id, except_operation=row.id)
        except WorkspaceBusyError as exc:
            raise RunnerError("stop_unconfirmed", str(exc)) from exc
        if not row.attempt_id:
            raise RunnerError("stop_unconfirmed", "Stop proof requires an attempt binding")
        proof = {
            "source": "runner",
            "operationId": row.id,
            "workspaceId": row.workspace_id,
            "workspaceRoot": str(resolve_workspace_root(row.workspace_root)),
            "attemptId": row.attempt_id,
            "generation": row.generation,
            "writableScope": row.scope,
            "writesQuiesced": True,
            "checkedAt": _now().isoformat(),
            "receiptToken": receipt["dispatchToken"],
            "coverage": "runner_synchronous_only",
            "reason": reason,
        }
        digest = hashlib.sha256(
            json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        metadata = dict(row.result_json or {})
        metadata["stopEvidence"] = {**proof, "evidenceSha256": digest}
        row.result_json = metadata
        row.updated_at = _now()
        self.session.flush()
        return _to_ticket(row)
