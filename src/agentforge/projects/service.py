"""Project registration, query, baseline coordination, and execution gates (BE-04.3/7)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.grants import ProjectBaseline, TrustPolicy
from agentforge.contracts.projects import (
    ProjectCreateRequest,
    ProjectPrecheckSummary,
    ProjectResponse,
)
from agentforge.core.config import Settings
from agentforge.filesystem.access import (
    FilesystemLimits,
    ProjectFilesystem,
    ProjectFilesystemError,
)
from agentforge.projects.baseline import inspect_precheck
from agentforge.projects.grants import GrantError, require_active_grant
from agentforge.storage.models import (
    DirectoryGrantRow,
    Project,
    ProjectBaselineRow,
    ProjectInputRow,
)


class ProjectError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def default_trust_policy() -> TrustPolicy:
    return TrustPolicy()


def platform_workspace_root(settings: Settings, project_id: str) -> Path:
    """Workspace layout under platform state — never trust client workspaceRoot."""
    root = settings.state_dir.resolve() / "workspaces" / project_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "inputs").mkdir(exist_ok=True)
    (root / "baselines").mkdir(exist_ok=True)
    return root


def open_project_filesystem(
    grant: DirectoryGrantRow,
    settings: Settings,
    trust: TrustPolicy | None = None,
) -> ProjectFilesystem:
    policy = trust or default_trust_policy()
    secret_roots = [
        settings.state_dir.resolve(),
        Path.home() / ".agentforge",
    ]
    try:
        return ProjectFilesystem(
            Path(grant.normalized_root),
            directory_identity=grant.directory_identity,
            limits=FilesystemLimits.from_trust_policy(policy),
            secret_roots=secret_roots,
        )
    except ProjectFilesystemError as exc:
        raise ProjectError(exc.code, exc.message) from exc


class ProjectService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def register(self, request: ProjectCreateRequest) -> ProjectResponse:
        try:
            grant = require_active_grant(self.session, request.directory_grant_id)
        except GrantError as exc:
            raise ProjectError(exc.code, exc.message) from exc

        # Idempotent: same active grant → return existing project.
        existing = self.session.scalar(
            select(Project).where(
                Project.directory_grant_id == grant.id,
                Project.grant_status == "active",
            )
        )
        if existing is not None:
            return self.get(existing.id, include_precheck=True)

        trust = default_trust_policy()
        fs = open_project_filesystem(grant, self.settings, trust)
        project_id = f"proj_{uuid.uuid4().hex[:16]}"
        workspace = platform_workspace_root(self.settings, project_id)

        summary, baseline_draft = inspect_precheck(
            fs,
            dirty_policy=request.dirty_policy,
            environment_id=grant.environment_id,
        )

        project = Project(
            id=project_id,
            directory_grant_id=grant.id,
            display_name=request.display_name,
            root_path=grant.normalized_root,
            workspace_root=str(workspace),
            trust_policy=trust.model_dump(mode="json"),
            environment_id=grant.environment_id,
            grant_version=grant.grant_version,
            grant_status="active",
            revision=1,
        )
        self.session.add(project)
        self.session.flush()

        baseline_row = self._persist_baseline(project_id, baseline_draft)
        project.current_baseline_id = baseline_row.id
        self.session.flush()

        return self._to_response(
            project, summary=summary, baseline=self._baseline_contract(baseline_row)
        )

    def get(self, project_id: str, *, include_precheck: bool = False) -> ProjectResponse:
        project = self.session.get(Project, project_id)
        if project is None:
            raise ProjectError("project_not_found", "Project not found")

        summary: ProjectPrecheckSummary | None = None
        baseline: ProjectBaseline | None = None
        if project.current_baseline_id:
            row = self.session.get(ProjectBaselineRow, project.current_baseline_id)
            if row is not None:
                baseline = self._baseline_contract(row)

        if include_precheck and project.grant_status == "active":
            try:
                grant = require_active_grant(self.session, project.directory_grant_id)
                trust = TrustPolicy.model_validate(project.trust_policy or {})
                fs = open_project_filesystem(grant, self.settings, trust)
                summary, draft = inspect_precheck(
                    fs,
                    dirty_policy=(baseline.dirty_policy if baseline else "committed_only"),
                    environment_id=grant.environment_id,
                )
                if baseline is None:
                    baseline = draft
            except (GrantError, ProjectError) as exc:
                summary = ProjectPrecheckSummary(
                    accessible=False,
                    is_git=False,
                    dirty=False,
                    dirty_policy="committed_only",
                    blockers=[exc.message],
                    status="blocked",
                )

        return self._to_response(project, summary=summary, baseline=baseline)

    def assert_grant_allows_work(self, project_id: str) -> Project:
        """BE-04.7: block new reads/dispatch when grant revoked or baseline/input stale."""
        project = self.session.get(Project, project_id)
        if project is None:
            raise ProjectError("project_not_found", "Project not found")
        if project.grant_status != "active":
            raise ProjectError(
                "grant_inactive",
                "Directory grant revoked or superseded; new work blocked",
            )
        try:
            require_active_grant(self.session, project.directory_grant_id)
        except GrantError as exc:
            project.grant_status = "revoked" if exc.code == "grant_revoked" else "needs_attention"
            self.session.flush()
            raise ProjectError(exc.code, exc.message) from exc
        return project

    def on_grant_revoked(self, grant_id: str) -> list[str]:
        """Mark dependent projects; do not claim external agents already stopped."""
        projects = list(
            self.session.scalars(select(Project).where(Project.directory_grant_id == grant_id))
        )
        affected: list[str] = []
        for project in projects:
            project.grant_status = "revoked"
            project.updated_at = datetime.now(UTC)
            affected.append(project.id)
        self.session.flush()
        return affected

    def invalidate_baseline(self, project_id: str, reason: str) -> None:
        project = self.session.get(Project, project_id)
        if project is None:
            return
        if project.current_baseline_id:
            row = self.session.get(ProjectBaselineRow, project.current_baseline_id)
            if row is not None:
                notes = list(row.notes or [])
                notes.append(f"invalidated:{reason}")
                row.notes = notes[:32]
                row.status = "blocked"
        project.current_baseline_id = None
        project.updated_at = datetime.now(UTC)
        self.session.flush()

    def input_staging_dir(self, project_id: str) -> Path:
        return platform_workspace_root(self.settings, project_id) / "inputs"

    def list_available_inputs(self, project_id: str) -> list[ProjectInputRow]:
        self.assert_grant_allows_work(project_id)
        return list(
            self.session.scalars(
                select(ProjectInputRow).where(
                    ProjectInputRow.project_id == project_id,
                    ProjectInputRow.status == "available",
                )
            )
        )

    def _persist_baseline(self, project_id: str, draft: ProjectBaseline) -> ProjectBaselineRow:
        row = ProjectBaselineRow(
            id=f"base_{uuid.uuid4().hex[:16]}",
            project_id=project_id,
            environment_id=draft.environment_id,
            dirty_policy=draft.dirty_policy,
            status=draft.status,
            is_git=draft.is_git,
            git_commit=draft.git_commit,
            git_dirty_summary=dict(draft.git_dirty_summary or {}),
            manifest_hash=draft.manifest_hash,
            scope_strategy=draft.scope_strategy,
            notes=list(draft.notes or []),
            checked_at=draft.checked_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _baseline_contract(self, row: ProjectBaselineRow) -> ProjectBaseline:
        return ProjectBaseline(
            baseline_id=row.id,
            project_id=row.project_id,
            environment_id=row.environment_id,
            dirty_policy=row.dirty_policy,  # type: ignore[arg-type]
            status=row.status,  # type: ignore[arg-type]
            is_git=row.is_git,
            git_commit=row.git_commit,
            git_dirty_summary=dict(row.git_dirty_summary or {}),
            manifest_hash=row.manifest_hash,
            scope_strategy=row.scope_strategy,
            checked_at=row.checked_at,
            notes=[str(x) for x in (row.notes or [])],
        )

    def _to_response(
        self,
        project: Project,
        *,
        summary: ProjectPrecheckSummary | None = None,
        baseline: ProjectBaseline | None = None,
    ) -> ProjectResponse:
        trust_raw = project.trust_policy or {}
        try:
            trust = TrustPolicy.model_validate(trust_raw)
        except Exception:
            trust = default_trust_policy()
        return ProjectResponse(
            id=project.id,
            directory_grant_id=project.directory_grant_id,
            display_name=project.display_name,
            root_path=project.root_path,
            workspace_root=project.workspace_root,
            trust_policy=trust,
            revision=project.revision,
            current_baseline_id=project.current_baseline_id,
            grant_status=project.grant_status,
            environment_id=project.environment_id or "unknown",
            precheck=summary,
            baseline=baseline,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
