"""Resolve upstream ReleaseRecord members into a bounded context bundle (D11.7)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.driver.binding import sha256_hex
from agentforge.instances.errors import InstanceError
from agentforge.storage.models import (
    ArtifactCandidateRow,
    ArtifactPublicationRow,
    Project,
    ReleaseRecordRow,
    Run,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class ContextBundleMember:
    release_id: str
    work_package_id: str
    candidate_id: str
    content_hash: str
    contract_id: str
    contract_version: int
    contract_hash: str
    artifact_version: int | None = None


@dataclass
class ContextBundle:
    bundle_id: str
    run_id: str
    project_id: str
    release_refs: list[str] = field(default_factory=list)
    candidate_refs: list[str] = field(default_factory=list)
    members: list[ContextBundleMember] = field(default_factory=list)
    digest: str = ""
    created_at: datetime = field(default_factory=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "bundleId": self.bundle_id,
            "runId": self.run_id,
            "projectId": self.project_id,
            "releaseRefs": list(self.release_refs),
            "candidateRefs": list(self.candidate_refs),
            "members": [
                {
                    "releaseId": m.release_id,
                    "workPackageId": m.work_package_id,
                    "candidateId": m.candidate_id,
                    "contentHash": m.content_hash,
                    "contractId": m.contract_id,
                    "contractVersion": m.contract_version,
                    "contractHash": m.contract_hash,
                    "artifactVersion": m.artifact_version,
                }
                for m in self.members
            ],
            "digest": self.digest,
            "createdAt": self.created_at.isoformat(),
        }


class ReleasedInputResolver:
    """Platform-only resolver: publication ≠ D10 release; ReleaseRecord is authority."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def resolve(
        self,
        *,
        run_id: str,
        release_ids: list[str],
        required_work_package_ids: set[str] | None = None,
    ) -> ContextBundle:
        run = self.session.get(Run, run_id)
        if run is None:
            raise InstanceError("run_not_found", run_id)
        project = self.session.get(Project, run.project_id)
        if project is None:
            raise InstanceError("project_not_found", run.project_id)

        members: list[ContextBundleMember] = []
        candidate_refs: list[str] = []
        covered: set[str] = set()

        for release_id in release_ids:
            release = self.session.get(ReleaseRecordRow, release_id)
            if release is None:
                raise InstanceError("release_not_found", release_id)
            for raw in release.candidate_versions or []:
                member = self._member_from_release(release, raw, project_id=project.id)
                if (
                    required_work_package_ids
                    and member.work_package_id not in required_work_package_ids
                ):
                    continue
                members.append(member)
                candidate_refs.append(member.candidate_id)
                covered.add(member.work_package_id)

        if required_work_package_ids and not required_work_package_ids <= covered:
            missing = sorted(required_work_package_ids - covered)
            raise InstanceError(
                "released_input_incomplete",
                f"missing released packages: {','.join(missing)}",
            )

        digest = sha256_hex(
            {
                "runId": run_id,
                "releases": sorted(release_ids),
                "candidates": sorted(candidate_refs),
                "hashes": sorted(m.content_hash for m in members),
            }
        )
        return ContextBundle(
            bundle_id=_id("ctx"),
            run_id=run_id,
            project_id=project.id,
            release_refs=list(release_ids),
            candidate_refs=candidate_refs,
            members=members,
            digest=digest,
        )

    def _member_from_release(
        self,
        release: ReleaseRecordRow,
        raw: dict[str, Any] | Any,
        *,
        project_id: str,
    ) -> ContextBundleMember:
        data = raw if isinstance(raw, dict) else {}
        candidate_id = str(data.get("candidateId") or data.get("candidate_id") or "")
        work_package_id = str(data.get("workPackageId") or data.get("work_package_id") or "")
        content_hash = data.get("contentHash") or data.get("content_hash")
        artifact_version = data.get("artifactVersion") or data.get("artifact_version")
        if not candidate_id or not work_package_id:
            raise InstanceError("release_member_invalid", release.id)

        candidate = self.session.get(ArtifactCandidateRow, candidate_id)
        if candidate is None:
            raise InstanceError("candidate_not_found", candidate_id)
        if candidate.project_id != project_id:
            raise InstanceError("candidate_wrong_project", candidate_id)
        if candidate.state in {"rejected", "superseded"}:
            raise InstanceError("candidate_not_consumable", candidate_id)
        if content_hash and candidate.content_hash != content_hash:
            raise InstanceError("candidate_hash_mismatch", candidate_id)

        # Strict consume gate: must appear on this ReleaseRecord (not merely published).
        if not self._candidate_on_release(release, candidate_id):
            raise InstanceError("candidate_not_released", candidate_id)

        return ContextBundleMember(
            release_id=release.id,
            work_package_id=work_package_id,
            candidate_id=candidate.id,
            content_hash=str(content_hash or candidate.content_hash),
            contract_id=candidate.contract_id,
            contract_version=int(candidate.contract_version),
            contract_hash=candidate.contract_hash,
            artifact_version=int(artifact_version) if artifact_version is not None else None,
        )

    @staticmethod
    def _candidate_on_release(release: ReleaseRecordRow, candidate_id: str) -> bool:
        for raw in release.candidate_versions or []:
            if not isinstance(raw, dict):
                continue
            cid = raw.get("candidateId") or raw.get("candidate_id")
            if cid == candidate_id:
                return True
        return False


def find_release_ids_for_upstream(session: Session, *, upstream_stage_ids: list[str]) -> list[str]:
    """Collect ReleaseRecord ids for upstream stages (latest facts; eligibility already gated)."""
    if not upstream_stage_ids:
        return []
    rows = list(
        session.scalars(
            select(ReleaseRecordRow).where(ReleaseRecordRow.stage_id.in_(upstream_stage_ids))
        ).all()
    )
    return [r.id for r in rows]


def publication_marked_released(session: Session, *, candidate_id: str) -> bool:
    """Denormalized publication flag — never sufficient alone for dispatch inputs."""
    pub = session.scalar(
        select(ArtifactPublicationRow).where(ArtifactPublicationRow.candidate_id == candidate_id)
    )
    return bool(pub and pub.stage_released)
