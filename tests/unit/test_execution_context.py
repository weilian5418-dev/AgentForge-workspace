"""ExecutionContext binding and separated recovery facets."""

from __future__ import annotations

import pytest

from agentforge.adapter_host.host import AdapterHost
from agentforge.runtime.execution_context import (
    ExecutionContextError,
    bind_execution_context,
    recovery_all_ok,
    verify_recovery,
)


def test_bind_rejects_workspace_mismatch(tmp_path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    with pytest.raises(ExecutionContextError) as exc:
        bind_execution_context(
            profile_id="aprof_1",
            operation_id="rop_1",
            instance_id="inst_1",
            workspace_id="ws_a",
            workspace_root=str(root),
            environment_id="env_local",
            generation=1,
            expected_workspace_id="ws_b",
        )
    assert exc.value.code == "workspace_mismatch"


def test_bind_resolves_workspace_root(tmp_path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    ctx = bind_execution_context(
        profile_id="aprof_1",
        operation_id="rop_1",
        instance_id="inst_1",
        workspace_id="ws_1",
        workspace_root=str(root),
        environment_id="env_local",
        generation=1,
        attempt_id="att_1",
        session_ref="sess_1",
        baseline_id="bl_1",
    )
    assert ctx.workspace_root == str(root.resolve())
    assert ctx.as_adapter_payload()["workspaceId"] == "ws_1"


def test_recovery_facets_independent(tmp_path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    ctx = bind_execution_context(
        profile_id="aprof_1",
        operation_id="rop_1",
        instance_id="inst_1",
        workspace_id="ws_1",
        workspace_root=str(root),
        environment_id="env_local",
        generation=1,
        session_ref="sess_1",
    )
    checks = verify_recovery(
        ctx,
        native_session_ok=True,
        workspace_identity_ok=True,
        file_version_ok=False,
    )
    assert [c.facet for c in checks] == ["session", "workspace", "files"]
    assert checks[0].ok is True
    assert checks[2].ok is False
    assert recovery_all_ok(checks) is False


def test_adapter_host_requires_coherent_context(tmp_path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    host = AdapterHost()
    host.register_profile("aprof_1")
    ctx = bind_execution_context(
        profile_id="aprof_1",
        operation_id="rop_1",
        instance_id="inst_1",
        workspace_id="ws_1",
        workspace_root=str(root),
        environment_id="env_local",
        generation=1,
    )
    host.assert_allowed(
        profile_id="aprof_1",
        instance_id="inst_1",
        workspace_id="ws_1",
        operation_id="rop_1",
        context=ctx,
    )
    with pytest.raises(PermissionError):
        host.assert_allowed(
            profile_id="aprof_1",
            instance_id="inst_other",
            workspace_id="ws_1",
            operation_id="rop_1",
            context=ctx,
        )
    with pytest.raises(PermissionError):
        host.require_context(ctx, require_session=True)
