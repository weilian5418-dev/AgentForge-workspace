"""D05 Runner: profiles, path confinement, ticket replay/expiry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentforge.contracts.runner import RunnerExecuteRequest, RunnerTicketIssueRequest
from agentforge.runner.paths import RunnerPathError, resolve_under_workspace, resolve_workspace_root
from agentforge.runner.profiles import execute_profile, list_profiles
from agentforge.runner.service import RunnerError, RunnerService
from agentforge.runner.tickets import compute_runner_digest
from agentforge.storage.models import RunnerOperationRow


class _MemSession:
    def __init__(self) -> None:
        self.store: dict[tuple[type, str], Any] = {}
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)
        if getattr(row, "id", None):
            self.store[(type(row), row.id)] = row

    def flush(self) -> None:
        return None

    def get(self, model: type, key: str) -> Any | None:
        return self.store.get((model, key))


def _issue(
    svc: RunnerService,
    workspace: Path,
    *,
    profile_id: str = "prepare_dir",
    params: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
) -> Any:
    body = RunnerTicketIssueRequest.model_validate(
        {
            "profileId": profile_id,
            "workspaceId": "ws_1",
            "workspaceRoot": str(workspace),
            "scope": "ws_1/",
            "params": params or {"path": "out"},
            "expiresAt": expires_at or (datetime.now(UTC) + timedelta(hours=1)),
        }
    )
    return svc.issue(body)


def test_profiles_include_blocked_container() -> None:
    ids = {p.profile_id for p in list_profiles()}
    assert "prepare_dir" in ids
    assert "run_in_container" in ids
    container = next(p for p in list_profiles() if p.profile_id == "run_in_container")
    assert container.docker_required is True
    assert container.host_supported is False


def test_path_escape_rejected(tmp_path: Path) -> None:
    root = resolve_workspace_root(str(tmp_path))
    with pytest.raises(RunnerPathError) as exc:
        resolve_under_workspace(root, "../outside.txt")
    assert exc.value.code == "invalid_relative_path"


def test_prepare_render_read_roundtrip(tmp_path: Path) -> None:
    session = _MemSession()
    svc = RunnerService(session)  # type: ignore[arg-type]
    ticket = _issue(svc, tmp_path, profile_id="prepare_dir", params={"path": "build"})
    done = svc.execute(
        RunnerExecuteRequest.model_validate(
            {"operationId": ticket.operation_id, "actionDigest": ticket.action_digest}
        )
    )
    assert done.status == "succeeded"
    assert (tmp_path / "build").is_dir()

    render = _issue(
        svc,
        tmp_path,
        profile_id="render",
        params={"path": "build/hello.txt", "content": "hi"},
    )
    rendered = svc.execute(
        RunnerExecuteRequest.model_validate(
            {"operationId": render.operation_id, "actionDigest": render.action_digest}
        )
    )
    assert rendered.status == "succeeded"
    assert (tmp_path / "build" / "hello.txt").read_text(encoding="utf-8") == "hi"

    read = _issue(
        svc,
        tmp_path,
        profile_id="read_snippet",
        params={"path": "build/hello.txt"},
    )
    got = svc.execute(
        RunnerExecuteRequest.model_validate(
            {"operationId": read.operation_id, "actionDigest": read.action_digest}
        )
    )
    assert got.status == "succeeded"
    assert got.result.get("content") == "hi"


def test_replay_returns_same_result(tmp_path: Path) -> None:
    session = _MemSession()
    svc = RunnerService(session)  # type: ignore[arg-type]
    ticket = _issue(svc, tmp_path, params={"path": "a"})
    req = RunnerExecuteRequest.model_validate(
        {"operationId": ticket.operation_id, "actionDigest": ticket.action_digest}
    )
    first = svc.execute(req)
    second = svc.execute(req)
    assert first.status == second.status == "succeeded"
    assert first.result == second.result


def test_digest_mismatch_rejected(tmp_path: Path) -> None:
    session = _MemSession()
    svc = RunnerService(session)  # type: ignore[arg-type]
    ticket = _issue(svc, tmp_path)
    with pytest.raises(RunnerError) as exc:
        svc.execute(
            RunnerExecuteRequest.model_validate(
                {"operationId": ticket.operation_id, "actionDigest": "0" * 64}
            )
        )
    assert exc.value.code == "action_digest_mismatch"


def test_expired_ticket(tmp_path: Path) -> None:
    session = _MemSession()
    svc = RunnerService(session)  # type: ignore[arg-type]
    ticket = _issue(
        svc,
        tmp_path,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    row = session.get(RunnerOperationRow, ticket.operation_id)
    assert row is not None
    row.expires_at = datetime(2020, 1, 1, tzinfo=UTC)
    with pytest.raises(RunnerError) as exc:
        svc.execute(
            RunnerExecuteRequest.model_validate(
                {
                    "operationId": ticket.operation_id,
                    "actionDigest": ticket.action_digest,
                }
            )
        )
    assert exc.value.code == "expired"


def test_container_profile_blocked(tmp_path: Path) -> None:
    session = _MemSession()
    svc = RunnerService(session)  # type: ignore[arg-type]
    ticket = _issue(svc, tmp_path, profile_id="run_in_container", params={})
    done = svc.execute(
        RunnerExecuteRequest.model_validate(
            {"operationId": ticket.operation_id, "actionDigest": ticket.action_digest}
        )
    )
    assert done.status == "blocked"


def test_running_reentry_needs_reconciliation(tmp_path: Path) -> None:
    session = _MemSession()
    svc = RunnerService(session)  # type: ignore[arg-type]
    ticket = _issue(svc, tmp_path)
    row = session.get(RunnerOperationRow, ticket.operation_id)
    assert row is not None
    row.status = "running"
    with pytest.raises(RunnerError) as exc:
        svc.execute(
            RunnerExecuteRequest.model_validate(
                {
                    "operationId": ticket.operation_id,
                    "actionDigest": ticket.action_digest,
                }
            )
        )
    assert exc.value.code == "needs_reconciliation"


def test_digest_changes_with_params() -> None:
    a = compute_runner_digest(
        profile_id="render",
        workspace_id="ws",
        node_id="local",
        attempt_id=None,
        generation=1,
        scope="s",
        params={"path": "a", "content": "1"},
    )
    b = compute_runner_digest(
        profile_id="render",
        workspace_id="ws",
        node_id="local",
        attempt_id=None,
        generation=1,
        scope="s",
        params={"path": "a", "content": "2"},
    )
    assert a != b


def test_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    link = ws / "leak"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink not permitted on this host")
    root = resolve_workspace_root(str(ws))
    with pytest.raises(RunnerPathError) as exc:
        resolve_under_workspace(root, "leak/secret.txt")
    assert exc.value.code in {"symlink_escape", "path_escape"}


def test_execute_profile_check_allowlist(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    ok = execute_profile(
        "run_approved_check",
        workspace_root=tmp_path,
        params={"check": "path_exists", "path": "f.txt"},
    )
    assert ok["status"] == "succeeded"
    blocked = execute_profile(
        "run_approved_check",
        workspace_root=tmp_path,
        params={"check": "shell", "cmd": "echo hi"},
    )
    assert blocked["status"] == "blocked"
