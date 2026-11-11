"""Controlled Runner service: issue ticket → validate → execute profile (D05)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from agentforge.contracts.runner import (
    RunnerExecuteRequest,
    RunnerTicket,
    RunnerTicketIssueRequest,
)
from agentforge.permissions.service import (
    ApprovalError,
    assert_action_executable,
    try_consume_once,
)
from agentforge.runner.paths import RunnerPathError, resolve_workspace_root
from agentforge.runner.profiles import execute_profile, get_profile
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
    if row.status in {"issued", "running"} and row.expires_at <= _now():
        row.status = "expired"
        row.message = "Ticket expired"
        row.updated_at = _now()
        return True
    return False


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
            params_json=dict(body.params),
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

    def execute(self, body: RunnerExecuteRequest) -> RunnerTicket:
        row = self.session.get(RunnerOperationRow, body.operation_id)
        if row is None:
            raise RunnerError("operation_not_found", "Runner operation not found")
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
            row.status = "needs_reconciliation"
            row.message = "Execute re-entered while running; reconcile"
            row.updated_at = _now()
            self.session.flush()
            raise RunnerError(
                "needs_reconciliation",
                "Prior execution may have started; refusing blind retry",
            )

        if row.require_effective_approval:
            if not row.approval_id:
                raise RunnerError("approval_required", "Effective approval_id required")
            approval = self.session.get(ApprovalMappingRow, row.approval_id)
            if approval is None:
                raise RunnerError(
                    "approval_not_effective",
                    "D04 grant is not native-effective for this operation",
                )
            try:
                # Runner digest ≠ approval digest; bind on attempt/instance when present.
                assert_action_executable(
                    approval,
                    action_digest=None,
                    attempt_id=row.attempt_id,
                )
            except ApprovalError as exc:
                raise RunnerError("approval_not_effective", exc.message) from exc
            if not try_consume_once(self.session, approval):
                raise RunnerError(
                    "approval_consumed",
                    "Once-scoped grant already consumed",
                )

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

        row.status = "running"
        row.process_ref = f"host:{row.id}"
        row.updated_at = _now()
        self.session.flush()

        try:
            root = resolve_workspace_root(row.workspace_root)
            result = execute_profile(
                row.profile_id,
                workspace_root=root,
                params=dict(row.params_json or {}),
            )
        except RunnerPathError as exc:
            row.status = "failed"
            row.result_json = {"status": "failed", "code": exc.code, "detail": exc.message}
            row.message = f"{exc.code}: {exc.message}"
            row.updated_at = _now()
            self.session.flush()
            return _to_ticket(row)
        except Exception as exc:  # noqa: BLE001 — unknown host failure → reconcile
            row.status = "needs_reconciliation"
            row.result_json = {"status": "unknown", "detail": type(exc).__name__}
            row.message = "Unexpected failure after start; reconcile"
            row.updated_at = _now()
            self.session.flush()
            raise RunnerError("needs_reconciliation", str(exc)) from exc

        status = str(result.get("status") or "failed")
        if status == "succeeded":
            row.status = "succeeded"
        elif status == "blocked":
            row.status = "blocked"
        else:
            row.status = "failed"
        row.result_json = result
        row.message = f"profile={row.profile_id} status={row.status}"
        row.updated_at = _now()
        self.session.flush()
        return _to_ticket(row)

    def record_stop_evidence(self, operation_id: str, *, reason: str | None = None) -> RunnerTicket:
        """Run close_task-style evidence write for an existing succeeded/failed op workspace."""
        row = self.session.get(RunnerOperationRow, operation_id)
        if row is None:
            raise RunnerError("operation_not_found", "Runner operation not found")
        try:
            root = resolve_workspace_root(row.workspace_root)
            evidence = execute_profile(
                "close_task",
                workspace_root=root,
                params={"reason": reason or f"stop:{row.status}", "evidencePath": "_agentforge/stop_evidence.json"},
            )
        except RunnerPathError as exc:
            raise RunnerError(exc.code, exc.message) from exc
        meta = dict(row.result_json or {})
        meta["stopEvidence"] = evidence
        row.result_json = meta
        row.updated_at = _now()
        self.session.flush()
        return _to_ticket(row)
