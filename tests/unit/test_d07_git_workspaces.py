"""D07: real git FS — dirty strategies, conflicts, freeze recoverability, cleanup guards."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.git_workspaces import (
    CaptureDirtySnapshotRequest,
    CleanupWorktreeRequest,
    FreezeWorkspaceSnapshotRequest,
    IntegrateRequest,
    PrepareWorktreeRequest,
)
from agentforge.git.safe import GitSafeError, run_git
from agentforge.git.service import GitWorkspaceError, GitWorkspaceService
from agentforge.storage.models import (
    DirectoryGrantRow,
    GitDirtySnapshotRow,
    GitIntegrationRow,
    GitWorkspaceRow,
    GitWorkspaceSnapshotRow,
    Project,
    ProjectBaselineRow,
)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _MemSession:
    def __init__(self) -> None:
        self.store: dict[tuple[type, str], Any] = {}
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)
        key = getattr(row, "id", None)
        if key is None and hasattr(row, "content_hash"):
            key = row.content_hash
        if key is not None:
            self.store[(type(row), str(key))] = row

    def flush(self) -> None:
        return None

    def get(self, model: type, key: str) -> Any | None:
        return self.store.get((model, key))

    def scalar(self, stmt: Any) -> Any | None:
        text = str(stmt).lower()
        if "git_workspace_snapshots" in text:
            rows = [r for r in self.added if isinstance(r, GitWorkspaceSnapshotRow)]
            try:
                params = dict(stmt.compile().params)
                for value in params.values():
                    matched = [r for r in rows if r.workspace_id == value]
                    if matched:
                        return sorted(matched, key=lambda r: r.created_at, reverse=True)[0]
            except Exception:
                pass
            for row in sorted(rows, key=lambda r: r.created_at, reverse=True):
                if row.workspace_id in text:
                    return row
            return None
        return None

    def scalars(self, stmt: Any) -> _Result:
        text = str(stmt).lower()
        if "git_integrations" in text:
            return _Result([r for r in self.added if isinstance(r, GitIntegrationRow)])
        if "git_workspace_snapshots" in text:
            return _Result([r for r in self.added if isinstance(r, GitWorkspaceSnapshotRow)])
        if "git_workspaces" in text:
            return _Result([r for r in self.added if isinstance(r, GitWorkspaceRow)])
        return _Result([])


def _git_available() -> bool:
    try:
        run_git(None, ["--version"])
        return True
    except GitSafeError:
        return False


pytestmark = pytest.mark.skipif(not _git_available(), reason="git not on PATH")


def _init_repo(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    run_git(None, ["init", str(root)], check=True)
    run_git(root, ["config", "user.email", "d07@agentforge.test"], check=True)
    run_git(root, ["config", "user.name", "D07 Test"], check=True)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    (root / "shared.txt").write_text("shared-v1\n", encoding="utf-8")
    run_git(root, ["add", "README.md", "shared.txt"], check=True)
    run_git(root, ["commit", "-m", "initial"], check=True)
    head = run_git(root, ["rev-parse", "HEAD"], check=True)
    return head.stdout.strip()


def _seed(
    session: _MemSession,
    *,
    root: Path,
    commit: str,
    project_id: str = "prj_d07",
    baseline_id: str = "bl_d07",
    grant_id: str = "gr_d07",
    identity: str = "dir_id_d07",
) -> None:
    now = datetime.now(UTC)
    session.add(
        DirectoryGrantRow(
            id=grant_id,
            authorized_user="local",
            environment_id="env_local",
            normalized_root=str(root.resolve()),
            directory_identity=identity,
            allowed_operations=["read", "write"],
            grant_version=1,
            status="active",
            created_at=now,
            revoked_at=None,
        )
    )
    session.add(
        Project(
            id=project_id,
            directory_grant_id=grant_id,
            display_name="d07",
            root_path=str(root.resolve()),
            workspace_root=str(root.resolve()),
            trust_policy={},
            environment_id="env_local",
            grant_version=1,
            current_baseline_id=baseline_id,
            grant_status="active",
            revision=1,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        ProjectBaselineRow(
            id=baseline_id,
            project_id=project_id,
            environment_id="env_local",
            dirty_policy="committed_only",
            status="ready",
            is_git=True,
            git_commit=commit,
            git_dirty_summary={},
            manifest_hash=None,
            scope_strategy="authorized_root",
            notes=[],
            checked_at=now,
            created_at=now,
        )
    )


@pytest.fixture
def harness(tmp_path: Path) -> tuple[GitWorkspaceService, _MemSession, Path, str, str]:
    repo = tmp_path / "repo"
    commit = _init_repo(repo)
    session = _MemSession()
    _seed(session, root=repo, commit=commit)
    store = ArtifactStore(tmp_path / "artifact_store")
    svc = GitWorkspaceService(session, store, tmp_path / "state")
    return svc, session, repo, commit, "dir_id_d07"


def test_commit_only_excludes_dirty(harness: tuple) -> None:
    svc, _session, repo, _commit, identity = harness
    (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    view = svc.prepare_worktree(
        PrepareWorktreeRequest.model_validate(
            {
                "projectId": "prj_d07",
                "baselineId": "bl_d07",
                "strategy": "commit_only",
                "sourceRoot": str(repo.resolve()),
                "directoryIdentity": identity,
            }
        )
    )
    assert view.status in {"active", "restricted"}
    assert view.dirty_included is False
    assert "dirty_present_but_excluded_from_baseline" in view.restrictions
    assert not (Path(view.worktree_path) / "dirty.txt").exists()
    # User source still dirty — no auto stash/clean
    assert (repo / "dirty.txt").exists()


def test_managed_dirty_snapshot_requires_explicit_capture(harness: tuple) -> None:
    svc, _session, repo, _commit, identity = harness
    with pytest.raises(GitWorkspaceError) as exc:
        svc.prepare_worktree(
            PrepareWorktreeRequest.model_validate(
                {
                    "projectId": "prj_d07",
                    "baselineId": "bl_d07",
                    "strategy": "managed_dirty_snapshot",
                    "sourceRoot": str(repo.resolve()),
                    "directoryIdentity": identity,
                }
            )
        )
    assert exc.value.code == "dirty_snapshot_required"


def test_managed_dirty_applies_selected_paths(harness: tuple) -> None:
    svc, session, repo, _commit, identity = harness
    (repo / "dirty.txt").write_text("picked\n", encoding="utf-8")
    snap = svc.capture_dirty(
        CaptureDirtySnapshotRequest.model_validate(
            {
                "projectId": "prj_d07",
                "baselineId": "bl_d07",
                "sourceRoot": str(repo.resolve()),
                "selectedPaths": ["dirty.txt"],
            }
        )
    )
    assert isinstance(session.get(GitDirtySnapshotRow, snap.snapshot_id), GitDirtySnapshotRow)
    view = svc.prepare_worktree(
        PrepareWorktreeRequest.model_validate(
            {
                "projectId": "prj_d07",
                "baselineId": "bl_d07",
                "strategy": "managed_dirty_snapshot",
                "sourceRoot": str(repo.resolve()),
                "directoryIdentity": identity,
                "dirtySnapshotId": snap.snapshot_id,
            }
        )
    )
    assert view.dirty_included is True
    assert (Path(view.worktree_path) / "dirty.txt").read_text(encoding="utf-8") == "picked\n"


def test_blocked_strategy_no_worktree(harness: tuple) -> None:
    svc, _session, repo, _commit, identity = harness
    view = svc.prepare_worktree(
        PrepareWorktreeRequest.model_validate(
            {
                "projectId": "prj_d07",
                "baselineId": "bl_d07",
                "strategy": "blocked",
                "sourceRoot": str(repo.resolve()),
                "directoryIdentity": identity,
            }
        )
    )
    assert view.status == "blocked"
    assert view.worktree_path == "blocked"


def test_freeze_recoverable_after_worktree_gone(harness: tuple) -> None:
    svc, _session, repo, _commit, identity = harness
    view = svc.prepare_worktree(
        PrepareWorktreeRequest.model_validate(
            {
                "projectId": "prj_d07",
                "baselineId": "bl_d07",
                "strategy": "commit_only",
                "sourceRoot": str(repo.resolve()),
                "directoryIdentity": identity,
            }
        )
    )
    wt = Path(view.worktree_path)
    (wt / "extra.py").write_text("print(1)\n", encoding="utf-8")
    frozen = svc.freeze_snapshot(
        FreezeWorkspaceSnapshotRequest.model_validate({"workspaceId": view.workspace_id})
    )
    assert frozen.content_hash
    # Simulate crash: remove live worktree
    import shutil

    shutil.rmtree(wt, ignore_errors=True)
    assert not wt.exists()
    # Snapshot still readable from D06 store
    data = svc.store.get_bytes(frozen.content_hash)
    assert len(data) > 0


def test_integrate_conflict_retained(harness: tuple) -> None:
    svc, _session, repo, _commit, identity = harness

    def _prepare_and_edit(filename: str, content: str) -> str:
        view = svc.prepare_worktree(
            PrepareWorktreeRequest.model_validate(
                {
                    "projectId": "prj_d07",
                    "baselineId": "bl_d07",
                    "strategy": "commit_only",
                    "sourceRoot": str(repo.resolve()),
                    "directoryIdentity": identity,
                }
            )
        )
        (Path(view.worktree_path) / filename).write_text(content, encoding="utf-8")
        svc.freeze_snapshot(
            FreezeWorkspaceSnapshotRequest.model_validate({"workspaceId": view.workspace_id})
        )
        return view.workspace_id

    a = _prepare_and_edit("shared.txt", "from-a\n")
    b = _prepare_and_edit("shared.txt", "from-b\n")

    result = svc.integrate(
        IntegrateRequest.model_validate(
            {
                "projectId": "prj_d07",
                "baselineId": "bl_d07",
                "sourceRoot": str(repo.resolve()),
                "directoryIdentity": identity,
                "candidateWorkspaceIds": [a, b],
            }
        )
    )
    assert result.status == "conflicted"
    assert "shared.txt" in result.conflict_paths
    assert result.stage_released is False


def test_cleanup_refuses_user_root_and_active_refs(harness: tuple) -> None:
    svc, session, repo, _commit, identity = harness
    view = svc.prepare_worktree(
        PrepareWorktreeRequest.model_validate(
            {
                "projectId": "prj_d07",
                "baselineId": "bl_d07",
                "strategy": "commit_only",
                "sourceRoot": str(repo.resolve()),
                "directoryIdentity": identity,
            }
        )
    )
    svc.freeze_snapshot(
        FreezeWorkspaceSnapshotRequest.model_validate({"workspaceId": view.workspace_id})
    )
    # Seed an active integration referencing the workspace
    session.add(
        GitIntegrationRow(
            id="gin_active",
            project_id="prj_d07",
            baseline_id="bl_d07",
            status="conflicted",
            worktree_path=str(Path(view.worktree_path)),
            input_workspace_ids=[view.workspace_id],
            conflict_paths=["x"],
            content_hash=None,
            manifest_hash=None,
            stage_released=False,
            message="held",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    with pytest.raises(GitWorkspaceError) as exc:
        svc.cleanup(
            CleanupWorktreeRequest.model_validate({"workspaceId": view.workspace_id, "force": False})
        )
    assert exc.value.code == "refs_active"

    closed = svc.cleanup(
        CleanupWorktreeRequest.model_validate({"workspaceId": view.workspace_id, "force": True})
    )
    assert closed.status == "closed"
    assert not Path(view.worktree_path).exists()
    # User source root intact
    assert (repo / "README.md").exists()


def test_source_must_match_grant(harness: tuple) -> None:
    svc, _session, repo, _commit, identity = harness
    other = repo.parent / "other"
    other.mkdir()
    with pytest.raises(GitWorkspaceError) as exc:
        svc.prepare_worktree(
            PrepareWorktreeRequest.model_validate(
                {
                    "projectId": "prj_d07",
                    "baselineId": "bl_d07",
                    "strategy": "commit_only",
                    "sourceRoot": str(other.resolve()),
                    "directoryIdentity": identity,
                }
            )
        )
    assert exc.value.code == "source_not_authorized"


def test_network_git_blocked_by_default(tmp_path: Path) -> None:
    with pytest.raises(GitSafeError) as exc:
        run_git(tmp_path, ["fetch", "origin"], allow_network=True)
    assert exc.value.code == "network_not_authorized"
