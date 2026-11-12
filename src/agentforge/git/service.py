"""Git worktree prepare, recoverable snapshot, integration with conflicts (D07)."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.git_workspaces import (
    CaptureDirtySnapshotRequest,
    CleanupWorktreeRequest,
    DirtySnapshotView,
    FreezeWorkspaceSnapshotRequest,
    GitStopEvidence,
    IntegrateRequest,
    IntegrationView,
    PrepareWorktreeRequest,
    WorkspaceSnapshotView,
    WorktreeView,
)
from agentforge.git.safe import GitSafeError, probe_repo_limits, run_git
from agentforge.git.snapshot import (
    SnapshotError,
    capture_selected_paths,
    extract_tar_to,
    head_commit,
    list_tar_paths,
    porcelain_status,
    store_workspace_snapshot,
)
from agentforge.runner.quiescence import (
    StopProofError,
    WorkspaceBusyError,
    assert_workspace_quiescent,
    lock_workspace,
    validate_stop_proof,
)
from agentforge.storage.models import (
    ContentObjectRow,
    DirectoryGrantRow,
    GitDirtySnapshotRow,
    GitIntegrationRow,
    GitWorkspaceRow,
    GitWorkspaceSnapshotRow,
    Project,
    ProjectBaselineRow,
)


class GitWorkspaceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


class GitWorkspaceService:
    def __init__(self, session: Session, store: ArtifactStore, state_dir: Path) -> None:
        self.session = session
        self.store = store
        self.state_dir = state_dir.resolve()
        self.wt_root = self.state_dir / "git_worktrees"
        self.wt_root.mkdir(parents=True, exist_ok=True)

    def capture_dirty(self, body: CaptureDirtySnapshotRequest) -> DirtySnapshotView:
        project, baseline, grant = self._load_project_baseline(body.project_id, body.baseline_id)
        self._assert_source_binding(grant, body.source_root)
        try:
            content_hash, manifest_hash, count = capture_selected_paths(
                self.store,
                Path(body.source_root),
                list(body.selected_paths),
            )
        except SnapshotError as exc:
            raise GitWorkspaceError(exc.code, exc.message) from exc
        self._upsert_content(content_hash, size=0)
        row = GitDirtySnapshotRow(
            id=f"gds_{uuid.uuid4().hex[:16]}",
            project_id=project.id,
            baseline_id=baseline.id,
            content_hash=content_hash,
            manifest_hash=manifest_hash,
            path_count=count,
            selected_paths=list(body.selected_paths),
            created_at=_now(),
        )
        self.session.add(row)
        self.session.flush()
        return DirtySnapshotView(
            snapshot_id=row.id,
            project_id=row.project_id,
            baseline_id=row.baseline_id,
            content_hash=row.content_hash,
            manifest_hash=row.manifest_hash,
            path_count=row.path_count,
            created_at=row.created_at,
        )

    def prepare_worktree(self, body: PrepareWorktreeRequest) -> WorktreeView:
        project, baseline, grant = self._load_project_baseline(body.project_id, body.baseline_id)
        self._assert_source_binding(grant, body.source_root)
        if body.directory_identity != grant.directory_identity:
            raise GitWorkspaceError(
                "directory_identity_mismatch",
                "directoryIdentity does not match authorized grant",
            )
        source = Path(body.source_root)
        if not baseline.is_git or not baseline.git_commit:
            raise GitWorkspaceError("baseline_not_git", "Baseline is not a git commit baseline")

        restrictions = probe_repo_limits(source)
        if any(n.startswith("no_commits") for n in restrictions):
            return self._blocked_view(
                project.id,
                baseline.id,
                body.strategy,
                "blocked: repository has no commits",
                restrictions,
            )
        if body.strategy == "blocked":
            return self._blocked_view(
                project.id, baseline.id, body.strategy, "strategy=blocked", restrictions
            )

        dirty = porcelain_status(source)
        has_dirty = bool(dirty["staged"] or dirty["unstaged"] or dirty["untracked"])
        dirty_included = False

        if body.strategy == "commit_only":
            if has_dirty:
                # Allowed, but dirty is NOT in the execution baseline.
                restrictions = list(restrictions) + ["dirty_present_but_excluded_from_baseline"]
        elif body.strategy == "managed_dirty_snapshot":
            if not body.dirty_snapshot_id:
                raise GitWorkspaceError(
                    "dirty_snapshot_required",
                    "managed_dirty_snapshot requires dirtySnapshotId (explicit capture)",
                )
            snap = self.session.get(GitDirtySnapshotRow, body.dirty_snapshot_id)
            if snap is None or snap.baseline_id != baseline.id:
                raise GitWorkspaceError(
                    "dirty_snapshot_invalid", "Dirty snapshot not found/mismatched"
                )
            dirty_included = True
        else:
            raise GitWorkspaceError("unknown_strategy", body.strategy)

        if has_dirty and body.strategy == "commit_only":
            # Do not auto stash/reset/clean — proceed with commit-only worktree.
            pass

        workspace_id = f"gwt_{uuid.uuid4().hex[:16]}"
        path = self.wt_root / project.id / workspace_id
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            run_git(
                source,
                ["worktree", "add", "--detach", str(path), baseline.git_commit],
                check=True,
            )
        except GitSafeError as exc:
            raise GitWorkspaceError(exc.code, exc.message) from exc

        if dirty_included and body.dirty_snapshot_id:
            snap = self.session.get(GitDirtySnapshotRow, body.dirty_snapshot_id)
            assert snap is not None
            try:
                extract_tar_to(self.store, snap.content_hash, path)
            except SnapshotError as exc:
                self._force_remove_worktree(source, path)
                raise GitWorkspaceError(exc.code, exc.message) from exc

        status = "restricted" if any("restricted" in n for n in restrictions) else "active"
        row = GitWorkspaceRow(
            id=workspace_id,
            project_id=project.id,
            baseline_id=baseline.id,
            strategy=body.strategy,
            status=status,
            worktree_path=str(path),
            source_root=str(source.resolve()),
            head_commit=baseline.git_commit,
            dirty_included=dirty_included,
            dirty_snapshot_id=body.dirty_snapshot_id,
            attempt_id=body.attempt_id,
            role_id=body.role_id,
            restrictions=list(restrictions),
            message="worktree prepared; isolation≠full host isolation",
            created_at=_now(),
            updated_at=_now(),
        )
        self.session.add(row)
        self.session.flush()
        return self._worktree_view(row)

    def freeze_snapshot(self, body: FreezeWorkspaceSnapshotRequest) -> WorkspaceSnapshotView:
        row = lock_workspace(self.session, body.workspace_id)
        if row is None:
            raise GitWorkspaceError("workspace_not_found", "Worktree not found")
        if row.status in {"blocked", "closed"}:
            raise GitWorkspaceError("invalid_state", f"Cannot snapshot status={row.status}")
        self._assert_stop_evidence(row, body.stop_evidence)
        path = Path(row.worktree_path)
        try:
            content_hash, manifest_hash, _manifest = store_workspace_snapshot(self.store, path)
        except SnapshotError as exc:
            raise GitWorkspaceError(exc.code, exc.message) from exc
        self._upsert_content(content_hash, size=0)
        snap = GitWorkspaceSnapshotRow(
            id=f"gws_{uuid.uuid4().hex[:16]}",
            workspace_id=row.id,
            content_hash=content_hash,
            manifest_hash=manifest_hash,
            head_commit=head_commit(path),
            stop_operation_id=body.stop_evidence.operation_id,
            created_at=_now(),
        )
        self.session.add(snap)
        row.status = "snapshot_frozen"
        row.updated_at = _now()
        self.session.flush()
        return WorkspaceSnapshotView(
            snapshot_id=snap.id,
            workspace_id=row.id,
            content_hash=snap.content_hash,
            manifest_hash=snap.manifest_hash,
            head_commit=snap.head_commit,
            created_at=snap.created_at,
        )

    def integrate(self, body: IntegrateRequest) -> IntegrationView:
        project, baseline, grant = self._load_project_baseline(body.project_id, body.baseline_id)
        self._assert_source_binding(grant, body.source_root)

        refs = list(body.candidate_snapshots)
        if not refs and body.candidate_workspace_ids:
            raise GitWorkspaceError(
                "snapshot_ids_required",
                "candidateSnapshots with locked snapshotId are required (no latest-follow)",
            )
        order_ids = list(body.merge_order or [r.workspace_id for r in refs])
        ref_by_ws = {r.workspace_id: r for r in refs}
        if set(order_ids) != set(ref_by_ws):
            raise GitWorkspaceError(
                "merge_order_mismatch",
                "mergeOrder must list the same workspaces as candidateSnapshots",
            )

        locked: list[tuple[GitWorkspaceRow, GitWorkspaceSnapshotRow]] = []
        for wid in order_ids:
            ws = self.session.get(GitWorkspaceRow, wid)
            if ws is None or ws.project_id != project.id:
                raise GitWorkspaceError("workspace_not_found", f"Missing workspace {wid}")
            if ws.baseline_id != baseline.id:
                raise GitWorkspaceError(
                    "baseline_mismatch",
                    "All candidates must share the same confirmed baseline",
                )
            snap = self.session.get(GitWorkspaceSnapshotRow, ref_by_ws[wid].snapshot_id)
            if snap is None or snap.workspace_id != ws.id:
                raise GitWorkspaceError(
                    "snapshot_not_found",
                    f"Locked snapshot not found for workspace {wid}",
                )
            locked.append((ws, snap))

        integration_id = f"gin_{uuid.uuid4().hex[:16]}"
        path = self.wt_root / project.id / f"integration_{integration_id}"
        path.parent.mkdir(parents=True, exist_ok=True)
        source = Path(body.source_root)
        try:
            run_git(
                source,
                ["worktree", "add", "--detach", str(path), baseline.git_commit or "HEAD"],
                check=True,
            )
        except GitSafeError as exc:
            raise GitWorkspaceError(exc.code, exc.message) from exc

        baseline_paths = {
            p.relative_to(path).as_posix()
            for p in path.rglob("*")
            if p.is_file() and not p.is_symlink() and ".git" not in p.relative_to(path).parts
        }
        conflict_paths: list[str] = []
        # Declared strategy: full-tree overlay + explicit deletes vs baseline;
        # conflict on write/write or write/delete disagreement (no silent side pick).
        written_by: dict[str, str] = {}
        deleted_by: dict[str, str] = {}
        for ws, snap in locked:
            overlay = path.parent / f".overlay_{ws.id}"
            if overlay.exists():
                shutil.rmtree(overlay)
            try:
                extract_tar_to(self.store, snap.content_hash, overlay)
                overlay_paths = list_tar_paths(self.store, snap.content_hash)
            except SnapshotError as exc:
                self._force_remove_worktree(source, path)
                raise GitWorkspaceError(exc.code, exc.message) from exc

            for rel in sorted(baseline_paths - overlay_paths):
                if rel in written_by and written_by[rel] != ws.id:
                    conflict_paths.append(rel)
                    continue
                if rel in deleted_by and deleted_by[rel] != ws.id:
                    continue
                deleted_by[rel] = ws.id

            for file in sorted(overlay.rglob("*")):
                if not file.is_file() or file.is_symlink():
                    continue
                rel = file.relative_to(overlay).as_posix()
                if rel in deleted_by and deleted_by[rel] != ws.id:
                    conflict_paths.append(rel)
                    continue
                dest = path / rel
                new_bytes = file.read_bytes()
                if dest.exists() and dest.is_file():
                    cur = dest.read_bytes()
                    if cur == new_bytes:
                        written_by.setdefault(rel, ws.id)
                        continue
                    if rel in written_by and written_by[rel] != ws.id:
                        conflict_paths.append(rel)
                        continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, dest)
                written_by[rel] = ws.id
                deleted_by.pop(rel, None)
            shutil.rmtree(overlay, ignore_errors=True)

        for rel, _ws_id in deleted_by.items():
            if rel in conflict_paths:
                continue
            dest = path / rel
            if dest.exists() and dest.is_file():
                dest.unlink()

        if conflict_paths:
            row = GitIntegrationRow(
                id=integration_id,
                project_id=project.id,
                baseline_id=baseline.id,
                status="conflicted",
                worktree_path=str(path),
                input_workspace_ids=order_ids,
                conflict_paths=sorted(set(conflict_paths)),
                content_hash=None,
                manifest_hash=None,
                stage_released=False,
                message="merge conflicts retained; no silent side pick",
                created_at=_now(),
                updated_at=_now(),
            )
            self.session.add(row)
            self.session.flush()
            return self._integration_view(row)

        try:
            content_hash, manifest_hash, _ = store_workspace_snapshot(self.store, path)
        except SnapshotError as exc:
            raise GitWorkspaceError(exc.code, exc.message) from exc
        self._upsert_content(content_hash, size=0)
        row = GitIntegrationRow(
            id=integration_id,
            project_id=project.id,
            baseline_id=baseline.id,
            status="ready",
            worktree_path=str(path),
            input_workspace_ids=order_ids,
            conflict_paths=[],
            content_hash=content_hash,
            manifest_hash=manifest_hash,
            stage_released=False,
            message="integration ready; locked snapshots; not stage-released (D10)",
            created_at=_now(),
            updated_at=_now(),
        )
        self.session.add(row)
        self.session.flush()
        return self._integration_view(row)

    def cleanup(self, body: CleanupWorktreeRequest) -> WorktreeView:
        row = lock_workspace(self.session, body.workspace_id)
        if row is None:
            raise GitWorkspaceError("workspace_not_found", "Worktree not found")
        try:
            assert_workspace_quiescent(self.session, row.id, except_operation="")
        except WorkspaceBusyError as exc:
            raise GitWorkspaceError("workspace_active", str(exc)) from exc
        source = Path(row.source_root)
        path = Path(row.worktree_path)
        if path.resolve() == source.resolve():
            raise GitWorkspaceError("refuse_user_root", "Refusing to cleanup user source root")
        # Active / unresolved integrations always block — force cannot bypass refs.
        integrations = [
            r
            for r in self.session.scalars(select(GitIntegrationRow)).all()
            if row.id in (r.input_workspace_ids or [])
            and r.status in {"pending", "merging", "conflicted"}
        ]
        if integrations:
            raise GitWorkspaceError(
                "refs_active",
                "Active integration references this workspace; refuse cleanup (force cannot bypass)",
            )
        if row.status == "active" and not body.force:
            raise GitWorkspaceError(
                "workspace_active",
                "Workspace still active; stop and freeze first, or pass force only after safe stop",
            )
        # force only allows removing disposable trees after snapshot_frozen/closed/blocked
        if body.force and row.status not in {"snapshot_frozen", "closed", "blocked", "restricted"}:
            raise GitWorkspaceError(
                "force_not_safe",
                "force only applies after safe stop/freeze; not for unknown-active workspaces",
            )
        self._force_remove_worktree(source, path)
        row.status = "closed"
        row.updated_at = _now()
        self.session.flush()
        return self._worktree_view(row)

    # --- helpers ---

    def _load_project_baseline(
        self, project_id: str, baseline_id: str
    ) -> tuple[Project, ProjectBaselineRow, DirectoryGrantRow]:
        project = self.session.get(Project, project_id)
        if project is None:
            raise GitWorkspaceError("project_not_found", "Project not found")
        baseline = self.session.get(ProjectBaselineRow, baseline_id)
        if baseline is None or baseline.project_id != project_id:
            raise GitWorkspaceError("baseline_not_found", "Baseline not found for project")
        if baseline.status not in {"ready", "captured", "active", "ok"}:
            # Accept common BE-04 statuses; block clearly bad ones.
            if baseline.status == "blocked":
                raise GitWorkspaceError("baseline_blocked", "Baseline is blocked")
        grant = self.session.get(DirectoryGrantRow, project.directory_grant_id)
        if grant is None or grant.status != "active":
            raise GitWorkspaceError("grant_inactive", "Project directory grant not active")
        return project, baseline, grant

    def _assert_source_binding(self, grant: DirectoryGrantRow, source_root: str) -> None:
        authorized = Path(grant.normalized_root).resolve()
        given = Path(source_root).resolve()
        if given != authorized:
            raise GitWorkspaceError(
                "source_not_authorized",
                "sourceRoot must equal the authorized grant root (no client-arbitrary bind)",
            )

    def _assert_stop_evidence(self, workspace: GitWorkspaceRow, claim: GitStopEvidence) -> None:
        try:
            validate_stop_proof(
                self.session,
                workspace_id=workspace.id,
                workspace_root=workspace.worktree_path,
                expected_attempt_id=workspace.attempt_id,
                claim=claim,
            )
        except StopProofError as exc:
            raise GitWorkspaceError(exc.code, exc.message) from exc

    def _force_remove_worktree(self, source: Path, path: Path) -> None:
        if path.exists():
            try:
                run_git(source, ["worktree", "remove", "--force", str(path)])
            except GitSafeError:
                shutil.rmtree(path, ignore_errors=True)
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
        try:
            run_git(source, ["worktree", "prune"])
        except GitSafeError:
            pass

    def _upsert_content(self, content_hash: str, *, size: int) -> None:
        if self.session.get(ContentObjectRow, content_hash) is None:
            self.session.add(
                ContentObjectRow(
                    content_hash=content_hash,
                    size_bytes=size,
                    created_at=_now(),
                )
            )

    def _blocked_view(
        self,
        project_id: str,
        baseline_id: str,
        strategy: str,
        message: str,
        restrictions: list[str],
    ) -> WorktreeView:
        row = GitWorkspaceRow(
            id=f"gwt_{uuid.uuid4().hex[:16]}",
            project_id=project_id,
            baseline_id=baseline_id,
            strategy=strategy,
            status="blocked",
            worktree_path="blocked",
            source_root="blocked",
            head_commit=None,
            dirty_included=False,
            dirty_snapshot_id=None,
            attempt_id=None,
            role_id=None,
            restrictions=list(restrictions),
            message=message,
            created_at=_now(),
            updated_at=_now(),
        )
        self.session.add(row)
        self.session.flush()
        return self._worktree_view(row)

    def _worktree_view(self, row: GitWorkspaceRow) -> WorktreeView:
        return WorktreeView(
            workspace_id=row.id,
            project_id=row.project_id,
            baseline_id=row.baseline_id,
            strategy=row.strategy,  # type: ignore[arg-type]
            status=row.status,  # type: ignore[arg-type]
            worktree_path=row.worktree_path or ".",
            head_commit=row.head_commit,
            restrictions=[str(x) for x in (row.restrictions or [])],
            dirty_included=bool(row.dirty_included),
            message=row.message,
            created_at=row.created_at,
        )

    def _integration_view(self, row: GitIntegrationRow) -> IntegrationView:
        return IntegrationView(
            integration_id=row.id,
            project_id=row.project_id,
            baseline_id=row.baseline_id,
            status=row.status,  # type: ignore[arg-type]
            worktree_path=row.worktree_path,
            conflict_paths=[str(x) for x in (row.conflict_paths or [])],
            content_hash=row.content_hash,
            manifest_hash=row.manifest_hash,
            input_workspace_ids=[str(x) for x in (row.input_workspace_ids or [])],
            message=row.message,
            stage_released=bool(row.stage_released),
            created_at=row.created_at,
        )
