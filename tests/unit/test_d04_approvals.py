"""D04 approval ledger: digest, expiry, bridge, no fake native grant."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta  # timedelta used in happy-path expiry window
from typing import Any
from unittest.mock import patch

import pytest

from agentforge.contracts.plans import NativeApprovalRecordRequest
from agentforge.permissions.bridge import map_user_decision
from agentforge.permissions.digest import compute_action_digest
from agentforge.permissions.service import (
    ApprovalError,
    confirm_native,
    decide,
    is_effective_grant,
    record_native_request,
    retry_forward,
)
from agentforge.storage.models import ApprovalMappingRow


class _MemSession:
    def __init__(self) -> None:
        self.store: dict[tuple[type, str], Any] = {}
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)
        ident = getattr(row, "id", None)
        if ident is not None:
            self.store[(type(row), str(ident))] = row

    def flush(self) -> None:
        return None

    def get(self, model: type, key: str) -> Any | None:
        return self.store.get((model, key))

    def scalar(self, stmt: Any) -> Any | None:
        text = str(stmt).lower()
        if "native_request_id" in text or "approval_mappings" in text:
            # Return existing by scanning if any row matches last added native id
            for row in self.added:
                if isinstance(row, ApprovalMappingRow) and row.native_request_id:
                    # Tests that need conflict set up store explicitly via get path
                    return None
        return None


def _body(**overrides: Any) -> NativeApprovalRecordRequest:
    base = {
        "action": "run_command",
        "targetSummary": "workspace tmp",
        "parameterSummary": "cmd=echo",
        "requestedScope": "repo:demo/src/",
        "platformScope": "repo:demo/",
        "nativeScope": "repo:demo/src/",
        "productId": "cursor",
        "profileId": "aprof_1",
        "sessionRef": "sess_1",
        "nativeRequestId": "native_1",
        "adapterId": "cursor_agent",
        "operationType": "tool.invoke",
        "semantic": "once",
        "expiresAt": datetime.now(UTC) + timedelta(hours=1),
    }
    base.update(overrides)
    return NativeApprovalRecordRequest.model_validate(base)


def test_digest_stable() -> None:
    a = compute_action_digest(
        action="run_command",
        target_summary="t",
        parameter_summary="p",
        requested_scope="s",
        session_ref="sess",
        native_request_id="n1",
        attempt_id=None,
        semantic="once",
    )
    b = compute_action_digest(
        action="run_command",
        target_summary="t",
        parameter_summary="p",
        requested_scope="s",
        session_ref="sess",
        native_request_id="n1",
        attempt_id=None,
        semantic="once",
    )
    assert a == b
    assert len(a) == 64


def test_bridge_refuses_scope_expansion() -> None:
    result = map_user_decision(
        user_decision="approve",
        semantic="once",
        native_scope="repo:other/",
        platform_scope="repo:demo/",
    )
    assert result.outcome == "manual_required"


def test_record_and_decide_mapping_unsupported_without_product_ack() -> None:
    session = _MemSession()
    summary = record_native_request(session, _body())  # type: ignore[arg-type]
    assert summary.status == "pending"
    assert summary.action_digest

    decided = decide(
        session,  # type: ignore[arg-type]
        approval_id=summary.id,
        decision="approve",
        action_digest=summary.action_digest,
        forward=True,
    )
    # Real adapters return unsupported → ledger must not fake confirmed
    assert decided.status == "mapping_unsupported"
    assert decided.native_confirmed is False
    row = session.get(ApprovalMappingRow, summary.id)
    assert row is not None
    assert is_effective_grant(row) is False


def test_wrong_digest_rejected() -> None:
    session = _MemSession()
    summary = record_native_request(session, _body())  # type: ignore[arg-type]
    with pytest.raises(ApprovalError) as exc:
        decide(
            session,  # type: ignore[arg-type]
            approval_id=summary.id,
            decision="approve",
            action_digest="0" * 64,
            forward=False,
        )
    assert exc.value.code == "action_digest_mismatch"


def test_expired_blocks_late_decision() -> None:
    session = _MemSession()
    summary = record_native_request(
        session,  # type: ignore[arg-type]
        _body(expiresAt=datetime.now(UTC) + timedelta(hours=1)),
    )
    row = session.get(ApprovalMappingRow, summary.id)
    assert row is not None
    # Fixed clock: force expiry without relying on a 1ms race window.
    row.expires_at = datetime(2020, 1, 1, tzinfo=UTC)
    with pytest.raises(ApprovalError) as exc:
        decide(
            session,  # type: ignore[arg-type]
            approval_id=summary.id,
            decision="approve",
            action_digest=summary.action_digest,
            forward=False,
        )
    assert exc.value.code == "expired"


def test_confirm_approve_is_effective_with_mock_adapter() -> None:
    class _OkAdapter:
        def respond_approval(self, *, session_ref: str, native_request_id: str, decision: str):
            assert decision == "allow"
            return {
                "status": "supported",
                "confirmationRef": f"ack:{native_request_id}:{session_ref}",
            }

    session = _MemSession()
    summary = record_native_request(session, _body())  # type: ignore[arg-type]
    with patch("agentforge.permissions.service.get_adapter", return_value=_OkAdapter()):
        decided = decide(
            session,  # type: ignore[arg-type]
            approval_id=summary.id,
            decision="approve",
            action_digest=summary.action_digest,
            forward=True,
        )
    assert decided.status == "confirmed"
    assert decided.native_confirmed is True
    row = session.get(ApprovalMappingRow, summary.id)
    assert is_effective_grant(row) is True  # type: ignore[arg-type]


def test_reject_confirmed_is_not_effective_grant() -> None:
    class _OkAdapter:
        def respond_approval(self, *, session_ref: str, native_request_id: str, decision: str):
            assert decision == "deny"
            return {"status": "supported", "confirmationRef": "ack-deny"}

    session = _MemSession()
    summary = record_native_request(session, _body())  # type: ignore[arg-type]
    with patch("agentforge.permissions.service.get_adapter", return_value=_OkAdapter()):
        decided = decide(
            session,  # type: ignore[arg-type]
            approval_id=summary.id,
            decision="reject",
            action_digest=summary.action_digest,
            forward=True,
        )
    assert decided.status == "confirmed"
    assert decided.native_confirmed is False
    row = session.get(ApprovalMappingRow, summary.id)
    assert is_effective_grant(row) is False  # type: ignore[arg-type]


def test_delivery_unknown_retry_same_decision_only() -> None:
    class _Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def respond_approval(self, *, session_ref: str, native_request_id: str, decision: str):
            self.calls += 1
            if self.calls == 1:
                return {"status": "unknown", "detail": "timeout"}
            return {"status": "supported", "confirmationRef": "ack-late"}

    session = _MemSession()
    summary = record_native_request(session, _body())  # type: ignore[arg-type]
    flaky = _Flaky()
    with patch("agentforge.permissions.service.get_adapter", return_value=flaky):
        first = decide(
            session,  # type: ignore[arg-type]
            approval_id=summary.id,
            decision="approve",
            action_digest=summary.action_digest,
            forward=True,
        )
        assert first.status == "delivery_unknown"
        second = retry_forward(session, approval_id=summary.id)  # type: ignore[arg-type]
    assert second.status == "confirmed"
    assert second.native_confirmed is True


def test_native_id_session_conflict() -> None:
    session = _MemSession()
    first = record_native_request(session, _body())  # type: ignore[arg-type]
    # Force scalar to return the existing row for conflict path
    existing = session.get(ApprovalMappingRow, first.id)

    def _scalar(_stmt: Any) -> Any:
        return existing

    session.scalar = _scalar  # type: ignore[method-assign]
    with pytest.raises(ApprovalError) as exc:
        record_native_request(
            session,  # type: ignore[arg-type]
            _body(sessionRef="sess_other", nativeRequestId="native_1"),
        )
    assert exc.value.code == "native_id_session_conflict"


def test_confirm_mismatch_native_id() -> None:
    session = _MemSession()
    summary = record_native_request(session, _body())  # type: ignore[arg-type]
    decide(
        session,  # type: ignore[arg-type]
        approval_id=summary.id,
        decision="approve",
        action_digest=summary.action_digest,
        forward=False,
    )
    with pytest.raises(ApprovalError) as exc:
        confirm_native(
            session,  # type: ignore[arg-type]
            approval_id=summary.id,
            native_request_id="wrong",
            confirmation_ref="ack",
            source="adapter_host",
        )
    assert exc.value.code == "native_request_mismatch"


def test_public_confirm_cannot_forge_native_grant() -> None:
    session = _MemSession()
    summary = record_native_request(session, _body())  # type: ignore[arg-type]
    decided = decide(
        session,  # type: ignore[arg-type]
        approval_id=summary.id,
        decision="approve",
        action_digest=summary.action_digest,
        forward=True,
    )
    assert decided.status == "mapping_unsupported"
    with pytest.raises(ApprovalError) as exc:
        confirm_native(
            session,  # type: ignore[arg-type]
            approval_id=summary.id,
            native_request_id="native_1",
            confirmation_ref="user_typed_fake_ack",
            source="public",
        )
    assert exc.value.code in {"confirm_not_allowed", "trusted_confirm_required"}
    row = session.get(ApprovalMappingRow, summary.id)
    assert row is not None
    assert is_effective_grant(row) is False
