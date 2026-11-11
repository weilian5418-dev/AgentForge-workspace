"""PermissionEvaluator — platform allow/deny/ask/defer before Bridge mapping.

Bridge only maps platform decisions onto product-native options.
Evaluator owns whether the final action is permitted under platform rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from agentforge.permissions.action import ActionDescriptor, action_descriptor_digest
from agentforge.permissions.bridge import parse_scope, scope_fits
from agentforge.storage.models import ApprovalMappingRow

PermissionVerdict = Literal["allow", "deny", "ask", "defer"]
EvaluationMode = Literal["preflight", "execute"]


@dataclass(frozen=True)
class EvaluationResult:
    verdict: PermissionVerdict
    code: str
    detail: str

    @property
    def permitted(self) -> bool:
        return self.verdict == "allow"


@dataclass(frozen=True)
class EvaluationRequest:
    """Inputs for a single permission judgment (final action, not a vague intent)."""

    descriptor: ActionDescriptor
    mode: EvaluationMode = "execute"
    platform_scope: str | None = None
    approval: ApprovalMappingRow | None = None
    role_allowed_scopes: tuple[str, ...] = ()
    stage_id: str | None = None
    at: datetime | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _meta(row: ApprovalMappingRow) -> dict[str, Any]:
    raw = row.mapping_json or {}
    return dict(raw) if isinstance(raw, dict) else {}


def evaluate(request: EvaluationRequest) -> EvaluationResult:
    """Judge the final ActionDescriptor against platform rules and current grant state.

    Never defaults to allow. Incomplete or unknown state → ask (needs user) or defer
    (waiting on native/ledger), never silent permission.
    """
    descriptor = request.descriptor.normalized()
    if not descriptor.operation_type.strip():
        return EvaluationResult("deny", "missing_operation", "operationType is required")
    if not descriptor.scope.strip():
        return EvaluationResult("deny", "missing_scope", "Action scope is required")
    if not descriptor.workspace_id.strip():
        return EvaluationResult("deny", "missing_workspace", "workspaceId is required")

    parsed = parse_scope(descriptor.scope)
    if parsed is None:
        return EvaluationResult("deny", "unparseable_scope", "Action scope could not be parsed")

    platform_scope = request.platform_scope
    if platform_scope is None and request.approval is not None:
        platform_scope = str(_meta(request.approval).get("platformScope") or "") or None

    if platform_scope:
        if not scope_fits(native_scope=descriptor.scope, platform_scope=platform_scope):
            return EvaluationResult(
                "deny",
                "scope_exceeds_platform",
                "Action scope exceeds platform-granted scope",
            )

    if request.role_allowed_scopes:
        if not any(
            scope_fits(native_scope=descriptor.scope, platform_scope=allowed)
            for allowed in request.role_allowed_scopes
        ):
            return EvaluationResult(
                "deny",
                "scope_outside_role",
                "Action scope is outside role-allowed scopes",
            )

    if request.stage_id and request.approval is not None:
        if request.approval.stage_id and request.approval.stage_id != request.stage_id:
            return EvaluationResult(
                "deny",
                "stage_mismatch",
                "Approval stageId does not match current stage",
            )

    approval = request.approval
    if approval is None:
        if request.mode == "preflight":
            return EvaluationResult(
                "ask",
                "approval_required",
                "No authorization record; user decision required",
            )
        return EvaluationResult(
            "deny",
            "approval_required",
            "Execute refused without an authorization record",
        )

    when = request.at or _now()
    meta = _meta(approval)

    if meta.get("revokedAt"):
        return EvaluationResult("deny", "grant_revoked", "Authorization was revoked")

    if approval.expires_at <= when:
        return EvaluationResult("deny", "grant_expired", "Authorization expired")

    if approval.user_decision == "reject" or approval.status == "rejected":
        return EvaluationResult("deny", "user_rejected", "User rejected the action")

    if approval.status in {"mapping_unsupported", "manual_required"}:
        return EvaluationResult(
            "deny",
            approval.status,
            "Native mapping cannot safely express the platform decision",
        )

    if approval.status == "pending" or approval.user_decision is None:
        return EvaluationResult(
            "ask",
            "awaiting_user",
            "Platform decision not yet recorded",
        )

    if approval.user_decision == "approve" and approval.status in {
        "user_decided",
        "forwarding",
        "delivery_unknown",
    }:
        return EvaluationResult(
            "defer",
            "awaiting_native",
            f"Waiting on native confirmation (status={approval.status})",
        )

    forward_state = str(meta.get("forwardState") or "")
    if approval.user_decision == "approve" and not approval.native_confirmed:
        if forward_state in {"forwarding", "delivery_unknown", "queued", "not_started"}:
            return EvaluationResult(
                "defer",
                "awaiting_native",
                f"Native forward incomplete (forwardState={forward_state or 'unknown'})",
            )
        if request.mode == "preflight":
            return EvaluationResult(
                "defer",
                "awaiting_native",
                "Approve recorded but native confirmation missing",
            )
        return EvaluationResult(
            "deny",
            "native_unconfirmed",
            "Execute refused without native confirmation",
        )

    if approval.status == "consumed":
        # Same-operation idempotency is handled by try_consume_once; evaluator only
        # sees a consumed row as allow when caller already bound the operation.
        consumed_by = meta.get("consumedByOperationId")
        if not consumed_by:
            return EvaluationResult("deny", "grant_consumed", "Once-grant already consumed")
        # Caller must still pass digest checks below; treat as allow candidate.
        pass
    elif approval.status not in {"confirmed", "consumed"}:
        return EvaluationResult(
            "defer",
            "indeterminate_status",
            f"Authorization status={approval.status} is not executable",
        )

    if not approval.native_confirmed and approval.status != "consumed":
        return EvaluationResult(
            "deny",
            "native_unconfirmed",
            "Native confirmation required before execute",
        )

    live_digest = action_descriptor_digest(descriptor)
    if approval.action_digest != live_digest:
        return EvaluationResult(
            "deny",
            "action_digest_mismatch",
            "Final action does not match approved ActionDescriptor",
        )

    if descriptor.attempt_id and approval.attempt_id and approval.attempt_id != descriptor.attempt_id:
        return EvaluationResult(
            "deny",
            "attempt_mismatch",
            "Grant attemptId does not match current attempt",
        )
    if descriptor.instance_id and approval.instance_id and approval.instance_id != descriptor.instance_id:
        return EvaluationResult(
            "deny",
            "instance_mismatch",
            "Grant instanceId does not match current instance",
        )
    session_ref = descriptor.session_ref
    if session_ref is not None and str(meta.get("sessionRef") or "") != session_ref:
        return EvaluationResult(
            "deny",
            "session_mismatch",
            "Grant sessionRef does not match current session",
        )

    return EvaluationResult("allow", "permitted", "Action permitted under current authorization")
