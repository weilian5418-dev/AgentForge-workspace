"""RunControlService — pause / resume / cancel / replan request + coordination (D11.10)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.runs import OperationAccepted, RunActionRequest
from agentforge.driver.signals import record_wake_signal
from agentforge.recovery.service import RecoveryService
from agentforge.storage.event_log import append_fact
from agentforge.storage.models import (
    AgentInstanceRow,
    AsyncOperationRow,
    InstanceAttemptRow,
    InstanceOperationRow,
    Outbox,
    Run,
)
from agentforge.storage.uow import db_now

ControlAction = Literal["pause", "resume", "cancel", "replan"]


def _now(session: Session | None = None) -> datetime:
    if session is not None:
        try:
            value = db_now(session)
            if getattr(value, "tzinfo", None) is None:
                return value.replace(tzinfo=UTC)
            return value
        except Exception:
            pass
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def run_admits_new_development(run: Run) -> bool:
    """New workspace/instance/task dispatch — false under pause/cancel control."""
    if run.cancel_requested_at is not None:
        return False
    return run.status in {"preparing", "running"}


class RunControlError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RunControlService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def request_action(
        self,
        run_id: str,
        body: RunActionRequest,
        *,
        actor: str = "user:operator",
    ) -> OperationAccepted:
        """Synchronous accept only — does not claim native pause/stop verified."""
        run = self.session.get(Run, run_id, with_for_update=True, populate_existing=True)
        if run is None:
            raise RunControlError("run_not_found", run_id)

        existing = None
        for row in self.session.scalars(
            select(AsyncOperationRow).where(
                AsyncOperationRow.run_id == run_id,
                AsyncOperationRow.operation_type == f"run.{body.action}",
            )
        ).all():
            if (row.payload or {}).get("idempotencyKey") == body.idempotency_key:
                existing = row
                break
        if existing is not None:
            prior_action = (existing.payload or {}).get("action")
            prior_reason = (existing.payload or {}).get("reason")
            if (
                prior_action != body.action
                or prior_reason != body.reason
                or (existing.payload or {}).get("expectedStatusVersion")
                != body.expected_status_version
            ):
                raise RunControlError(
                    "idempotency_conflict", "control key reused with different body"
                )
            return OperationAccepted(
                operation_id=existing.id,
                status="accepted" if existing.status == "accepted" else existing.status,  # type: ignore[arg-type]
                run_id=run_id,
            )

        expected = int(body.expected_status_version)
        if expected != int(run.status_version):
            raise RunControlError("status_version_conflict", "expectedStatusVersion mismatch")

        op_id = _id("aop")
        if body.action == "pause":
            if run.status in {"paused", "pause_requested"}:
                pass
            elif run.status not in {"preparing", "running"}:
                raise RunControlError("illegal_status", f"cannot pause from {run.status}")
            else:
                run.status = "pause_requested"
                run.status_version = int(run.status_version) + 1
                run.updated_at = _now(self.session)
            event_type = "run.pause_requested"
        elif body.action == "cancel":
            if run.status in {"canceled"}:
                pass
            elif run.status == "cancel_requested":
                pass
            else:
                run.status = "cancel_requested"
                run.cancel_requested_at = _now(self.session)
                run.status_version = int(run.status_version) + 1
                run.updated_at = _now(self.session)
            event_type = "run.cancel_requested"
        elif body.action == "resume":
            if run.status not in {"paused", "blocked", "pause_requested"}:
                raise RunControlError("illegal_status", f"cannot resume from {run.status}")
            # Keep paused/blocked facts; open recovery case for verification.
            event_type = "run.resume_requested"
        elif body.action == "replan":
            if run.status not in {"paused", "blocked", "cancel_requested", "running", "preparing"}:
                raise RunControlError("illegal_status", f"cannot replan from {run.status}")
            event_type = None
        else:
            raise RunControlError("unsupported_action", body.action)

        op = AsyncOperationRow(
            id=op_id,
            operation_type=f"run.{body.action}",
            status="accepted",
            run_id=run_id,
            message=f"{body.action} accepted; awaiting coordination",
            payload={
                "action": body.action,
                "idempotencyKey": body.idempotency_key,
                "reason": body.reason,
                "actor": actor,
                "statusVersionAtAccept": run.status_version,
                "expectedStatusVersion": body.expected_status_version,
            },
            created_at=_now(self.session),
            updated_at=_now(self.session),
        )
        self.session.add(op)

        if body.action == "resume":
            case = RecoveryService(self.session).open_case(
                run_id,
                reason=f"resume:{body.reason or 'user'}",
                request_operation_id=op_id,
                emit_outbox=True,
            )
            op.payload = {**op.payload, "recoveryCaseId": case.case_id}
            op.status = "running"
            op.message = "resume accepted; recovery probe required before running"

        if body.action == "replan":
            op.message = "replan accepted; require safe boundary then D08 revision"
            op.status = "accepted"

        if event_type is not None:
            append_fact(
                self.session,
                run_id,
                event_type,
                {
                    "operationId": op_id,
                    "action": body.action,
                    "statusVersion": run.status_version,
                    "reason": body.reason,
                },
            )
        record_wake_signal(
            self.session,
            run_id=run_id,
            source_kind="control",
            source_ref=f"{op_id}:{body.action}:{run.status_version}",
            binding={"operationId": op_id, "action": body.action},
            priority=20,
        )
        return OperationAccepted(operation_id=op_id, status="accepted", run_id=run_id)

    def coordinate_pause(self, run_id: str, *, operation_id: str | None = None) -> dict[str, Any]:
        """Request stops for writable instances; flip to paused only when quiescent."""
        run = self.session.get(Run, run_id, with_for_update=True, populate_existing=True)
        if run is None:
            raise RunControlError("run_not_found", run_id)
        if run.status not in {"pause_requested", "paused"}:
            return {"status": run.status, "paused": False, "note": "not_pause_requested"}

        stop_ops = self._ensure_stop_requests(run, reason="pause")
        pending = self._list_unstopped(run)
        unknown = self._list_unknown_side_effects(run)
        if pending or unknown:
            self._update_control_op(
                operation_id,
                status="needs_reconciliation" if unknown else "running",
                message="pause_requested; awaiting stop verification",
                extra={"pendingInstances": pending, "unknownOps": unknown, "stopOps": stop_ops},
            )
            return {
                "status": "pause_requested",
                "paused": False,
                "pendingInstances": pending,
                "unknownOps": unknown,
                "stopOperationIds": stop_ops,
            }

        run.status = "paused"
        run.status_version = int(run.status_version) + 1
        run.updated_at = _now(self.session)
        append_fact(
            self.session,
            run_id,
            "run.paused",
            {
                "operationId": operation_id or "coord",
                "action": "pause",
                "statusVersion": run.status_version,
                "reason": None,
            },
        )
        self._update_control_op(operation_id, status="succeeded", message="paused")
        return {"status": "paused", "paused": True, "stopOperationIds": stop_ops}

    def coordinate_cancel(self, run_id: str, *, operation_id: str | None = None) -> dict[str, Any]:
        """Cancel completion requires verified stops; else remain cancel_requested."""
        run = self.session.get(Run, run_id, with_for_update=True, populate_existing=True)
        if run is None:
            raise RunControlError("run_not_found", run_id)
        if run.status not in {"cancel_requested", "canceled"}:
            return {"status": run.status, "canceled": False, "note": "not_cancel_requested"}
        if run.status == "canceled":
            return {"status": "canceled", "canceled": True}

        # Revoke prepared (not yet possibly-native) create/deliver intents.
        revoked = self._revoke_unstarted_intents(run)
        stop_ops = self._ensure_stop_requests(run, reason="cancel")
        pending = self._list_unstopped(run)
        unknown = self._list_unknown_side_effects(run)
        if pending or unknown:
            self._update_control_op(
                operation_id,
                status="needs_reconciliation" if unknown else "running",
                message="cancel_requested; remote may still run",
                extra={
                    "pendingInstances": pending,
                    "unknownOps": unknown,
                    "revoked": revoked,
                    "stopOps": stop_ops,
                },
            )
            return {
                "status": "cancel_requested",
                "canceled": False,
                "pendingInstances": pending,
                "unknownOps": unknown,
                "stopOperationIds": stop_ops,
                "revoked": revoked,
            }

        run.status = "canceled"
        run.status_version = int(run.status_version) + 1
        run.updated_at = _now(self.session)
        append_fact(
            self.session,
            run_id,
            "run.canceled",
            {
                "operationId": operation_id or "coord",
                "action": "cancel",
                "statusVersion": run.status_version,
                "reason": None,
            },
        )
        self._update_control_op(operation_id, status="succeeded", message="canceled")
        return {"status": "canceled", "canceled": True, "stopOperationIds": stop_ops}

    def apply_verified_resume(
        self,
        run_id: str,
        *,
        operation_id: str | None = None,
        recovery_case_id: str | None = None,
    ) -> dict[str, Any]:
        """Only after recovery evidence allows development again — never invent stop."""
        run = self.session.get(Run, run_id, with_for_update=True, populate_existing=True)
        if run is None:
            raise RunControlError("run_not_found", run_id)
        if run.status not in {"paused", "blocked", "pause_requested"}:
            raise RunControlError("illegal_status", f"cannot apply resume from {run.status}")

        unknown = self._list_unknown_side_effects(run)
        if unknown:
            raise RunControlError("pending_side_effects", "unknown ops block resume")

        from agentforge.storage.models import RecoveryCaseRow

        if not recovery_case_id:
            raise RunControlError(
                "recovery_evidence_stale", "A completed recovery case is required"
            )
        case = self.session.get(RecoveryCaseRow, recovery_case_id)
        if (
            case is None
            or case.run_id != run_id
            or case.status != "applied"
            or case.proposed_action
            not in {
                "observe_existing",
                "continue_stage_processing",
                "resume_existing",
                "handoff_new_session",
            }
        ):
            raise RunControlError("recovery_evidence_stale", "Recovery action is not complete")
        binding = case.context_json or {}
        if (case.plan_version, case.assignment_version, binding.get("activeGeneration")) != (
            run.plan_version,
            run.assignment_version,
            run.active_generation,
        ):
            raise RunControlError("recovery_evidence_stale", "Recovery binding changed")
        run.status = "running"
        run.status_version = int(run.status_version) + 1
        run.updated_at = _now(self.session)
        append_fact(
            self.session,
            run.id,
            "run.status_changed",
            {
                "status": "running",
                "statusVersion": run.status_version,
                "reason": "verified recovery",
            },
        )
        self._update_control_op(operation_id, status="succeeded", message="running")
        return {"status": "running", "statusVersion": run.status_version}

    def _ensure_stop_requests(self, run: Run, *, reason: str) -> list[str]:
        op_ids: list[str] = []
        instances = list(
            self.session.scalars(
                select(AgentInstanceRow).where(AgentInstanceRow.run_id == run.id)
            ).all()
        )
        for inst in instances:
            if inst.lifecycle in {"stopped", "closed", "failed", "created"}:
                continue
            if not inst.session_ref:
                continue
            idem = f"control-stop:{run.id}:{inst.id}:{run.status_version}:{reason}"
            existing = self.session.scalar(
                select(InstanceOperationRow).where(
                    InstanceOperationRow.instance_id == inst.id,
                    InstanceOperationRow.kind == "request_stop",
                    InstanceOperationRow.idempotency_key == idem,
                )
            )
            if existing is not None:
                op_ids.append(existing.id)
                continue
            op_id = _id("iop")
            self.session.add(
                InstanceOperationRow(
                    id=op_id,
                    instance_id=inst.id,
                    kind="request_stop",
                    status="prepared",
                    idempotency_key=idem,
                    attempt_id=inst.current_attempt_id,
                    payload_json={
                        "reason": reason,
                        "sessionRef": inst.session_ref,
                        "nativeSessionId": inst.native_session_id,
                        "generation": inst.generation,
                        "actor": "run_control",
                    },
                    prepared_at=_now(self.session),
                )
            )
            self.session.add(
                Outbox(
                    id=_id("obx"),
                    run_id=run.id,
                    intent_type="instance.request_stop",
                    target="instance",
                    payload={"operationId": op_id, "instanceId": inst.id},
                    idempotency_key=f"instance-op:{op_id}",
                    delivery_status="pending",
                    attempt_count=0,
                    created_at=_now(self.session),
                    updated_at=_now(self.session),
                )
            )
            if inst.lifecycle == "active":
                inst.lifecycle = "stop_requested"
                inst.updated_at = _now(self.session)
            op_ids.append(op_id)
        return op_ids

    def _list_unstopped(self, run: Run) -> list[dict[str, str]]:
        pending: list[dict[str, str]] = []
        for inst in self.session.scalars(
            select(AgentInstanceRow).where(AgentInstanceRow.run_id == run.id)
        ).all():
            if inst.lifecycle in {"stopped", "closed", "failed", "created"}:
                continue
            if inst.session_ref is None and inst.lifecycle in {"created", "starting"}:
                continue
            pending.append(
                {
                    "instanceId": inst.id,
                    "lifecycle": inst.lifecycle,
                    "status": inst.status,
                    "sessionRef": inst.session_ref or "",
                }
            )
        return pending

    def _list_unknown_side_effects(self, run: Run) -> list[dict[str, str]]:
        unknown: list[dict[str, str]] = []
        instance_ids = [
            i.id
            for i in self.session.scalars(
                select(AgentInstanceRow).where(AgentInstanceRow.run_id == run.id)
            ).all()
        ]
        if instance_ids:
            for op in self.session.scalars(
                select(InstanceOperationRow).where(
                    InstanceOperationRow.instance_id.in_(instance_ids),
                    InstanceOperationRow.status == "unknown",
                )
            ).all():
                unknown.append(
                    {
                        "kind": "instance_operation",
                        "operationId": op.id,
                        "instanceId": op.instance_id,
                        "opKind": op.kind,
                    }
                )
        for attempt in self.session.scalars(
            select(InstanceAttemptRow).where(
                InstanceAttemptRow.run_id == run.id,
                InstanceAttemptRow.status == "unknown",
            )
        ).all():
            unknown.append(
                {
                    "kind": "instance_attempt",
                    "operationId": attempt.id,
                    "instanceId": attempt.instance_id,
                    "opKind": "attempt",
                }
            )
        return unknown

    def _revoke_unstarted_intents(self, run: Run) -> list[str]:
        """Cancel prepared ops that have not started native dispatch."""
        revoked: list[str] = []
        instance_ids = [
            i.id
            for i in self.session.scalars(
                select(AgentInstanceRow).where(AgentInstanceRow.run_id == run.id)
            ).all()
        ]
        if not instance_ids:
            return revoked
        for op in self.session.scalars(
            select(InstanceOperationRow).where(
                InstanceOperationRow.instance_id.in_(instance_ids),
                InstanceOperationRow.status == "prepared",
                InstanceOperationRow.kind.in_(["create_session", "resume"]),
            )
        ).all():
            if (op.payload_json or {}).get("dispatchStartedAt"):
                continue
            op.status = "failed"
            op.error_code = "revoked_by_cancel"
            op.completed_at = _now(self.session)
            revoked.append(op.id)
        return revoked

    def _update_control_op(
        self,
        operation_id: str | None,
        *,
        status: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if not operation_id:
            return
        op = self.session.get(AsyncOperationRow, operation_id)
        if op is None:
            return
        op.status = status
        op.message = message
        op.uncertain_side_effects = status == "needs_reconciliation"
        if extra:
            op.payload = {**(op.payload or {}), **extra}
        op.updated_at = _now(self.session)
