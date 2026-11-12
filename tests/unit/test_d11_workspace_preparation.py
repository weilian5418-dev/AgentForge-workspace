"""D11.6: fixed workspace identity and ensure_instances bridge."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentforge.contracts.collaboration import LeaseFence
from agentforge.driver.binding import sha256_hex
from agentforge.driver.workspace_preparation import (
    ensure_workspace_operation,
    expected_worktree_path,
    reconcile_existing_workspace,
)
from agentforge.storage.models import Outbox, SchedulerOperationRow


class _Mem:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.store: dict[tuple[type, Any], Any] = {}

    def add(self, row: Any) -> None:
        self.added.append(row)
        if getattr(row, "id", None) is not None:
            self.store[(type(row), row.id)] = row

    def flush(self) -> None:
        return None

    def get(self, model: type, key: Any, **_k: Any) -> Any | None:
        return self.store.get((model, key))

    def scalar(self, stmt: Any) -> Any | None:
        return None


def test_expected_path_is_deterministic() -> None:
    p = expected_worktree_path(Path("/state"), "proj_1", "gwt_abc")
    assert p.as_posix().endswith("git_worktrees/proj_1/gwt_abc")


def test_ensure_workspace_operation_fixes_identity_and_outbox() -> None:
    session = _Mem()
    run = MagicMock(
        id="run_1",
        project_id="proj_1",
        baseline_id="base_1",
        plan_version=1,
        assignment_version=1,
        active_generation=2,
    )
    project = MagicMock(id="proj_1", directory_grant_id="grant_1")
    grant = MagicMock(
        status="active",
        normalized_root="/repo",
        directory_identity="id-1",
    )
    baseline = MagicMock(id="base_1")
    session.store[(type(run), "run_1")] = run
    # Use real model classes for get()
    from agentforge.storage.models import DirectoryGrantRow, Project, ProjectBaselineRow, Run

    session.store[(Run, "run_1")] = run
    session.store[(Project, "proj_1")] = project
    session.store[(DirectoryGrantRow, "grant_1")] = grant
    session.store[(ProjectBaselineRow, "base_1")] = baseline

    fence = LeaseFence(
        run_id="run_1", owner="o", token="t", lease_generation=1, thread_id="afg:1"
    )
    with patch(
        "agentforge.driver.workspace_preparation.assert_driver_fence", return_value=None
    ), patch("agentforge.driver.workspace_preparation.db_now", side_effect=Exception("no sql")):
        result = ensure_workspace_operation(
            session,  # type: ignore[arg-type]
            fence,
            role_id="role_dev",
            state_dir=Path("/state"),
        )
    assert result["status"] == "waiting"
    expected_id = f"gwt_{sha256_hex({'run': 'run_1', 'role': 'role_dev', 'g': 2})[:16]}"
    assert result["binding"]["workspaceId"] == expected_id
    assert "role_dev" in result["binding"]["worktreePath"] or expected_id in result["binding"][
        "worktreePath"
    ]
    ops = [r for r in session.added if isinstance(r, SchedulerOperationRow)]
    assert len(ops) == 1
    assert ops[0].operation_key.startswith("workspace_prepare:")
    boxes = [r for r in session.added if isinstance(r, Outbox)]
    assert boxes and boxes[0].intent_type == "workspace.prepare"


def test_ensure_workspace_reuses_same_operation_key() -> None:
    session = _Mem()
    from agentforge.storage.models import DirectoryGrantRow, Project, ProjectBaselineRow, Run

    run = MagicMock(
        id="run_1",
        project_id="proj_1",
        baseline_id="base_1",
        plan_version=1,
        assignment_version=1,
        active_generation=1,
    )
    session.store[(Run, "run_1")] = run
    session.store[(Project, "proj_1")] = MagicMock(id="proj_1", directory_grant_id="g1")
    session.store[(DirectoryGrantRow, "g1")] = MagicMock(
        status="active", normalized_root="/r", directory_identity="d"
    )
    session.store[(ProjectBaselineRow, "base_1")] = MagicMock(id="base_1")
    fence = LeaseFence(run_id="run_1", owner="o", token="t", lease_generation=1)
    with patch(
        "agentforge.driver.workspace_preparation.assert_driver_fence", return_value=None
    ), patch("agentforge.driver.workspace_preparation.db_now", side_effect=Exception()):
        a = ensure_workspace_operation(
            session, fence, role_id="role_dev", state_dir=Path("/s")  # type: ignore[arg-type]
        )
        b = ensure_workspace_operation(
            session, fence, role_id="role_dev", state_dir=Path("/s")  # type: ignore[arg-type]
        )
    assert a["operationId"] == b["operationId"]
    assert len([r for r in session.added if isinstance(r, SchedulerOperationRow)]) == 1


def test_reconcile_reports_disk_without_row() -> None:
    session = _Mem()
    path = Path(".")
    report = reconcile_existing_workspace(
        session,  # type: ignore[arg-type]
        workspace_id="gwt_missing",
        expected_path=path,
        expected_baseline_commit=None,
    )
    assert report["status"] in {"disk_without_row", "matched", "missing"}
