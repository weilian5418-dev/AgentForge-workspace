"""Unified action authorization ledger + native forward/confirm (D04)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.adapters.registry import get_adapter
from agentforge.contracts.plans import (
    ApprovalSummary,
    NativeApprovalMapping,
    NativeApprovalRecordRequest,
)
from agentforge.permissions.bridge import map_user_decision
from agentforge.permissions.digest import compute_action_digest
from agentforge.storage.models import ApprovalMappingRow


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


def is_effective_grant(row: ApprovalMappingRow, *, at: datetime | None = None) -> bool:
    """Native-effective only when confirmed approve, unexpired, and object still bound."""
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
    return True


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

    digest = compute_action_digest(
        action=body.action,
        target_summary=body.target_summary,
        parameter_summary=body.parameter_summary,
        requested_scope=body.requested_scope,
        session_ref=body.session_ref,
        native_request_id=body.native_request_id,
        attempt_id=body.attempt_id,
        semantic=body.semantic,
    )
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
        return _forward(session, row)
    return _to_summary(row)


def _forward(session: Session, row: ApprovalMappingRow) -> ApprovalSummary:
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
        adapter = get_adapter(adapter_id)
    except KeyError as exc:
        raise ApprovalError("adapter_not_found", str(exc)) from exc

    row.status = "forwarding"
    meta["forwardState"] = "forwarding"
    row.mapping_json = meta
    row.updated_at = _now()
    session.flush()

    result = adapter.respond_approval(
        session_ref=str(meta.get("sessionRef") or ""),
        native_request_id=str(row.native_request_id or ""),
        decision=str(bridge.native_decision),
    )
    status = str(result.get("status") or "unknown")
    if status == "unsupported":
        row.status = "mapping_unsupported"
        meta["forwardState"] = "mapping_unsupported"
        meta["adapterDetail"] = result.get("detail")
        row.mapping_json = meta
        row.updated_at = _now()
        session.flush()
        return _to_summary(row)
    if status == "failed":
        # Failed forward: do not invent confirmation; leave delivery_unknown for reconcile.
        row.status = "delivery_unknown"
        meta["forwardState"] = "delivery_unknown"
        meta["adapterDetail"] = result.get("detail")
        row.mapping_json = meta
        row.updated_at = _now()
        session.flush()
        return _to_summary(row)
    if status == "unknown":
        row.status = "delivery_unknown"
        meta["forwardState"] = "delivery_unknown"
        meta["adapterDetail"] = result.get("detail")
        row.mapping_json = meta
        row.updated_at = _now()
        session.flush()
        return _to_summary(row)
    if status == "supported":
        return confirm_native(
            session,
            approval_id=row.id,
            native_request_id=str(row.native_request_id or ""),
            confirmation_ref=str(result.get("confirmationRef") or result.get("detail") or "ack"),
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
    row.status = "confirmed"
    # native_confirmed only for approve path (effective grant signal)
    row.native_confirmed = row.user_decision == "approve"
    row.updated_at = _now()
    session.flush()
    return _to_summary(row)


def retry_forward(session: Session, *, approval_id: str) -> ApprovalSummary:
    """Retry forward only when prior delivery is unknown — never expand authorization."""
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
    return _forward(session, row)
