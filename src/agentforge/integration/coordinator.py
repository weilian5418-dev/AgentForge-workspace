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
    BuilderError,
    TreeEntry,
    TreeManifest,
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
    IntegrationBuildRow,
    IntegrationSelectionRow,
    IntegrationVersionRow,
    Outbox,
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
            return value.replace(tzinfo=UTC)  # type: ignore[union-attr]
        return value  # type: ignore[return-value]
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
            "workspaceRef": row.workspace_ref,
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
        workspace = build_workspace_root(
            settings.state_dir, run_id=run_id, build_id=build_id
        )
        write_build_marker(
            workspace,
            build_id=build_id,
            run_id=run_id,
            selection_id=selection.id,
            selection_hash=selection.selection_hash,
            baseline_tree_hash=selection.baseline_tree_hash,
            claim_token=claim,
        )
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
                payload={"buildId": build_id, "runId": run_id},
                idempotency_key=f"integration_build:{build_id}",
                delivery_status="pending",
                attempt_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        return _to_view(row)


def _baseline_manifest_from_selection(
    session: Session, selection: IntegrationSelectionRow, store: ArtifactStore
) -> TreeManifest:
    """Initial baseline manifest: empty or reconstructed from selection baseline hash only.

    File candidates supply deltas against this baseline; unchanged paths are not writes.
    """
    _ = store
    # Without a persisted baseline tree listing, start empty — file adds are 'add'.
    # Future: load from baseline snapshot object when available.
    _ = selection
    return {}


def _candidate_manifest(
    session: Session, binding: dict[str, Any]
) -> tuple[str, TreeManifest]:
    candidate_id = binding.get("candidateId")
    if not candidate_id:
        raise IntegrationError("selection_stale", "binding missing candidateId")
    candidate = session.get(ArtifactCandidateRow, str(candidate_id))
    if candidate is None:
        raise IntegrationError("selection_stale", f"candidate {candidate_id} missing")
    path = candidate.relative_path.replace("\\", "/").lstrip("/")
    manifest: TreeManifest = {
        path: TreeEntry(content_hash=candidate.content_hash, mode="100644")
    }
    # If treeHash present, this binding claims a full tree — require object later.
    if binding.get("treeHash"):
        raise IntegrationError(
            "blocked",
            "full-tree candidate materialization not enabled in this build path yet",
        )
    return str(candidate_id), manifest


def process_integration_build(
    engine: Engine,
    settings: Settings,
    *,
    build_id: str,
) -> IntegrationBuildView:
    """Phase 2: outside the accept transaction — merge deltas into fixed workspace."""
    store = ArtifactStore(settings.state_dir / "artifact_store")
    with unit_of_work_connection(engine) as uow:
        session = uow.session
        build = session.get(IntegrationBuildRow, build_id, with_for_update=True)
        if build is None:
            raise IntegrationError("build_not_found", build_id)
        if build.status in {"succeeded", "conflicted", "failed"}:
            return _to_view(build)
        if build.status not in {"accepted", "running", "unknown", "needs_reconciliation"}:
            raise IntegrationError("invalid_state", f"cannot process status {build.status}")

        selection = session.get(IntegrationSelectionRow, build.selection_id)
        if selection is None:
            build.status = "failed"
            build.error_code = "selection_stale"
            build.updated_at = _now(session)
            session.flush()
            return _to_view(build)

        workspace = Path(build.workspace_ref or "")
        try:
            verify_workspace_binding(
                workspace,
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

        build.status = "running"
        build.updated_at = _now(session)
        session.flush()

        # Capture immutable inputs for phase-2 work (still in txn for consistency of reads)
        bindings = list(selection.source_bindings or [])
        baseline = _baseline_manifest_from_selection(session, selection, store)
        deltas = []
        try:
            for binding in bindings:
                source_ref, cand_manifest = _candidate_manifest(session, binding)
                deltas.append(
                    compute_tree_delta(baseline, cand_manifest, source_ref=source_ref)
                )
            plan = combine_approved_deltas(baseline, tuple(deltas))
        except (BuilderError, IntegrationError) as exc:
            code = getattr(exc, "code", "failed")
            build.status = "failed"
            build.error_code = code
            build.updated_at = _now(session)
            append_domain_fact(
                session,
                build.run_id,
                "integration.conflicted" if code == "integration_conflict" else "integration.built",
                {
                    "buildId": build.id,
                    "selectionId": selection.id,
                    "selectionHash": selection.selection_hash,
                    "status": build.status,
                    "errorCode": code,
                },
            )
            session.flush()
            return _to_view(build)

        # Release DB before heavy IO — but we need to finish IO then reconcile in new txn.
        # Keep claim and do IO inside this UOW for initial implementation; mark unknown on crash.
        result = materialize_merge_plan(
            plan, base=baseline, destination=workspace, object_store=store
        )

        if result.status == "conflicted":
            build.status = "conflicted"
            build.error_code = result.error_code
            build.updated_at = _now(session)
            delivery = session.get(RunDeliveryRow, build.run_id)
            if delivery is not None:
                delivery.technical_status = "conflicted"
                delivery.updated_at = _now(session)
            append_domain_fact(
                session,
                build.run_id,
                "integration.conflicted",
                {
                    "buildId": build.id,
                    "selectionId": selection.id,
                    "selectionHash": selection.selection_hash,
                    "status": "conflicted",
                    "errorCode": result.error_code,
                },
            )
            session.flush()
            return _to_view(build)

        if result.status != "succeeded":
            build.status = "failed"
            build.error_code = result.error_code
            build.updated_at = _now(session)
            append_domain_fact(
                session,
                build.run_id,
                "integration.built",
                {
                    "buildId": build.id,
                    "selectionId": selection.id,
                    "selectionHash": selection.selection_hash,
                    "status": build.status,
                    "errorCode": result.error_code,
                },
            )
            session.flush()
            return _to_view(build)

        # Persist immutable version
        version_number = int(
            session.scalar(
                select(func.coalesce(func.max(IntegrationVersionRow.version_number), 0)).where(
                    IntegrationVersionRow.run_id == build.run_id
                )
            )
            or 0
        ) + 1
        version_id = _id("iver")
        candidate_ids = [
            str(b.get("candidateId"))
            for b in bindings
            if b.get("candidateId")
        ]
        session.add(
            IntegrationVersionRow(
                id=version_id,
                run_id=build.run_id,
                input_candidate_ids=candidate_ids,
                commit_or_tree=None,
                content_hash=result.content_hash,
                environment_ref="local",
                selection_id=selection.id,
                selection_hash=selection.selection_hash,
                version_number=version_number,
                tree_manifest_hash=result.tree_manifest_hash,
                baseline_hash=selection.baseline_tree_hash,
                contract_hash=None,
                input_versions_hash=sha256_hex({"candidates": sorted(candidate_ids)}),
                build_environment_ref="local",
                created_at=_now(session),
            )
        )
        build.status = "succeeded"
        build.result_version_id = version_id
        build.error_code = None
        build.updated_at = _now(session)
        delivery = session.get(RunDeliveryRow, build.run_id)
        if delivery is not None:
            delivery.current_version_id = version_id
            delivery.technical_status = "checking"
            delivery.delivery_revision = int(delivery.delivery_revision) + 1
            delivery.updated_at = _now(session)
        append_domain_fact(
            session,
            build.run_id,
            "integration.built",
            {
                "buildId": build.id,
                "selectionId": selection.id,
                "selectionHash": selection.selection_hash,
                "status": "succeeded",
                "versionId": version_id,
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
