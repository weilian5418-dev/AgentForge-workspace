"""D04 scope mapping negatives — must not expand or default-allow."""

from __future__ import annotations

import pytest

from agentforge.permissions.bridge import args_digest_for, map_user_decision, scope_fits
from agentforge.permissions.service import (
    ApprovalError,
    assert_action_executable,
    grant_record_looks_valid,
    try_consume_once,
)
from agentforge.storage.models import ApprovalMappingRow
from datetime import UTC, datetime, timedelta


def test_path_prefix_does_not_cover_sibling_name() -> None:
    assert scope_fits(native_scope="repo:demo/src-old", platform_scope="repo:demo/src") is False
    assert scope_fits(native_scope="repo:demo/src/a.py", platform_scope="repo:demo/src") is True


def test_read_does_not_imply_write() -> None:
    assert (
        scope_fits(
            native_scope="file:write:/ws/a",
            platform_scope="file:read:/ws/a",
        )
        is False
    )
    assert (
        scope_fits(
            native_scope="file:read:/ws/a/b",
            platform_scope="file:read:/ws/a",
        )
        is True
    )


def test_command_args_change_rejected() -> None:
    d1 = args_digest_for(["echo", "a"])
    d2 = args_digest_for(["echo", "b"])
    assert scope_fits(native_scope=f"cmd:echo:{d2}", platform_scope=f"cmd:echo:{d1}") is False
    assert scope_fits(native_scope=f"cmd:echo:{d1}", platform_scope=f"cmd:echo:{d1}") is True


def test_missing_scope_refuses_default_allow() -> None:
    result = map_user_decision(
        user_decision="approve",
        semantic="once",
        native_scope="",
        platform_scope="repo:demo/",
    )
    assert result.outcome == "mapping_unsupported"


def test_once_consume_only_once() -> None:
    class _S:
        def __init__(self, row: ApprovalMappingRow) -> None:
            self.row = row

        def get(self, _model: type, _key: str) -> ApprovalMappingRow:
            return self.row

        def flush(self) -> None:
            return None

    now = datetime.now(UTC)
    row = ApprovalMappingRow(
        id="appr_once",
        run_id=None,
        instance_id=None,
        role_id=None,
        stage_id=None,
        attempt_id="att_1",
        action_digest="a" * 64,
        action="run",
        native_request_id="n1",
        status="confirmed",
        user_decision="approve",
        native_confirmed=True,
        mapping_json={"semantic": "once", "sessionRef": "s1"},
        expires_at=now + timedelta(hours=1),
        created_at=now,
        updated_at=now,
    )
    session = _S(row)
    assert grant_record_looks_valid(row) is True
    assert try_consume_once(session, row) is True  # type: ignore[arg-type]
    assert row.status == "consumed"
    assert try_consume_once(session, row) is False  # type: ignore[arg-type]


def test_assert_action_rejects_digest_and_session_mismatch() -> None:
    now = datetime.now(UTC)
    row = ApprovalMappingRow(
        id="appr_x",
        run_id=None,
        instance_id="inst_1",
        role_id=None,
        stage_id=None,
        attempt_id="att_1",
        action_digest="b" * 64,
        action="run",
        native_request_id="n1",
        status="confirmed",
        user_decision="approve",
        native_confirmed=True,
        mapping_json={"semantic": "stage", "sessionRef": "s1"},
        expires_at=now + timedelta(hours=1),
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(ApprovalError) as exc:
        assert_action_executable(row, action_digest="c" * 64)
    assert exc.value.code == "action_digest_mismatch"
    with pytest.raises(ApprovalError) as exc2:
        assert_action_executable(row, session_ref="other")
    assert exc2.value.code == "session_mismatch"
