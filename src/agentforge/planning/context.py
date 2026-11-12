"""Build versioned PlanningContext from authorized project facts only."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.plans import PlanningContext
from agentforge.planning.repository import PlanRepository
from agentforge.projects.grants import GrantError, require_active_grant
from agentforge.storage.models import (
    PlanningContextRow,
    Project,
    ProjectBaselineRow,
    ProjectInputRow,
    Run,
)


class PlanningContextError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


class PlanningContextBuilder:
    """Reads BE-04 authorized facts; does not invent ContextManifest for model egress."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = PlanRepository(session)

    def _require_project(self, project_id: str) -> Project:
        project = self.session.get(Project, project_id)
        if project is None:
            raise PlanningContextError("project_not_found", "Project not found")
        if project.grant_status != "active":
            raise PlanningContextError(
                "grant_inactive",
                "Directory grant revoked or superseded; new work blocked",
            )
        try:
            require_active_grant(self.session, project.directory_grant_id)
        except GrantError as exc:
            raise PlanningContextError(exc.code, exc.message) from exc
        return project

    def build_for_run(self, run: Run, *, persist: bool = True) -> PlanningContext:
        project = self._require_project(run.project_id)
        baseline_id = run.baseline_id or project.current_baseline_id
        if not baseline_id:
            raise PlanningContextError("baseline_required", "Run has no pinned baseline")
        baseline = self.session.get(ProjectBaselineRow, baseline_id)
        if baseline is None or baseline.project_id != run.project_id:
            raise PlanningContextError("baseline_not_found", "Baseline not found for project")
        if baseline.status not in {"ready", "partial"}:
            raise PlanningContextError(
                "baseline_unusable",
                f"Baseline status={baseline.status} cannot be used for planning",
            )

        input_ids = list(run.input_refs or [])
        if input_ids:
            available = {
                row.id: row
                for row in self.session.scalars(
                    select(ProjectInputRow).where(
                        ProjectInputRow.project_id == run.project_id,
                        ProjectInputRow.status == "available",
                    )
                ).all()
            }
            for input_id in input_ids:
                if input_id not in available:
                    raise PlanningContextError(
                        "input_unavailable",
                        f"Input {input_id} is missing or not available",
                    )

        summary_payload = {
            "baselineId": baseline.id,
            "baselineStatus": baseline.status,
            "gitCommit": baseline.git_commit,
            "inputIds": sorted(input_ids),
            "projectId": run.project_id,
            "runId": run.id,
        }
        content_hash = hashlib.sha256(
            json.dumps(summary_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        context_id = f"pctx_{uuid.uuid4().hex[:20]}"
        summary = (
            f"baseline={baseline.id} inputs={len(input_ids)} "
            f"dirty_policy={baseline.dirty_policy}"
        )
        view = PlanningContext(
            context_id=context_id,
            project_id=run.project_id,
            run_id=run.id,
            baseline_id=baseline.id,
            input_ids=input_ids,
            read_scopes=[],
            summary=summary,
            content_hash=content_hash,
            context_manifest_id=None,  # never fake a model-egress manifest
            created_at=_now(),
        )
        if persist:
            self.repo.add_context(
                PlanningContextRow(
                    id=view.context_id,
                    project_id=view.project_id,
                    run_id=view.run_id,
                    baseline_id=view.baseline_id,
                    input_ids=list(view.input_ids),
                    read_scopes=list(view.read_scopes),
                    summary=view.summary,
                    content_hash=view.content_hash,
                    context_manifest_id=view.context_manifest_id,
                    created_at=view.created_at,
                )
            )
        return view
