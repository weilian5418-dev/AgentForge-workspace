"""Build structured HandoffManifest for new-session handoff (not resume)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from agentforge.contracts.recovery import HandoffManifest
from agentforge.driver.binding import sha256_hex
from agentforge.recovery.errors import RecoveryError
from agentforge.storage.models import (
    ArtifactCandidateRow,
    InstanceAttemptRow,
    InstanceOperationRow,
    ReleaseRecordRow,
    Run,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def build_handoff_manifest(
    session: Session,
    *,
    run_id: str,
    source_attempt_id: str,
    remaining_objective: str | None = None,
    stop_evidence_refs: list[str] | None = None,
    uncertain_side_effect_refs: list[str] | None = None,
    uncertain_budget_refs: list[str] | None = None,
    required_reauth: list[str] | None = None,
) -> HandoffManifest:
    run = session.get(Run, run_id)
    if run is None:
        raise RecoveryError("run_not_found", run_id)
    if run.plan_version is None or run.assignment_version is None:
        raise RecoveryError("execution_blocked", "plan/assignment required for handoff")

    attempt = session.get(InstanceAttemptRow, source_attempt_id)
    if attempt is None or attempt.run_id != run_id:
        raise RecoveryError("attempt_not_found", source_attempt_id)
    if attempt.status not in {"stopped", "failed", "superseded", "canceled"}:
        # Must prove stop before handoff package authorizes a new writable attempt.
        if attempt.stop_verified_at is None and attempt.status not in {"stopped"}:
            raise RecoveryError(
                "pending_stop_verification",
                "handoff requires verified stop on source attempt",
            )

    release_ids: list[str] = []
    candidate_refs: list[str] = []
    candidate_hashes: list[str] = []
    if attempt.stage_id:
        from sqlalchemy import select

        for release in session.scalars(
            select(ReleaseRecordRow).where(ReleaseRecordRow.stage_id == attempt.stage_id)
        ).all():
            release_ids.append(release.id)
            for raw in release.candidate_versions or []:
                if not isinstance(raw, dict):
                    continue
                cid = raw.get("candidateId") or raw.get("candidate_id")
                ch = raw.get("contentHash") or raw.get("content_hash")
                if cid:
                    candidate_refs.append(str(cid))
                if ch:
                    candidate_hashes.append(str(ch))
                elif cid:
                    cand = session.get(ArtifactCandidateRow, str(cid))
                    if cand is not None:
                        candidate_hashes.append(cand.content_hash)

    unknown_ops = []
    from sqlalchemy import select

    for op in session.scalars(
        select(InstanceOperationRow).where(
            InstanceOperationRow.instance_id == attempt.instance_id,
            InstanceOperationRow.status == "unknown",
        )
    ).all():
        unknown_ops.append(op.id)

    body: dict[str, Any] = {
        "runId": run.id,
        "planVersion": run.plan_version,
        "assignmentVersion": run.assignment_version,
        "stageId": attempt.stage_id,
        "workPackageId": attempt.work_package_id,
        "sourceAttemptId": attempt.id,
        "sourceInstanceId": attempt.instance_id,
        "sourceNativeSessionId": attempt.native_session_id,
        "sourceWorkspaceId": attempt.workspace_id,
        "stopEvidenceRefs": list(stop_evidence_refs or []),
        "inputReleaseRefs": sorted(set(release_ids)),
        "inputCandidateRefs": sorted(set(candidate_refs)),
        "candidateHashes": sorted(set(candidate_hashes)),
        "uncertainSideEffectRefs": sorted(set((uncertain_side_effect_refs or []) + unknown_ops)),
        "uncertainBudgetRefs": list(uncertain_budget_refs or []),
        "requiredReauth": list(required_reauth or []),
        "remainingObjective": remaining_objective,
        "strategyVersion": "d11.1",
    }
    digest = sha256_hex(body)
    handoff_id = _id("hof")
    return HandoffManifest.model_validate(
        {
            **{
                "handoffId": handoff_id,
                "handoffHash": digest,
                "createdAt": _now(),
                "completedRefs": [],
                "pendingReviewRefs": [],
                "evidenceRefs": list(stop_evidence_refs or []),
            },
            **body,
        }
    )
