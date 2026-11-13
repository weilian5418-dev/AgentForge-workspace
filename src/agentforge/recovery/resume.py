"""Trusted resume coordinator — never silently creates a new native session."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.recovery import (
    HandoffRequiredResult,
    ResumeCoordinatorResult,
    ResumeUnsupportedResult,
)
from agentforge.recovery.errors import RecoveryError
from agentforge.recovery.normalize import normalize_resume_result
from agentforge.storage.models import (
    AgentInstanceRow,
    InstanceAttemptRow,
    InstanceOperationRow,
    NativeSessionRow,
    Outbox,
    Run,
)
from agentforge.storage.uow import db_now


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


def prepare_resume_operation(
    session: Session,
    *,
    instance_id: str,
    attempt_id: str | None = None,
    idempotency_key: str,
    actor: str = "recovery",
) -> InstanceOperationRow:
    """Phase 1: durable resume intent. Does not call the adapter."""
    instance = session.get(AgentInstanceRow, instance_id, with_for_update=True)
    if instance is None:
        raise RecoveryError("instance_not_found", instance_id)
    run = session.get(Run, instance.run_id, with_for_update=True)
    if run is None:
        raise RecoveryError("run_not_found", instance.run_id)
    if run.status in {"canceled", "cancel_requested"}:
        raise RecoveryError("execution_blocked", "run cancel refuses resume")

    if not instance.session_ref or not instance.native_session_id:
        raise RecoveryError("resume_unsupported", "instance has no active session binding")

    ns = session.get(NativeSessionRow, instance.native_session_id)
    if ns is None or ns.binding_status != "active":
        raise RecoveryError("resume_unsupported", "native session not active")

    if attempt_id:
        attempt = session.get(InstanceAttemptRow, attempt_id)
        if attempt is None or attempt.instance_id != instance_id:
            raise RecoveryError("attempt_not_found", attempt_id or "")
        if attempt.status == "unknown":
            raise RecoveryError(
                "pending_side_effects",
                "cannot resume while attempt status is unknown",
            )

    existing = session.scalar(
        select(InstanceOperationRow).where(
            InstanceOperationRow.instance_id == instance_id,
            InstanceOperationRow.kind == "resume",
            InstanceOperationRow.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing

    op = InstanceOperationRow(
        id=_id("iop"),
        instance_id=instance_id,
        kind="resume",
        status="prepared",
        idempotency_key=idempotency_key,
        attempt_id=attempt_id or instance.current_attempt_id,
        payload_json={
            "sessionRef": instance.session_ref,
            "nativeSessionId": instance.native_session_id,
            "generation": instance.generation,
            "workspaceId": instance.workspace_id,
            "actor": actor,
        },
        prepared_at=_now(session),
    )
    session.add(op)
    session.flush()
    return op


def classify_resume_result(
    raw: dict[str, Any],
    *,
    expected_session_ref: str,
    expected_native_session_id: str,
) -> ResumeCoordinatorResult:
    """Normalize adapter resume payload; never accept silent session creation as resume."""
    if raw.get("created") or raw.get("createdSession") or raw.get("newSession"):
        return HandoffRequiredResult(
            kind="handoff_required",
            reason_codes=["handoff_confirmation_required"],
            detail="adapter created a session; resume_existing forbidden",
        )
    if (raw.get("support") or raw.get("status")) == "unsupported":
        return ResumeUnsupportedResult(kind="unsupported", detail=raw.get("detail"))
    return normalize_resume_result(
        raw,
        expected_session_ref=expected_session_ref,
        expected_native_session_id=expected_native_session_id,
    )


def resume_existing_session(
    session: Session,
    *,
    instance_id: str,
    attempt_id: str | None = None,
    idempotency_key: str,
    actor: str = "recovery",
) -> dict[str, Any]:
    """Prepare resume operation + outbox — worker performs native I/O."""
    instance = session.get(AgentInstanceRow, instance_id)
    if instance is None:
        raise RecoveryError("instance_not_found", instance_id)
    op = prepare_resume_operation(
        session,
        instance_id=instance_id,
        attempt_id=attempt_id,
        idempotency_key=idempotency_key,
        actor=actor,
    )
    session.add(
        Outbox(
            id=_id("obx"),
            run_id=instance.run_id,
            event_id=None,
            intent_type="instance.resume",
            target="instance",
            payload={"operationId": op.id, "instanceId": instance_id, "taskVersion": 1},
            idempotency_key=f"instance.resume:{op.id}",
            delivery_status="pending",
            attempt_count=0,
            created_at=_now(session),
            updated_at=_now(session),
        )
    )
    return {"status": "accepted", "operationId": op.id, "kind": "resume"}
