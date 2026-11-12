"""Fix released handoff inputs into an immutable integration selection (D12.8)."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.integrations import (
    IntegrationSelection,
    IntegrationSelectionRequest,
)
from agentforge.driver.binding import sha256_hex
from agentforge.integration.fingerprints import (
    DEFAULT_MERGE_POLICY_VERSION,
    build_selection_hash,
    canonicalize_binding,
)
from agentforge.queries.authorization import AuthenticatedPrincipal
from agentforge.storage.models import (
    ArtifactCandidateRow,
    GitDirtySnapshotRow,
    IntegrationSelectionRow,
    Plan,
    ProjectBaselineRow,
    ReleaseRecordRow,
    Run,
    SchedulerOperationRow,
    StageRow,
)
from agentforge.storage.uow import db_now

_CODE_SCOPE_KINDS = frozenset(
    {
        "code",
        "source_tree",
        "full_tree",
        "workspace_tree",
        "git_tree",
    }
)
_MUTABLE_CANDIDATE_STATES = frozenset({"draft", "mutable", "open", "writing"})


class SelectionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _to_view(row: IntegrationSelectionRow) -> IntegrationSelection:
    return IntegrationSelection.model_validate(
        {
            "selectionId": row.id,
            "runId": row.run_id,
            "handoffRef": row.handoff_ref,
            "handoffHash": row.handoff_hash,
            "baselineRef": row.baseline_ref,
            "baselineTreeHash": row.baseline_tree_hash,
            "sourceBindings": row.source_bindings,
            "mergePolicyVersion": row.merge_policy_version,
            "selectionHash": row.selection_hash,
            "createdBy": row.created_by,
            "createdAt": row.created_at,
        }
    )


def _load_handoff_op(
    session: Session, *, run_id: str, handoff_ref: str
) -> SchedulerOperationRow:
    ops = list(
        session.scalars(
            select(SchedulerOperationRow).where(
                SchedulerOperationRow.run_id == run_id,
                SchedulerOperationRow.kind == "integration_handoff",
            )
        ).all()
    )
    for op in ops:
        refs = list(op.result_refs or [])
        binding = dict(op.binding_json or {})
        if handoff_ref in refs or binding.get("handoffId") == handoff_ref:
            if op.status != "succeeded":
                raise SelectionError("selection_stale", f"handoff status is {op.status}")
            return op
    raise SelectionError("selection_stale", f"handoff {handoff_ref} not found for run")


def validate_tree_release_scope(
    *,
    scope_kind: str,
    member: dict[str, Any],
    candidate: ArtifactCandidateRow | None,
) -> None:
    """Block silent upgrade of single-file release to full-tree authority."""
    kind = (scope_kind or "").lower()
    candidate_kind = (candidate.kind if candidate is not None else "").lower()
    if kind in _CODE_SCOPE_KINDS or candidate_kind in _CODE_SCOPE_KINDS:
        tree_hash = member.get("treeHash") or member.get("tree_hash")
        snapshot_id = member.get("workspaceSnapshotId") or member.get("snapshotId")
        if not tree_hash and not (
            snapshot_id and str(snapshot_id).startswith(("gws_", "snap_", "tree_"))
        ):
            raise SelectionError(
                "release_scope_mismatch",
                "code/tree release requires fixed tree or workspace snapshot binding",
            )


def resolve_released_inputs(
    session: Session,
    *,
    run_id: str,
    release_record_ids: list[str],
) -> list[dict[str, Any]]:
    """Resolve ReleaseRecord members into source bindings — no client-invented snapshots."""
    bindings: list[dict[str, Any]] = []
    for release_id in release_record_ids:
        release = session.get(ReleaseRecordRow, release_id)
        if release is None:
            raise SelectionError("release_scope_mismatch", f"release {release_id} missing")
        stage = session.get(StageRow, release.stage_id)
        if stage is None or stage.run_id != run_id:
            raise SelectionError(
                "release_scope_mismatch",
                f"release {release_id} does not belong to run",
            )
        if release.shared_interface_still_blocked:
            raise SelectionError(
                "release_scope_mismatch",
                f"release {release_id} still has blocked shared interface",
            )
        deps = list(release.dependency_satisfied_refs or [])
        if any(d in {None, "", False} for d in deps):
            raise SelectionError(
                "release_scope_mismatch",
                f"release {release_id} has unsatisfied dependency refs",
            )

        members = list(release.candidate_versions or [])
        if not members:
            raise SelectionError(
                "release_scope_mismatch",
                f"release {release_id} has no candidate versions",
            )
        allowed_wp = {str(w) for w in (release.work_package_ids or [])}
        for member in members:
            wp = str(member.get("workPackageId") or "")
            if allowed_wp and wp and wp not in allowed_wp:
                raise SelectionError(
                    "release_scope_mismatch",
                    f"candidate work package {wp} outside release scope",
                )
            candidate_id = member.get("candidateId")
            content_hash = member.get("contentHash")
            if not candidate_id or not content_hash:
                raise SelectionError(
                    "release_scope_mismatch",
                    "candidate binding missing candidateId/contentHash",
                )
            candidate = session.get(ArtifactCandidateRow, str(candidate_id))
            if candidate is None:
                raise SelectionError(
                    "release_scope_mismatch",
                    f"candidate {candidate_id} missing",
                )
            if candidate.content_hash != content_hash:
                raise SelectionError(
                    "release_scope_mismatch",
                    f"candidate {candidate_id} content hash drift",
                )
            if candidate.state in _MUTABLE_CANDIDATE_STATES or candidate.state in {
                "rejected",
                "superseded",
            }:
                raise SelectionError(
                    "release_scope_mismatch",
                    f"candidate {candidate_id} is not immutable ({candidate.state})",
                )
            validate_tree_release_scope(
                scope_kind=release.scope_kind,
                member=member,
                candidate=candidate,
            )
            snapshot_id = (
                member.get("workspaceSnapshotId")
                or member.get("snapshotId")
                or candidate.id
            )
            tree_hash = member.get("treeHash") or member.get("tree_hash")
            bindings.append(
                canonicalize_binding(
                    {
                        "releaseId": release.id,
                        "candidateId": candidate.id,
                        "snapshotId": str(snapshot_id),
                        "workPackageId": wp or None,
                        "stageId": stage.id,
                        "contentHash": candidate.content_hash,
                        "treeHash": tree_hash,
                    }
                )
            )
    if not bindings:
        raise SelectionError("release_scope_mismatch", "no released inputs to select")
    return bindings


def _resolve_baseline_tree_hash(session: Session, run: Run) -> tuple[str | None, str]:
    """Fixed baseline only — refuse mutable HEAD."""
    if not run.baseline_id:
        raise SelectionError("selection_stale", "run has no fixed baseline")
    baseline = session.get(ProjectBaselineRow, run.baseline_id)
    if baseline is None:
        raise SelectionError("selection_stale", "baseline missing")
    if baseline.status not in {"ready", "confirmed", "active", "frozen", "captured"}:
        raise SelectionError(
            "selection_stale",
            f"baseline status {baseline.status} is not fixed",
        )
    if baseline.git_commit:
        commit = baseline.git_commit.strip().lower()
        if commit in {"head", "main", "master", "origin/main", "origin/master"}:
            raise SelectionError(
                "selection_stale",
                "mutable HEAD/branch ref cannot be integration baseline",
            )
    tree_hash = baseline.manifest_hash
    if not tree_hash and baseline.git_commit:
        if not re.fullmatch(r"[0-9a-f]{40}", baseline.git_commit.lower()):
            raise SelectionError(
                "selection_stale",
                "baseline git_commit is not a fixed object id",
            )
        tree_hash = sha256_hex({"gitCommit": baseline.git_commit.lower()})
    if not tree_hash or len(tree_hash) != 64:
        raise SelectionError(
            "selection_stale",
            "baseline lacks fixed tree/manifest hash",
        )
    if baseline.dirty_policy == "managed_dirty_snapshot":
        dirty = session.scalar(
            select(GitDirtySnapshotRow)
            .where(GitDirtySnapshotRow.baseline_id == baseline.id)
            .order_by(GitDirtySnapshotRow.created_at.desc())
            .limit(1)
        )
        if dirty is None:
            raise SelectionError(
                "selection_stale",
                "managed_dirty_snapshot baseline missing dirty snapshot",
            )
        tree_hash = sha256_hex(
            {
                "baselineTree": tree_hash,
                "dirtySnapshotId": dirty.id,
                "dirtyContentHash": dirty.content_hash,
                "dirtyManifestHash": dirty.manifest_hash,
            }
        )
    return baseline.id, tree_hash


def create_selection(
    session: Session,
    *,
    principal: AuthenticatedPrincipal,
    run_id: str,
    request: IntegrationSelectionRequest,
) -> IntegrationSelection:
    """Lock run, verify handoff + releases, persist immutable selection."""
    if not principal.credential_id:
        raise SelectionError("unauthorized", "principal required")
    run = session.get(Run, run_id, with_for_update=True)
    if run is None:
        raise SelectionError("run_not_found", run_id)
    if run.collaboration_state != "ready_for_integration":
        raise SelectionError(
            "selection_stale",
            f"collaboration_state is {run.collaboration_state}, not ready_for_integration",
        )

    existing = session.scalar(
        select(IntegrationSelectionRow).where(
            IntegrationSelectionRow.run_id == run_id,
            IntegrationSelectionRow.request_key == request.idempotency_key,
        )
    )

    op = _load_handoff_op(session, run_id=run_id, handoff_ref=request.handoff_ref)
    binding = dict(op.binding_json or {})
    expected_hash = binding.get("handoffHash") or op.request_hash
    if expected_hash != request.handoff_hash:
        raise SelectionError("selection_stale", "handoff hash mismatch")
    if binding.get("runId") and binding["runId"] != run_id:
        raise SelectionError("selection_stale", "handoff run mismatch")
    if binding.get("planVersion") is not None and binding["planVersion"] != run.plan_version:
        raise SelectionError("selection_stale", "plan version drifted since handoff")
    if (
        binding.get("assignmentVersion") is not None
        and binding["assignmentVersion"] != run.assignment_version
    ):
        raise SelectionError("selection_stale", "assignment version drifted since handoff")
    if (
        binding.get("executionGeneration") is not None
        and int(binding["executionGeneration"]) != int(run.active_generation)
    ):
        raise SelectionError("selection_stale", "execution generation drifted since handoff")

    release_ids = list(binding.get("releaseRecordIds") or [])
    if not release_ids:
        raise SelectionError("selection_stale", "handoff has no release records")

    source_bindings = resolve_released_inputs(
        session, run_id=run_id, release_record_ids=[str(r) for r in release_ids]
    )
    baseline_ref, baseline_tree_hash = _resolve_baseline_tree_hash(session, run)

    contract_hash = binding.get("contractHash") or ("0" * 64)
    if run.plan_version is not None:
        plan = session.get(Plan, (run.id, run.plan_version))
        if plan is not None and plan.contract_hash:
            contract_hash = plan.contract_hash

    merge_policy = request.merge_policy_ref or DEFAULT_MERGE_POLICY_VERSION
    selection_hash = build_selection_hash(
        run_id=run.id,
        plan_version=run.plan_version,
        assignment_version=run.assignment_version,
        gate_policy_version=run.gate_policy_version,
        active_generation=int(run.active_generation),
        handoff_ref=request.handoff_ref,
        handoff_hash=request.handoff_hash,
        baseline_ref=baseline_ref,
        baseline_tree_hash=baseline_tree_hash,
        source_bindings=source_bindings,
        merge_policy_version=merge_policy,
        contract_hash=contract_hash,
    )

    if existing is not None:
        if existing.selection_hash != selection_hash:
            raise SelectionError(
                "integration_conflict",
                "idempotency key reused with different selection content",
            )
        return _to_view(existing)

    row = IntegrationSelectionRow(
        id=_id("isel"),
        run_id=run.id,
        handoff_ref=request.handoff_ref,
        handoff_hash=request.handoff_hash,
        baseline_ref=baseline_ref,
        baseline_tree_hash=baseline_tree_hash,
        source_bindings=source_bindings,
        merge_policy_version=merge_policy,
        selection_hash=selection_hash,
        request_key=request.idempotency_key,
        created_by=principal.actor_ref,
        created_at=db_now(session),
    )
    session.add(row)
    session.flush()
    return _to_view(row)
