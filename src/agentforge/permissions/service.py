"""Unified action authorization ledger + native forward/confirm (D04)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

import json

from sqlalchemy import text

from agentforge.adapters.registry import get_adapter
from agentforge.contracts.plans import (
    ApprovalSummary,
    NativeApprovalMapping,
    NativeApprovalRecordRequest,
)
from agentforge.permissions.action import ActionDescriptor, action_descriptor_digest
from agentforge.permissions.bridge import map_user_decision
from agentforge.permissions.digest import compute_action_digest
from agentforge.permissions.evaluator import EvaluationRequest, evaluate
from agentforge.queue.constants import INTENT_APPROVAL_FORWARD
from agentforge.storage.models import ApprovalMappingRow, Outbox


class ApprovalError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


def _mapping_payload(row: ApprovalMappingRow) -> dict[str, Any]:
    raw = row.mapping_json or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _to_summary(row: ApprovalMappingRow) -> ApprovalSummary:
    meta = _mapping_payload(row)
    mapping = None
    if meta.get("productId") and meta.get("profileId") and meta.get("sessionRef"):
        mapping = NativeApprovalMapping(
            product_id=str(meta["productId"]),
            profile_id=str(meta["profileId"]),
            session_ref=str(meta["sessionRef"]),
            native_scope=str(meta.get("nativeScope") or meta.get("requestedScope") or "unknown"),
            platform_scope=str(meta.get("platformScope") or "unknown"),
            mapping_version=str(meta.get("mappingVersion") or "1"),
            decision_id=str(meta["decisionId"]) if meta.get("decisionId") else None,
            forward_state=meta.get("forwardState") or "not_started",  # type: ignore[arg-type]
            native_confirmation_ref=(
                str(meta["nativeConfirmationRef"]) if meta.get("nativeConfirmationRef") else None
            ),
        )
    return ApprovalSummary(
        id=row.id,
        run_id=row.run_id,
        instance_id=row.instance_id,
        role_id=row.role_id,
        stage_id=row.stage_id,
        attempt_id=row.attempt_id,
        action=row.action,
        target_summary=str(meta.get("targetSummary") or ""),
        parameter_summary=str(meta.get("parameterSummary") or ""),
        action_digest=row.action_digest,
        requested_scope=str(meta["requestedScope"]) if meta.get("requestedScope") else None,
        native_request_id=row.native_request_id,
        mapping=mapping,
        expires_at=row.expires_at,
        status=row.status,  # type: ignore[arg-type]
        user_decision=row.user_decision,  # type: ignore[arg-type]
        native_confirmed=bool(row.native_confirmed),
    )


def grant_record_looks_valid(row: ApprovalMappingRow, *, at: datetime | None = None) -> bool:
    """Record-level validity only. Not proof the current action may run."""
    when = at or _now()
    if row.status != "confirmed":
        return False
    if row.user_decision != "approve":
        return False
    if not row.native_confirmed:
        return False
    if row.expires_at <= when:
        return False
    if not row.native_request_id:
        return False
    meta = _mapping_payload(row)
    if meta.get("revokedAt"):
        return False
    if meta.get("consumedAt") and str(meta.get("semantic") or "once") == "once":
        return False
    return True


def is_effective_grant(row: ApprovalMappingRow, *, at: datetime | None = None) -> bool:
    """Backward-compatible alias for record-level validity (not current-action gate)."""
    return grant_record_looks_valid(row, at=at)


def assert_action_executable(
    row: ApprovalMappingRow,
    *,
    action_digest: str | None = None,
    session_ref: str | None = None,
    instance_id: str | None = None,
    attempt_id: str | None = None,
    stage_id: str | None = None,
    at: datetime | None = None,
    descriptor: ActionDescriptor | None = None,
) -> None:
    """Current-action gate used at execution time (stricter than record validity).

    Prefer passing a typed ``descriptor`` so PermissionEvaluator owns the judgment.
    Legacy digest/identity kwargs remain for call sites that have not migrated yet.
    """
    if descriptor is not None:
        result = evaluate(
            EvaluationRequest(
                descriptor=descriptor,
                mode="execute",
                approval=row,
                stage_id=stage_id,
                at=at,
            )
        )
        if not result.permitted:
            raise ApprovalError(result.code, result.detail)
        return

    if not grant_record_looks_valid(row, at=at):
        raise ApprovalError("grant_not_effective", "Authorization record is not effective")
    if action_digest is not None and row.action_digest != action_digest:
        raise ApprovalError("action_digest_mismatch", "Action digest changed; grant not reusable")
    meta = _mapping_payload(row)
    if session_ref is not None and str(meta.get("sessionRef") or "") != session_ref:
        raise ApprovalError("session_mismatch", "Grant sessionRef does not match current session")
    if instance_id is not None and row.instance_id and row.instance_id != instance_id:
        raise ApprovalError("instance_mismatch", "Grant instanceId does not match")
    if attempt_id is not None and row.attempt_id and row.attempt_id != attempt_id:
        raise ApprovalError("attempt_mismatch", "Grant attemptId does not match")
    if stage_id is not None and row.stage_id and row.stage_id != stage_id:
        raise ApprovalError("stage_mismatch", "Grant stageId does not match")


def try_consume_once(
    session: Session,
    row: ApprovalMappingRow,
    *,
    operation_id: str,
) -> bool:
    """Atomically consume a once-scoped grant bound to ``operation_id``.

    Same operationId retries are idempotent (return True without double-granting).
    Different operationIds cannot share one once-grant. Uses conditional UPDATE on
    PostgreSQL; falls back to version-checked in-memory update for unit sessions.
    """
    if not operation_id:
        raise ApprovalError("operation_id_required", "once-consume requires operationId")
    meta = _mapping_payload(row)
    semantic = str(meta.get("semantic") or "once")
    if semantic != "once":
        return True
    existing_op = meta.get("consumedByOperationId")
    if meta.get("consumedAt") and existing_op == operation_id:
        return True
    if meta.get("consumedAt") and existing_op and existing_op != operation_id:
        return False

    consumed_at = _now().isoformat()
    patch = {
        "consumedAt": consumed_at,
        "consumedByOperationId": operation_id,
    }
    bind = {"id": row.id, "patch": json.dumps(patch)}
    # Prefer atomic JSONB conditional update when talking to real Postgres.
    try:
        result = session.execute(
            text(
                """
                UPDATE app.approval_mappings
                SET
                  mapping_json = COALESCE(mapping_json, '{}'::jsonb) || CAST(:patch AS jsonb),
                  status = 'consumed',
                  updated_at = now()
                WHERE id = :id
                  AND status = 'confirmed'
                  AND user_decision = 'approve'
                  AND native_confirmed = true
                  AND (
                    mapping_json->>'consumedAt' IS NULL
                    OR mapping_json->>'consumedByOperationId' = :opid
                  )
                """
            ),
            {**bind, "opid": operation_id},
        )
        if result.rowcount:  # type: ignore[attr-defined]
            session.refresh(row)
            return True
        # Re-read for idempotent same-operation case after lost race.
        fresh = session.get(ApprovalMappingRow, row.id)
        if fresh is None:
            return False
        fresh_meta = _mapping_payload(fresh)
        if fresh_meta.get("consumedByOperationId") == operation_id:
            row.mapping_json = fresh_meta
            row.status = fresh.status
            return True
        return False
    except Exception:
        # MemSession / non-PG: optimistic single-threaded update with version token.
        fresh = session.get(ApprovalMappingRow, row.id)
        if fresh is None:
            return False
        fresh_meta = _mapping_payload(fresh)
        if fresh_meta.get("consumedByOperationId") == operation_id:
            return True
        if fresh_meta.get("consumedAt"):
            return False
        if not grant_record_looks_valid(fresh):
            return False
        version = int(fresh_meta.get("grantVersion") or 1)
        fresh_meta.update(patch)
        fresh_meta["grantVersion"] = version + 1
        fresh.mapping_json = fresh_meta
        fresh.status = "consumed"
        fresh.updated_at = _now()
        session.flush()
        row.mapping_json = fresh_meta
        row.status = "consumed"
        row.updated_at = fresh.updated_at
        return True


def revoke_grant(session: Session, *, approval_id: str, reason: str | None = None) -> ApprovalSummary:
    row = session.get(ApprovalMappingRow, approval_id)
    if row is None:
        raise ApprovalError("approval_not_found", "Approval not found")
    meta = _mapping_payload(row)
    meta["revokedAt"] = _now().isoformat()
    if reason:
        meta["revokeReason"] = reason
    row.mapping_json = meta
    row.native_confirmed = False
    if row.status == "confirmed":
        row.status = "rejected"
    row.updated_at = _now()
    session.flush()
    return _to_summary(row)


def _expire_if_needed(row: ApprovalMappingRow, *, at: datetime | None = None) -> bool:
    when = at or _now()
    if row.status in {"pending", "user_decided", "forwarding", "delivery_unknown"} and row.expires_at <= when:
        row.status = "expired"
        row.updated_at = when
        return True
    return False


def record_native_request(
    session: Session,
    body: NativeApprovalRecordRequest,
) -> ApprovalSummary:
    """Accept a product-native approval request into the platform ledger."""
    # Same nativeRequestId must not bind across different sessions.
    existing = session.scalar(
        select(ApprovalMappingRow).where(
            ApprovalMappingRow.native_request_id == body.native_request_id
        )
    )
    if existing is not None:
        meta = _mapping_payload(existing)
        if meta.get("sessionRef") != body.session_ref:
            raise ApprovalError(
                "native_id_session_conflict",
                "nativeRequestId already bound to a different session",
            )
        return _to_summary(existing)

    descriptor = ActionDescriptor(
        operation_type=body.operation_type,
        params=body.params
        if body.params is not None
        else {
            "action": body.action,
            "target": body.target_summary,
            "parameters": body.parameter_summary,
        },
        workspace_id=body.workspace_id or body.requested_scope,
        workspace_root=body.workspace_root or "",
        scope=body.requested_scope,
        environment_id=body.environment_id,
        attempt_id=body.attempt_id,
        generation=body.generation,
        instance_id=body.instance_id,
        session_ref=body.session_ref,
    )
    digest = action_descriptor_digest(descriptor)
    if body.action_digest is not None and body.action_digest != digest:
        raise ApprovalError("action_digest_mismatch", "Client actionDigest does not match canonical digest")

    if body.expires_at <= _now():
        raise ApprovalError("already_expired", "expiresAt is not in the future")

    mapping_json: dict[str, Any] = {
        "actor": body.actor,
        "operationType": body.operation_type,
        "targetSummary": body.target_summary,
        "parameterSummary": body.parameter_summary,
        "requestedScope": body.requested_scope,
        "platformScope": body.platform_scope,
        "nativeScope": body.native_scope,
        "productId": body.product_id,
        "profileId": body.profile_id,
        "sessionRef": body.session_ref,
        "adapterId": body.adapter_id,
        "mappingVersion": "1",
        "semantic": body.semantic,
        "forwardState": "not_started",
        "nativeOptions": list(body.native_options),
        "actionDescriptor": {
            "operationType": descriptor.operation_type,
            "params": descriptor.params,
            "workspaceId": descriptor.workspace_id,
            "workspaceRoot": descriptor.normalized().workspace_root,
            "scope": descriptor.scope,
            "environmentId": descriptor.environment_id,
            "attemptId": descriptor.attempt_id,
            "generation": descriptor.generation,
            "instanceId": descriptor.instance_id,
            "sessionRef": descriptor.session_ref,
        },
        "grantVersion": 1,
    }
    row = ApprovalMappingRow(
        id=f"appr_{uuid.uuid4().hex[:16]}",
        run_id=body.run_id,
        instance_id=body.instance_id,
        role_id=body.role_id,
        stage_id=body.stage_id,
        attempt_id=body.attempt_id,
        action_digest=digest,
        action=body.action,
        native_request_id=body.native_request_id,
        status="pending",
        user_decision=None,
        native_confirmed=False,
        mapping_json=mapping_json,
        expires_at=body.expires_at,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    session.flush()
    return _to_summary(row)


def list_approvals(
    session: Session,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[ApprovalSummary]:
    stmt = select(ApprovalMappingRow).order_by(ApprovalMappingRow.created_at.desc()).limit(limit)
    rows = list(session.scalars(stmt).all())
    out: list[ApprovalSummary] = []
    for row in rows:
        _expire_if_needed(row)
        if status is not None and row.status != status:
            continue
        out.append(_to_summary(row))
    return out[:limit]


def get_approval(session: Session, approval_id: str) -> ApprovalSummary:
    row = session.get(ApprovalMappingRow, approval_id)
    if row is None:
        raise ApprovalError("approval_not_found", "Approval not found")
    _expire_if_needed(row)
    session.flush()
    return _to_summary(row)


def decide(
    session: Session,
    *,
    approval_id: str,
    decision: Literal["approve", "reject"],
    action_digest: str,
    note: str | None = None,
    forward: bool = True,
) -> ApprovalSummary:
    """Record user decision; optionally forward to native via adapter."""
    row = session.get(ApprovalMappingRow, approval_id)
    if row is None:
        raise ApprovalError("approval_not_found", "Approval not found")
    _expire_if_needed(row)
    if row.status == "expired":
        raise ApprovalError("expired", "Approval expired; late decisions cannot unlock new attempts")
    if row.action_digest != action_digest:
        raise ApprovalError("action_digest_mismatch", "actionDigest does not match ledger")

    if row.status in {"confirmed", "rejected", "mapping_unsupported", "manual_required", "consumed"}:
        # Idempotent only when same decision already recorded and terminal.
        if row.user_decision == decision and row.status == "confirmed":
            return _to_summary(row)
        raise ApprovalError("already_terminal", f"Approval already terminal: {row.status}")

    if row.status == "pending":
        row.user_decision = decision
        row.status = "user_decided"
        meta = _mapping_payload(row)
        meta["decisionId"] = f"dec_{uuid.uuid4().hex[:12]}"
        if note:
            meta["decisionNote"] = note
        meta["forwardState"] = "not_started"
        row.mapping_json = meta
        row.updated_at = _now()
        session.flush()
    elif row.status in {"user_decided", "forwarding", "delivery_unknown"}:
        if row.user_decision != decision:
            raise ApprovalError("decision_conflict", "Cannot change a recorded user decision")
    else:
        raise ApprovalError("invalid_state", f"Cannot decide from status={row.status}")

    if forward:
        return _queue_forward_intent(session, row)
    return _to_summary(row)


def _queue_forward_intent(session: Session, row: ApprovalMappingRow) -> ApprovalSummary:
    """Short-txn: bridge + persist forward intent / outbox. Does NOT call native adapter."""
    meta = _mapping_payload(row)
    semantic = str(meta.get("semantic") or "once")
    bridge = map_user_decision(
        user_decision=row.user_decision,  # type: ignore[arg-type]
        semantic=semantic,
        native_scope=str(meta.get("nativeScope") or ""),
        platform_scope=str(meta.get("platformScope") or ""),
        native_options=list(meta.get("nativeOptions") or []),
    )
    if bridge.outcome == "mapping_unsupported":
        row.status = "mapping_unsupported"
        meta["forwardState"] = "mapping_unsupported"
        meta["bridgeDetail"] = bridge.detail
        row.mapping_json = meta
        row.updated_at = _now()
        session.flush()
        return _to_summary(row)
    if bridge.outcome == "manual_required":
        row.status = "manual_required"
        meta["forwardState"] = "mapping_unsupported"
        meta["bridgeDetail"] = bridge.detail
        row.mapping_json = meta
        row.updated_at = _now()
        session.flush()
        return _to_summary(row)

    adapter_id = str(meta.get("adapterId") or "")
    try:
        get_adapter(adapter_id)
    except KeyError as exc:
        raise ApprovalError("adapter_not_found", str(exc)) from exc

    meta["bridgeMappedOptions"] = bridge.mapped_options
    meta["bridgeNativeDecision"] = bridge.native_decision
    meta["forwardIntentAt"] = _now().isoformat()
    meta["forwardPending"] = True
    meta["forwardState"] = "forwarding"
    row.status = "forwarding"
    row.mapping_json = meta
    row.updated_at = _now()
    outbox_id = f"obx_{uuid.uuid4().hex[:20]}"
    meta["forwardOutboxId"] = outbox_id
    row.mapping_json = meta
    session.add(
        Outbox(
            id=outbox_id,
            run_id=row.run_id,
            event_id=None,
            intent_type=INTENT_APPROVAL_FORWARD,
            target="approval:forward",
            payload={"approvalId": row.id, "adapterId": adapter_id},
            idempotency_key=f"approval-forward:{row.id}:{meta.get('decisionId')}",
            delivery_status="pending",
            attempt_count=0,
        )
    )
    session.flush()
    return _to_summary(row)


def process_pending_forward(session: Session, approval_id: str) -> ApprovalSummary:
    """Post-commit: call native adapter and persist trusted result in a new short txn."""
    row = session.get(ApprovalMappingRow, approval_id)
    if row is None:
        raise ApprovalError("approval_not_found", "Approval not found")
    meta = _mapping_payload(row)
    if row.status in {"confirmed", "rejected", "mapping_unsupported", "manual_required", "consumed"}:
        return _to_summary(row)
    if row.status != "forwarding" or not meta.get("forwardPending"):
        raise ApprovalError("invalid_state", "No pending forward intent to process")

    semantic = str(meta.get("semantic") or "once")
    native_decision = str(meta.get("bridgeNativeDecision") or "")
    mapped_options = meta.get("bridgeMappedOptions")
    adapter_id = str(meta.get("adapterId") or "")
    try:
        adapter = get_adapter(adapter_id)
    except KeyError as exc:
        raise ApprovalError("adapter_not_found", str(exc)) from exc

    meta["forwardState"] = "forwarding"
    row.mapping_json = meta
    session.flush()

    result = adapter.respond_approval(
        session_ref=str(meta.get("sessionRef") or ""),
        native_request_id=str(row.native_request_id or ""),
        decision=native_decision,
        semantic=semantic,
        mapped_options=mapped_options if isinstance(mapped_options, dict) else None,
    )
    status = str(result.get("status") or "unknown")
    meta["forwardPending"] = False
    if status == "unsupported":
        row.status = "mapping_unsupported"
        meta["forwardState"] = "mapping_unsupported"
        meta["adapterDetail"] = result.get("detail")
        row.mapping_json = meta
        row.updated_at = _now()
        session.flush()
        return _to_summary(row)
    if status in {"failed", "unknown", "supported"}:
        row.status = "delivery_unknown"
        meta["forwardState"] = "delivery_unknown"
        meta["adapterDetail"] = result.get("detail") or (
            "supported≠acked; waiting for native confirmationRef"
            if status == "supported"
            else result.get("detail")
        )
        row.mapping_json = meta
        row.updated_at = _now()
        session.flush()
        return _to_summary(row)
    if status in {"acked", "confirmed"}:
        conf = result.get("confirmationRef")
        if not conf:
            row.status = "delivery_unknown"
            meta["forwardState"] = "delivery_unknown"
            meta["adapterDetail"] = "acked/confirmed without confirmationRef"
            row.mapping_json = meta
            row.updated_at = _now()
            session.flush()
            return _to_summary(row)
        row.mapping_json = meta
        session.flush()
        return confirm_native(
            session,
            approval_id=row.id,
            native_request_id=str(row.native_request_id or ""),
            confirmation_ref=str(conf),
            source="adapter_host",
        )
    row.status = "delivery_unknown"
    meta["forwardState"] = "delivery_unknown"
    meta["adapterDetail"] = f"unexpected adapter status={status}"
    row.mapping_json = meta
    row.updated_at = _now()
    session.flush()
    return _to_summary(row)


def confirm_native(
    session: Session,
    *,
    approval_id: str,
    native_request_id: str,
    confirmation_ref: str,
    source: str = "public",
) -> ApprovalSummary:
    """Mark native ack for the same request. Does not expand scope or unlock on reject.

    F06: public callers cannot mint ``nativeConfirmed``. Only a trusted adapter-host
    receipt may elevate ``forwarding`` / ``delivery_unknown`` to an effective grant.
    ``mapping_unsupported`` / ``manual_required`` stay non-effective.
    """
    row = session.get(ApprovalMappingRow, approval_id)
    if row is None:
        raise ApprovalError("approval_not_found", "Approval not found")
    _expire_if_needed(row)
    if row.status == "expired":
        raise ApprovalError("expired", "Cannot confirm an expired approval")
    if row.native_request_id != native_request_id:
        raise ApprovalError("native_request_mismatch", "nativeRequestId does not match ledger")
    if row.user_decision is None:
        raise ApprovalError("no_user_decision", "User decision required before native confirm")
    if row.status in {"pending"}:
        raise ApprovalError("invalid_state", "Cannot confirm before user decision")
    if row.status in {"mapping_unsupported", "manual_required"}:
        raise ApprovalError(
            "confirm_not_allowed",
            "Cannot elevate mapping_unsupported/manual_required via confirm string",
        )
    if source != "adapter_host":
        raise ApprovalError(
            "trusted_confirm_required",
            "Public confirm cannot mint nativeConfirmed; requires trusted adapter-host receipt",
        )
    if row.status not in {"user_decided", "forwarding", "delivery_unknown"}:
        raise ApprovalError("invalid_state", f"Cannot confirm from status={row.status}")

    meta = _mapping_payload(row)
    meta["nativeConfirmationRef"] = confirmation_ref
    meta["forwardState"] = "confirmed" if row.user_decision == "approve" else "rejected"
    meta["confirmSource"] = source
    row.mapping_json = meta
    if row.user_decision == "approve":
        row.status = "confirmed"
        row.native_confirmed = True
    else:
        # Rejection delivered — no execution rights.
        row.status = "rejected"
        row.native_confirmed = False
    row.updated_at = _now()
    session.flush()
    return _to_summary(row)


def retry_forward(
    session: Session,
    *,
    approval_id: str,
    reconcile_token: str | None = None,
) -> ApprovalSummary:
    """Retry forward only after reconcile proves unsent — unknown ≠ unsent."""
    row = session.get(ApprovalMappingRow, approval_id)
    if row is None:
        raise ApprovalError("approval_not_found", "Approval not found")
    _expire_if_needed(row)
    if row.status == "expired":
        raise ApprovalError("expired", "Approval expired")
    if row.status != "delivery_unknown":
        raise ApprovalError(
            "retry_not_allowed",
            "Only delivery_unknown may retry forward; unknown results must not replay expanded grants",
        )
    if row.user_decision is None:
        raise ApprovalError("no_user_decision", "Missing user decision")
    meta = _mapping_payload(row)
    # Require an explicit reconcile token (inspect/idempotent query result) before resend.
    expected = str(meta.get("reconcileToken") or "")
    if not reconcile_token or not expected or reconcile_token != expected:
        raise ApprovalError(
            "reconcile_required",
            "delivery_unknown cannot blind-resend; provide reconcileToken after native inspect",
        )
    meta["reconcileConsumedAt"] = _now().isoformat()
    # One-shot token
    meta.pop("reconcileToken", None)
    row.mapping_json = meta
    session.flush()
    return _queue_forward_intent(session, row)


def mark_forward_reconciled(
    session: Session,
    *,
    approval_id: str,
    unsent: bool,
    inspect_ref: str,
) -> ApprovalSummary:
    """Record native inspect outcome. Only unsent=True mints a one-shot reconcileToken."""
    row = session.get(ApprovalMappingRow, approval_id)
    if row is None:
        raise ApprovalError("approval_not_found", "Approval not found")
    if row.status != "delivery_unknown":
        raise ApprovalError("invalid_state", "Reconcile only applies to delivery_unknown")
    meta = _mapping_payload(row)
    meta["lastInspectRef"] = inspect_ref
    meta["lastInspectAt"] = _now().isoformat()
    if unsent:
        meta["reconcileToken"] = f"rc_{uuid.uuid4().hex[:16]}"
    else:
        meta.pop("reconcileToken", None)
        meta["reconcileBlockedReason"] = "native already has delivery; do not resend"
    row.mapping_json = meta
    row.updated_at = _now()
    session.flush()
    return _to_summary(row)
