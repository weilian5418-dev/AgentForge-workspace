"""Three-phase integration build coordinator (D12.9)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.integrations import IntegrationBuildRequest, IntegrationBuildView
from agentforge.core.config import Settings
from agentforge.driver.binding import sha256_hex
from agentforge.integration.builder import (
    TreeEntry,
    combine_approved_deltas,
    compute_tree_delta,
    materialize_merge_plan,
)
from agentforge.integration.workspaces import (
    WorkspaceError,
    build_workspace_root,
    verify_workspace_binding,
    write_build_marker,
)
from agentforge.queries.authorization import AuthenticatedPrincipal
from agentforge.storage.models import (
    ArtifactCandidateRow,
    DirectoryGrantRow,
    IntegrationBuildRow,
    IntegrationSelectionRow,
    IntegrationVersionRow,
    Outbox,
    Project,
    ProjectBaselineRow,
    Run,
    RunDeliveryRow,
)
from agentforge.storage.repositories.events import append_domain_fact
from agentforge.storage.uow import db_now, unit_of_work_connection


class IntegrationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now(session: Session) -> datetime:
    try:
        value = db_now(session)
        if getattr(value, "tzinfo", None) is None:
            return value.replace(tzinfo=UTC)
        return value
    except Exception:
        return datetime.now(UTC)


def _to_view(row: IntegrationBuildRow) -> IntegrationBuildView:
    return IntegrationBuildView.model_validate(
        {
            "buildId": row.id,
            "runId": row.run_id,
            "selectionId": row.selection_id,
            "operationId": row.id,
            "status": row.status,
            "workspaceRef": row.id,
            "resultVersionId": row.result_version_id,
            "errorCode": row.error_code,
            "createdAt": row.created_at,
            "updatedAt": row.updated_at,
        }
    )


def request_integration_build(
    engine: Engine,
    *,
    principal: AuthenticatedPrincipal,
    run_id: str,
    body: IntegrationBuildRequest,
    settings: Settings,
) -> IntegrationBuildView:
    """Phase 1: short transaction — accept intent, allocate workspace, enqueue."""
    if not principal.credential_id:
        raise IntegrationError("unauthorized", "principal required")
    with unit_of_work_connection(engine) as uow:
        session = uow.session
        run = session.get(Run, run_id, with_for_update=True)
        if run is None:
            raise IntegrationError("run_not_found", run_id)
        selection = session.get(IntegrationSelectionRow, body.selection_id)
        if selection is None or selection.run_id != run_id:
            raise IntegrationError("selection_stale", "selection not found for run")
        if selection.selection_hash != body.selection_hash:
            raise IntegrationError("selection_stale", "selection hash mismatch")

        from agentforge.integration.authority import (
            assert_run_deliverable,
            assert_selection_current,
        )

        try:
            assert_run_deliverable(session, run)
            assert_selection_current(session, run, selection)
        except ValueError as exc:
            raise IntegrationError("selection_stale", str(exc)) from exc
        existing = session.scalar(
            select(IntegrationBuildRow).where(
                IntegrationBuildRow.run_id == run_id,
                IntegrationBuildRow.operation_key == body.idempotency_key,
            )
        )
        request_hash = sha256_hex(
            {
                "selectionId": body.selection_id,
                "selectionHash": body.selection_hash,
                "idempotencyKey": body.idempotency_key,
            }
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IntegrationError(
                    "integration_conflict",
                    "idempotency key reused with different build request",
                )
            return _to_view(existing)

        build_id = _id("ibld")
        claim = uuid.uuid4().hex
        workspace = build_workspace_root(settings.state_dir, run_id=run_id, build_id=build_id)
        now = _now(session)
        row = IntegrationBuildRow(
            id=build_id,
            run_id=run_id,
            selection_id=selection.id,
            operation_key=body.idempotency_key,
            request_hash=request_hash,
            status="accepted",
            workspace_ref=str(workspace),
            claim_token=claim,
            result_version_id=None,
            error_code=None,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        delivery = session.get(RunDeliveryRow, run_id)
        if delivery is None:
            session.add(
                RunDeliveryRow(
                    run_id=run_id,
                    current_version_id=None,
                    current_manifest_id=None,
                    delivery_revision=1,
                    technical_status="building",
                    acceptance_status="none",
                    updated_at=now,
                )
            )
        else:
            delivery.technical_status = "building"
            delivery.updated_at = now
        append_domain_fact(
            session,
            run_id,
            "integration.build_requested",
            {
                "buildId": build_id,
                "selectionId": selection.id,
                "selectionHash": selection.selection_hash,
                "status": "accepted",
            },
        )
        session.add(
            Outbox(
                id=_id("obx"),
                run_id=run_id,
                intent_type="integration_build",
                target="integration_build",
                payload={"operationId": build_id, "buildId": build_id, "runId": run_id},
                idempotency_key=f"integration_build:{build_id}",
                delivery_status="pending",
                attempt_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        return _to_view(row)


def process_integration_build(
    engine: Engine, settings: Settings, *, build_id: str
) -> IntegrationBuildView:
    """Commit dispatch before filesystem I/O; unknown results are never re-executed."""
    store = ArtifactStore(settings.state_dir / "artifact_store")
    from agentforge.integration.authority import assert_run_deliverable, assert_selection_current
    from agentforge.integration.baseline import read_git_baseline

    with unit_of_work_connection(engine) as uow:
        session = uow.session
        hint = session.get(IntegrationBuildRow, build_id)
        if hint is None:
            raise IntegrationError("build_not_found", build_id)
        run = session.get(Run, hint.run_id, with_for_update=True, populate_existing=True)
        build = session.get(
            IntegrationBuildRow, build_id, with_for_update=True, populate_existing=True
        )
        assert run is not None and build is not None
        if build.status != "accepted":
            return _to_view(build)
        selection = session.get(IntegrationSelectionRow, build.selection_id)
        try:
            assert_run_deliverable(session, run)
            plan_row = assert_selection_current(session, run, selection)
        except ValueError:
            build.status = "failed"
            build.error_code = "selection_stale"
            return _to_view(build)
        assert selection is not None
        baseline_row = session.get(ProjectBaselineRow, selection.baseline_ref)
        project = session.get(Project, run.project_id)
        grant = session.get(DirectoryGrantRow, project.directory_grant_id) if project else None
        if (
            baseline_row is None
            or not baseline_row.git_commit
            or grant is None
            or grant.status != "active"
        ):
            raise IntegrationError(
                "baseline_unavailable", "A fixed authorized Git baseline is required"
            )
        if baseline_row.dirty_policy == "managed_dirty_snapshot":
            # The legacy selection did not pin a dirty snapshot ID. Never follow latest.
            raise IntegrationError(
                "baseline_unavailable",
                "Pin an explicit dirty snapshot before integrating this baseline",
            )
        candidates = []
        for binding in selection.source_bindings or []:
            candidate = session.get(ArtifactCandidateRow, binding.get("candidateId"))
            if candidate is None or candidate.content_hash != binding.get("contentHash"):
                raise IntegrationError("selection_stale", "Candidate content changed")
            if binding.get("treeHash"):
                raise IntegrationError(
                    "release_scope_mismatch", "A whole-tree release needs a verified tree contract"
                )
            candidates.append((candidate.id, candidate.relative_path, candidate.content_hash))
        fixed: dict[str, Any] = dict(
            run_id=run.id,
            plan_version=run.plan_version,
            assignment_version=run.assignment_version,
            generation=run.active_generation,
            selection_id=selection.id,
            selection_hash=selection.selection_hash,
            baseline_hash=selection.baseline_tree_hash,
            source=grant.normalized_root,
            commit=baseline_row.git_commit,
            contract_hash=plan_row.contract_hash,
        )
        workspace = Path(build.workspace_ref or "")
        token = uuid.uuid4().hex
        build.claim_token = token
        build.status = "unknown"
        build.updated_at = _now(session)
        session.flush()
    try:
        baseline = read_git_baseline(fixed["source"], fixed["commit"], store)
        deltas = []
        for candidate_id, path, digest in candidates:
            # A released file only changes its approved path; other baseline files survive.
            candidate_tree = {
                **baseline,
                path: TreeEntry(
                    content_hash=digest, mode=baseline[path].mode if path in baseline else "100644"
                ),
            }
            deltas.append(compute_tree_delta(baseline, candidate_tree, source_ref=candidate_id))
        merge = combine_approved_deltas(baseline, tuple(deltas))
        write_build_marker(
            workspace,
            build_id=build_id,
            run_id=fixed["run_id"],
            selection_id=fixed["selection_id"],
            selection_hash=fixed["selection_hash"],
            baseline_tree_hash=fixed["baseline_hash"],
            claim_token=token,
        )
        result = materialize_merge_plan(
            merge, base=baseline, destination=workspace, object_store=store
        )
    except Exception as exc:
        with unit_of_work_connection(engine) as uow:
            row = uow.session.get(IntegrationBuildRow, build_id, with_for_update=True)
            if row and row.claim_token == token:
                row.error_code = "integration_outcome_unknown"
        raise IntegrationError("integration_outcome_unknown", type(exc).__name__) from exc
    with unit_of_work_connection(engine) as uow:
        session = uow.session
        run = session.get(Run, fixed["run_id"], with_for_update=True, populate_existing=True)
        build = session.get(
            IntegrationBuildRow, build_id, with_for_update=True, populate_existing=True
        )
        assert run is not None and build is not None
        if build.claim_token != token:
            return _to_view(build)
        if (run.plan_version, run.assignment_version, run.active_generation) != (
            fixed["plan_version"],
            fixed["assignment_version"],
            fixed["generation"],
        ):
            build.error_code = "stale_result"
            return _to_view(build)
        selection = session.get(IntegrationSelectionRow, fixed["selection_id"])
        try:
            assert_run_deliverable(session, run)
            assert_selection_current(session, run, selection)
        except ValueError:
            build.error_code = "selection_stale"
            return _to_view(build)
        build.status = result.status if result.status in {"succeeded", "conflicted"} else "failed"
        build.error_code = result.error_code
        if result.status == "succeeded":
            version_number = (
                int(
                    session.scalar(
                        select(
                            func.coalesce(func.max(IntegrationVersionRow.version_number), 0)
                        ).where(IntegrationVersionRow.run_id == run.id)
                    )
                    or 0
                )
                + 1
            )
            version_id = _id("iver")
            session.add(
                IntegrationVersionRow(
                    id=version_id,
                    run_id=run.id,
                    input_candidate_ids=[c[0] for c in candidates],
                    commit_or_tree=None,
                    content_hash=result.content_hash,
                    environment_ref="local",
                    selection_id=fixed["selection_id"],
                    selection_hash=fixed["selection_hash"],
                    version_number=version_number,
                    tree_manifest_hash=result.tree_manifest_hash,
                    baseline_hash=fixed["baseline_hash"],
                    contract_hash=fixed["contract_hash"],
                    input_versions_hash=sha256_hex({"candidates": candidates}),
                    build_environment_ref="local",
                )
            )
            build.result_version_id = version_id
            from agentforge.delivery.repository import (
                get_or_create_delivery,
                protect_content_object,
            )

            delivery = get_or_create_delivery(session, run.id)
            delivery.current_version_id = version_id
            delivery.current_manifest_id = None
            delivery.acceptance_status = "none"
            delivery.technical_status = "checking"
            delivery.delivery_revision += 1
            for digest in [
                result.content_hash,
                *[entry.content_hash for entry in result.tree_manifest.values()],
            ]:
                protect_content_object(
                    session,
                    content_hash=digest,
                    size_bytes=len(store.get_bytes(digest)),
                    protect_hours=24 * 365,
                )
        append_domain_fact(
            session,
            run.id,
            "integration.conflicted" if result.status == "conflicted" else "integration.built",
            {
                "buildId": build.id,
                "selectionId": fixed["selection_id"],
                "selectionHash": fixed["selection_hash"],
                "status": build.status,
                "versionId": build.result_version_id,
                "errorCode": build.error_code,
            },
        )
        session.flush()
        return _to_view(build)


def reconcile_integration_build(
    engine: Engine,
    settings: Settings,
    *,
    build_id: str,
) -> IntegrationBuildView:
    """Phase 3 helper: inspect workspace after crash; do not invent a new workspace."""
    with unit_of_work_connection(engine) as uow:
        session = uow.session
        build = session.get(IntegrationBuildRow, build_id, with_for_update=True)
        if build is None:
            raise IntegrationError("build_not_found", build_id)
        if build.status in {"succeeded", "conflicted", "failed"}:
            return _to_view(build)
        selection = session.get(IntegrationSelectionRow, build.selection_id)
        if selection is None or not build.workspace_ref:
            build.status = "needs_reconciliation"
            build.error_code = "workspace_identity_unknown"
            build.updated_at = _now(session)
            session.flush()
            return _to_view(build)
        try:
            verify_workspace_binding(
                Path(build.workspace_ref),
                build_id=build.id,
                run_id=build.run_id,
                selection_id=selection.id,
                selection_hash=selection.selection_hash,
                claim_token=build.claim_token,
            )
        except WorkspaceError as exc:
            build.status = "needs_reconciliation"
            build.error_code = exc.code
            build.updated_at = _now(session)
            session.flush()
            return _to_view(build)
        # Workspace identity OK but outcome unknown — leave for recovery; do not re-randomize.
        if build.status == "running":
            build.status = "unknown"
            build.error_code = "integration_outcome_unknown"
            build.updated_at = _now(session)
            session.flush()
        return _to_view(build)
