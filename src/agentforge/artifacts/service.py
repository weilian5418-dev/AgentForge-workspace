"""Artifact bus: freeze fixed snapshot → trusted verify → publish ≠ stage release (D06)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.artifacts.store import ArtifactStore, ArtifactStoreError
from agentforge.artifacts.validators import ValidatorRegistry, default_registry
from agentforge.contracts.artifact_bus import (
    CandidateView,
    ConsumeCheckResponse,
    FreezeCandidateRequest,
    GcRequest,
    GcResult,
    PublicationView,
    PublishRequest,
    ValidationReportView,
    VerifyCandidateRequest,
)
from agentforge.runner.paths import RunnerPathError, resolve_under_workspace, resolve_workspace_root
from agentforge.storage.models import (
    ArtifactCandidateRow,
    ArtifactPublicationRow,
    ArtifactValidationReportRow,
    ContentObjectRow,
    GitDirtySnapshotRow,
    GitIntegrationRow,
    GitWorkspaceSnapshotRow,
    RunnerOperationRow,
)


class ArtifactBusError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


def _sha_json(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ArtifactService:
    def __init__(
        self,
        session: Session,
        store: ArtifactStore,
        registry: ValidatorRegistry | None = None,
    ) -> None:
        self.session = session
        self.store = store
        self.registry = registry or default_registry()

    def freeze_candidate(self, body: FreezeCandidateRequest) -> CandidateView:
        """Copy live file into immutable store AFTER stop evidence is trusted — then bind hashes."""
        self._assert_stop_evidence(body)
        try:
            root = resolve_workspace_root(body.workspace_root)
            src = resolve_under_workspace(root, body.relative_path)
        except RunnerPathError as exc:
            raise ArtifactBusError(exc.code, exc.message) from exc
        if not src.is_file():
            raise ArtifactBusError("not_a_file", "Freeze source is not a file")

        # Evidence file must exist and match claimed hash (covers writable scope path).
        try:
            evidence_path = resolve_under_workspace(root, body.stop_evidence.evidence_path)
        except RunnerPathError as exc:
            raise ArtifactBusError(exc.code, exc.message) from exc
        if not evidence_path.is_file():
            raise ArtifactBusError("stop_evidence_missing", "Stop evidence file missing")
        evidence_bytes = evidence_path.read_bytes()
        evidence_digest = hashlib.sha256(evidence_bytes).hexdigest()
        if evidence_digest != body.stop_evidence.evidence_sha256:
            raise ArtifactBusError(
                "stop_evidence_mismatch",
                "Stop evidence sha256 does not match trusted claim",
            )

        try:
            content_hash = self.store.put_file(src)
            data = self.store.get_bytes(content_hash)
        except ArtifactStoreError as exc:
            raise ArtifactBusError(exc.code, exc.message) from exc

        contract_hash = _sha_json(
            {
                "contractId": body.contract_id,
                "contractVersion": body.contract_version,
                "relativePath": body.relative_path,
                "kind": body.kind,
            }
        )
        manifest_hash = _sha_json(
            {
                "contentHash": content_hash,
                "contractHash": contract_hash,
                "environmentHash": body.environment_hash,
                "relativePath": body.relative_path,
                "size": len(data),
            }
        )

        self._upsert_content_object(content_hash, size=len(data), protect_hours=24)

        row = ArtifactCandidateRow(
            id=f"cand_{uuid.uuid4().hex[:16]}",
            project_id=body.project_id,
            workspace_id=body.workspace_id,
            relative_path=body.relative_path,
            kind=body.kind,
            content_hash=content_hash,
            manifest_hash=manifest_hash,
            contract_id=body.contract_id,
            contract_version=body.contract_version,
            contract_hash=contract_hash,
            environment_hash=body.environment_hash,
            state="frozen",
            operation_id=body.stop_evidence.operation_id,
            attempt_id=body.stop_evidence.attempt_id,
            generation=body.stop_evidence.generation,
            stop_evidence_json=body.stop_evidence.model_dump(mode="json", by_alias=True),
            created_at=_now(),
            updated_at=_now(),
        )
        self.session.add(row)
        self.session.flush()
        return self._candidate_view(row)

    def verify(self, body: VerifyCandidateRequest) -> ValidationReportView:
        row = self.session.get(ArtifactCandidateRow, body.candidate_id)
        if row is None:
            raise ArtifactBusError("candidate_not_found", "Candidate not found")
        if row.state not in {"frozen", "validating", "verified", "rejected"}:
            raise ArtifactBusError("invalid_state", f"Cannot verify state={row.state}")

        # Re-check frozen object still present and matches — never re-read mutable workspace.
        if not self.store.verify(row.content_hash):
            row.state = "rejected"
            row.updated_at = _now()
            self.session.flush()
            raise ArtifactBusError(
                "snapshot_invalid",
                "Frozen content hash no longer verifies in immutable store",
            )

        try:
            validator = self.registry.get(body.validator_id)
        except KeyError as exc:
            raise ArtifactBusError("validator_not_found", str(exc)) from exc

        row.state = "validating"
        row.updated_at = _now()
        self.session.flush()

        outcome = validator.run(
            store=self.store,
            content_hash=row.content_hash,
            meta={
                "contractHash": row.contract_hash,
                "environmentHash": row.environment_hash,
                "candidateId": row.id,
            },
        )
        report = ArtifactValidationReportRow(
            id=f"vrep_{uuid.uuid4().hex[:16]}",
            candidate_id=row.id,
            validator_id=validator.spec.validator_id,
            validator_version=validator.spec.version,
            status=outcome.status,
            content_hash=row.content_hash,
            contract_hash=row.contract_hash,
            environment_hash=row.environment_hash,
            evidence_refs=list(outcome.evidence_refs),
            message=outcome.message,
            source="validator_registry",
            created_at=_now(),
        )
        self.session.add(report)
        if outcome.status == "passed":
            row.state = "verified"
        elif outcome.status == "not_run":
            row.state = "frozen"
        else:
            row.state = "rejected"
        row.updated_at = _now()
        self.session.flush()
        return ValidationReportView(
            report_id=report.id,
            candidate_id=row.id,
            validator_id=report.validator_id,
            validator_version=report.validator_version,
            status=report.status,  # type: ignore[arg-type]
            content_hash=report.content_hash,
            contract_hash=report.contract_hash,
            environment_hash=report.environment_hash,
            evidence_refs=[str(x) for x in report.evidence_refs],
            message=report.message,
            created_at=report.created_at,
        )

    def publish(self, body: PublishRequest) -> PublicationView:
        existing = self.session.scalar(
            select(ArtifactPublicationRow).where(
                ArtifactPublicationRow.idempotency_key == body.idempotency_key
            )
        )
        if existing is not None:
            cand = self.session.get(ArtifactCandidateRow, existing.candidate_id)
            if cand is None or cand.id != body.candidate_id:
                raise ArtifactBusError(
                    "idempotency_conflict",
                    "Idempotency key reused for a different candidate",
                )
            if cand.content_hash != existing.content_hash:
                raise ArtifactBusError(
                    "idempotency_conflict",
                    "Idempotency key reused with different content",
                )
            return self._publication_view(existing)

        row = self.session.get(ArtifactCandidateRow, body.candidate_id)
        if row is None:
            raise ArtifactBusError("candidate_not_found", "Candidate not found")
        if row.state != "verified":
            raise ArtifactBusError(
                "not_verified",
                "Only verified fixed candidates may be published",
            )

        # Re-validate hashes immediately before publish registration.
        if not self.store.verify(row.content_hash):
            row.state = "publish_failed"
            row.updated_at = _now()
            self.session.flush()
            raise ArtifactBusError("snapshot_invalid", "Content hash failed pre-publish check")

        passed = self.session.scalars(
            select(ArtifactValidationReportRow).where(
                ArtifactValidationReportRow.candidate_id == row.id,
                ArtifactValidationReportRow.status == "passed",
                ArtifactValidationReportRow.content_hash == row.content_hash,
                ArtifactValidationReportRow.contract_hash == row.contract_hash,
                ArtifactValidationReportRow.environment_hash == row.environment_hash,
                ArtifactValidationReportRow.source == "validator_registry",
            )
        ).all()
        if not passed:
            raise ArtifactBusError(
                "report_mismatch",
                "No trusted passed report bound to current candidate hashes",
            )

        artifact_id = body.artifact_id or f"art_{uuid.uuid4().hex[:16]}"
        version = self._next_version(artifact_id)
        pub = ArtifactPublicationRow(
            id=f"pub_{uuid.uuid4().hex[:16]}",
            candidate_id=row.id,
            artifact_id=artifact_id,
            version=version,
            content_hash=row.content_hash,
            manifest_hash=row.manifest_hash,
            idempotency_key=body.idempotency_key,
            stage_released=False,
            created_at=_now(),
        )
        self.session.add(pub)
        row.state = "published"
        row.updated_at = _now()
        # Protect content during publish window.
        self._protect_content(row.content_hash, hours=72)
        self.session.flush()
        return self._publication_view(pub)

    def can_consume(self, artifact_id: str, version: int) -> ConsumeCheckResponse:
        pub = self.session.scalar(
            select(ArtifactPublicationRow).where(
                ArtifactPublicationRow.artifact_id == artifact_id,
                ArtifactPublicationRow.version == version,
            )
        )
        if pub is None:
            return ConsumeCheckResponse(
                artifact_id=artifact_id,
                version=version,
                allowed=False,
                reason="publication_not_found",
            )
        # D10 gate not integrated — never default to released.
        if not pub.stage_released:
            return ConsumeCheckResponse(
                artifact_id=artifact_id,
                version=version,
                allowed=False,
                reason="stage_not_released_d10_required",
            )
        if not self.store.has(pub.content_hash):
            return ConsumeCheckResponse(
                artifact_id=artifact_id,
                version=version,
                allowed=False,
                reason="object_missing",
            )
        return ConsumeCheckResponse(
            artifact_id=artifact_id,
            version=version,
            allowed=True,
            reason="ok",
        )

    def get_candidate(self, candidate_id: str) -> CandidateView:
        row = self.session.get(ArtifactCandidateRow, candidate_id)
        if row is None:
            raise ArtifactBusError("candidate_not_found", "Candidate not found")
        return self._candidate_view(row)

    def read_content_for_view(self, candidate_id: str) -> bytes:
        """Viewable content for authorized callers — does not imply consumable."""
        row = self.session.get(ArtifactCandidateRow, candidate_id)
        if row is None:
            raise ArtifactBusError("candidate_not_found", "Candidate not found")
        try:
            return self.store.get_bytes(row.content_hash)
        except ArtifactStoreError as exc:
            raise ArtifactBusError(exc.code, exc.message) from exc

    def gc(self, body: GcRequest) -> GcResult:
        now = _now()
        grace = timedelta(seconds=body.grace_seconds)
        referenced = self._referenced_hashes()
        marked: list[str] = []
        deleted: list[str] = []
        skipped: list[str] = []

        objects = list(self.session.scalars(select(ContentObjectRow)).all())
        for obj in objects:
            if obj.content_hash in referenced:
                skipped.append(obj.content_hash)
                continue
            if obj.gc_protect_until and obj.gc_protect_until > now:
                skipped.append(obj.content_hash)
                continue
            if obj.gc_marked_at is None:
                obj.gc_marked_at = now
                marked.append(obj.content_hash)
                continue
            if obj.gc_marked_at + grace > now:
                skipped.append(obj.content_hash)
                continue
            # Recheck references between mark and delete (publish race).
            if obj.content_hash in self._referenced_hashes():
                obj.gc_marked_at = None
                skipped.append(obj.content_hash)
                continue
            if body.dry_run:
                marked.append(obj.content_hash)
                continue
            self.store.delete(obj.content_hash)
            self.session.delete(obj)
            deleted.append(obj.content_hash)
        self.session.flush()
        return GcResult(
            marked=marked,
            deleted=deleted,
            skipped_protected=skipped,
            dry_run=body.dry_run,
        )

    # --- helpers ---

    def _assert_stop_evidence(self, body: FreezeCandidateRequest) -> None:
        claim = body.stop_evidence
        if claim.source != "runner":
            raise ArtifactBusError(
                "untrusted_stop_source",
                "Stop evidence source must be trusted runner",
            )
        op = self.session.get(RunnerOperationRow, claim.operation_id)
        if op is None:
            raise ArtifactBusError(
                "untrusted_stop_source",
                "Stop evidence operationId not found in runner ledger",
            )
        if op.status not in {"succeeded", "failed", "blocked"}:
            raise ArtifactBusError(
                "stop_not_terminal",
                "Runner operation is not terminal; freeze refused",
            )
        if op.generation != claim.generation:
            raise ArtifactBusError(
                "generation_mismatch",
                "Stop evidence generation does not match runner operation",
            )
        if claim.attempt_id and op.attempt_id and claim.attempt_id != op.attempt_id:
            raise ArtifactBusError(
                "attempt_mismatch",
                "Stop evidence attemptId does not match runner operation",
            )

    def _upsert_content_object(self, content_hash: str, *, size: int, protect_hours: int) -> None:
        existing = self.session.get(ContentObjectRow, content_hash)
        if existing is None:
            self.session.add(
                ContentObjectRow(
                    content_hash=content_hash,
                    size_bytes=size,
                    created_at=_now(),
                    gc_marked_at=None,
                    gc_protect_until=_now() + timedelta(hours=protect_hours),
                )
            )
        else:
            existing.gc_marked_at = None
            existing.gc_protect_until = _now() + timedelta(hours=protect_hours)

    def _protect_content(self, content_hash: str, *, hours: int) -> None:
        obj = self.session.get(ContentObjectRow, content_hash)
        if obj is not None:
            obj.gc_marked_at = None
            obj.gc_protect_until = _now() + timedelta(hours=hours)

    def _referenced_hashes(self) -> set[str]:
        hashes: set[str] = set()
        for row in self.session.scalars(select(ArtifactCandidateRow)).all():
            hashes.add(row.content_hash)
        for row in self.session.scalars(select(ArtifactPublicationRow)).all():
            hashes.add(row.content_hash)
        for row in self.session.scalars(select(GitDirtySnapshotRow)).all():
            hashes.add(row.content_hash)
        for row in self.session.scalars(select(GitWorkspaceSnapshotRow)).all():
            hashes.add(row.content_hash)
        for row in self.session.scalars(select(GitIntegrationRow)).all():
            if row.content_hash:
                hashes.add(row.content_hash)
        return hashes

    def _next_version(self, artifact_id: str) -> int:
        rows = list(
            self.session.scalars(
                select(ArtifactPublicationRow).where(
                    ArtifactPublicationRow.artifact_id == artifact_id
                )
            ).all()
        )
        if not rows:
            return 1
        return max(r.version for r in rows) + 1

    def _candidate_view(self, row: ArtifactCandidateRow) -> CandidateView:
        return CandidateView(
            candidate_id=row.id,
            project_id=row.project_id,
            workspace_id=row.workspace_id,
            relative_path=row.relative_path,
            content_hash=row.content_hash,
            manifest_hash=row.manifest_hash,
            contract_hash=row.contract_hash,
            environment_hash=row.environment_hash,
            state=row.state,  # type: ignore[arg-type]
            operation_id=row.operation_id,
            generation=row.generation,
            created_at=row.created_at,
        )

    def _publication_view(self, row: ArtifactPublicationRow) -> PublicationView:
        return PublicationView(
            publication_id=row.id,
            candidate_id=row.candidate_id,
            artifact_id=row.artifact_id,
            version=row.version,
            content_hash=row.content_hash,
            manifest_hash=row.manifest_hash,
            state="published",
            stage_released=bool(row.stage_released),
            created_at=row.created_at,
        )
