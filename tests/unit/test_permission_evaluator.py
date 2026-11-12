"""PermissionEvaluator: allow/deny/ask/defer — Bridge stays mapping-only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from agentforge.permissions.action import ActionDescriptor, action_descriptor_digest
from agentforge.permissions.evaluator import EvaluationRequest, evaluate
from agentforge.storage.models import ApprovalMappingRow


def _descriptor(**overrides: Any) -> ActionDescriptor:
    base = {
        "operation_type": "runner:write_file",
        "params": {"path": "src/a.py"},
        "workspace_id": "ws_1",
        "workspace_root": "/tmp/ws_1",
        "scope": "file:write:src/",
        "attempt_id": "att_1",
        "generation": 1,
        "session_ref": "sess_1",
    }
    base.update(overrides)
    return ActionDescriptor(**base).normalized()


def _approval(
    descriptor: ActionDescriptor,
    *,
    status: str = "confirmed",
    user_decision: str | None = "approve",
    native_confirmed: bool = True,
    platform_scope: str = "file:write:src/",
    extra_meta: dict[str, Any] | None = None,
) -> ApprovalMappingRow:
    now = datetime.now(UTC)
    meta = {
        "platformScope": platform_scope,
        "nativeScope": descriptor.scope,
        "sessionRef": descriptor.session_ref,
        "semantic": "once",
        "forwardState": "confirmed",
    }
    if extra_meta:
        meta.update(extra_meta)
    return ApprovalMappingRow(
        id="appr_1",
        run_id=None,
        instance_id=descriptor.instance_id,
        role_id=None,
        stage_id=None,
        attempt_id=descriptor.attempt_id,
        action_digest=action_descriptor_digest(descriptor),
        action="write_file",
        native_request_id="native_1",
        status=status,
        user_decision=user_decision,
        native_confirmed=native_confirmed,
        mapping_json=meta,
        expires_at=now + timedelta(hours=1),
        created_at=now,
        updated_at=now,
    )


def test_allow_when_confirmed_digest_matches() -> None:
    desc = _descriptor()
    result = evaluate(EvaluationRequest(descriptor=desc, mode="execute", approval=_approval(desc)))
    assert result.verdict == "allow"
    assert result.permitted


def test_deny_when_read_grant_used_for_write() -> None:
    desc = _descriptor(scope="file:write:src/a.py")
    appr = _approval(desc, platform_scope="file:read:src/")
    # Recompute digest for write scope; approval still carries write digest but platform is read.
    result = evaluate(EvaluationRequest(descriptor=desc, approval=appr))
    assert result.verdict == "deny"
    assert result.code == "scope_exceeds_platform"


def test_ask_when_pending_user_decision() -> None:
    desc = _descriptor()
    appr = _approval(desc, status="pending", user_decision=None, native_confirmed=False)
    result = evaluate(EvaluationRequest(descriptor=desc, mode="preflight", approval=appr))
    assert result.verdict == "ask"
    assert result.code == "awaiting_user"


def test_ask_preflight_without_approval() -> None:
    desc = _descriptor()
    result = evaluate(EvaluationRequest(descriptor=desc, mode="preflight", approval=None))
    assert result.verdict == "ask"
    assert result.code == "approval_required"


def test_deny_execute_without_approval() -> None:
    desc = _descriptor()
    result = evaluate(EvaluationRequest(descriptor=desc, mode="execute", approval=None))
    assert result.verdict == "deny"
    assert result.code == "approval_required"


def test_defer_while_native_forward_pending() -> None:
    desc = _descriptor()
    appr = _approval(
        desc,
        status="forwarding",
        native_confirmed=False,
        extra_meta={"forwardState": "forwarding"},
    )
    result = evaluate(EvaluationRequest(descriptor=desc, mode="execute", approval=appr))
    assert result.verdict == "defer"
    assert result.code == "awaiting_native"


def test_deny_digest_mismatch_same_attempt() -> None:
    approved = _descriptor(params={"path": "src/a.py"})
    live = _descriptor(params={"path": "src/b.py"})
    appr = _approval(approved)
    result = evaluate(EvaluationRequest(descriptor=live, approval=appr))
    assert result.verdict == "deny"
    assert result.code == "action_digest_mismatch"


def test_deny_role_scope_miss() -> None:
    desc = _descriptor(scope="file:write:secrets/")
    appr = _approval(desc, platform_scope="file:write:secrets/")
    result = evaluate(
        EvaluationRequest(
            descriptor=desc,
            approval=appr,
            role_allowed_scopes=("file:write:src/",),
        )
    )
    assert result.verdict == "deny"
    assert result.code == "scope_outside_role"


def test_deny_rejected() -> None:
    desc = _descriptor()
    appr = _approval(desc, status="rejected", user_decision="reject", native_confirmed=False)
    result = evaluate(EvaluationRequest(descriptor=desc, approval=appr))
    assert result.verdict == "deny"
    assert result.code == "user_rejected"
