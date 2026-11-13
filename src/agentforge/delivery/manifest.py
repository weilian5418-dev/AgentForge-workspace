"""Immutable delivery manifest hashing and publish (D12.12)."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.delivery import (
    DeliveryManifestPayload,
    DeliveryManifestView,
    ManifestCheckEntry,
    ManifestMember,
    ManifestReviewEntry,
    ManifestRisk,
)
from agentforge.delivery.repository import (
    get_or_create_delivery,
    next_manifest_revision,
    protect_content_object,
)
from agentforge.driver.binding import canonical_json
from agentforge.integration.reviews import ReviewError
from agentforge.integration.service import evaluate_integration_cycle
from agentforge.storage.models import (
    DeliveryManifestRow,
    IntegrationCheckReportRow,
    IntegrationCheckRunRow,
    IntegrationReviewReportRow,
    IntegrationReviewRequestRow,
    IntegrationSelectionRow,
    IntegrationValidationCycleRow,
    IntegrationVersionRow,
    Run,
)
from agentforge.storage.repositories.events import append_domain_fact
from agentforge.storage.uow import db_now, unit_of_work_connection


class ManifestError(Exception):
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


def canonical_manifest_bytes(payload: DeliveryManifestPayload) -> bytes:
    """UTF-8 JSON, sorted keys, compact separators — manifestHash field excluded."""
    data = payload.model_dump(mode="json", by_alias=True)
    # Ensure unordered collections are sorted by stable id for hash stability
    members = sorted(data.get("members") or [], key=lambda m: str(m.get("memberId") or ""))
    checks = sorted(
        data.get("checks") or [],
        key=lambda c: (str(c.get("checkId") or ""), str(c.get("configHash") or "")),
    )
    reviews = sorted(data.get("reviews") or [], key=lambda r: str(r.get("requestId") or ""))
    risks = sorted(
        data.get("knownRisks") or [],
        key=lambda r: (str(r.get("kind") or ""), str(r.get("detail") or "")),
    )
    data["members"] = members
    data["checks"] = checks
    data["reviews"] = reviews
    data["knownRisks"] = risks
    text = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return text.encode("utf-8")


def compute_manifest_hash(payload: DeliveryManifestPayload) -> str:
    return hashlib.sha256(canonical_manifest_bytes(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class ManifestBuildIntent:
    intent_id: str
    run_id: str
    version_id: str
    cycle_id: str
    revision: int
    payload: DeliveryManifestPayload
    manifest_hash: str
    payload_bytes: bytes
    expected_delivery_revision: int = 0
    expected_status_version: int = 0
    expected_plan_hash: str = ""


def build_delivery_manifest(
    session: Session,
    *,
    version_id: str,
    cycle_id: str,
) -> ManifestBuildIntent:
    """Assemble immutable payload from server facts — empty members/checks stay blocked."""
    version = session.get(IntegrationVersionRow, version_id)
    if version is None:
        raise ManifestError("version_not_found", version_id)
    cycle = session.get(IntegrationValidationCycleRow, cycle_id)
    if cycle is None or cycle.version_id != version_id:
        raise ManifestError("cycle_not_found", cycle_id)
    run = session.get(Run, version.run_id)
    if run is None:
        raise ManifestError("run_not_found", version.run_id)

    try:
        evaluation = evaluate_integration_cycle(session, cycle_id=cycle_id)
        technical = evaluation.status
    except ReviewError:
        technical = "blocked"
    # Map evaluation status onto TechnicalStatus
    tech_map = {
        "passed": "passed",
        "failed": "failed",
        "blocked": "blocked",
        "waiting": "waiting_review",
    }
    technical_status = tech_map.get(technical, "blocked")

    from agentforge.storage.models import Plan

    current_plan = session.get(Plan, (run.id, run.plan_version))
    if current_plan is None:
        raise ManifestError("manifest_stale", "Current plan missing")
    tree_hash = version.tree_manifest_hash or version.content_hash
    selection_hash = version.selection_hash or ("0" * 64)
    baseline_hash = version.baseline_hash or ("0" * 64)
    contract_hash = version.contract_hash or ("0" * 64)

    members: list[ManifestMember] = []
    # Always include the integrated code tree as a first-class member when present
    if version.tree_manifest_hash or version.content_hash:
        members.append(
            ManifestMember.model_validate(
                {
                    "memberId": f"tree:{version.id}",
                    "kind": "code_tree",
                    "required": True,
                    "safeRelativePath": ".",
                    "objectHash": version.content_hash,
                    "sourceCandidateRef": None,
                    "sourceReleaseRef": None,
                    "dependsOnMemberIds": [],
                    "applicableCheckIds": [
                        str(c.get("checkId"))
                        for c in (cycle.required_checks or [])
                        if c.get("checkId")
                    ],
                    "applicableReviewRequestIds": [],
                    "memberTechnicalStatus": technical_status
                    if technical_status
                    in {
                        "not_started",
                        "building",
                        "conflicted",
                        "checking",
                        "waiting_review",
                        "passed",
                        "failed",
                        "blocked",
                        "unknown",
                    }
                    else "blocked",
                }
            )
        )

    selection = (
        session.get(IntegrationSelectionRow, version.selection_id) if version.selection_id else None
    )
    if selection is not None:
        for binding in selection.source_bindings or []:
            release_id = str(binding.get("releaseId") or "")
            candidate_id = binding.get("candidateId")
            content = str(binding.get("contentHash") or binding.get("treeHash") or "")
            if len(content) != 64:
                continue
            mid = f"src:{release_id or candidate_id or content[:12]}"
            members.append(
                ManifestMember.model_validate(
                    {
                        "memberId": mid,
                        "kind": "other_artifact",
                        "required": True,
                        "safeRelativePath": str(
                            binding.get("safeRelativePath") or f"sources/{mid}"
                        )[:128],
                        "objectHash": content,
                        "sourceCandidateRef": str(candidate_id) if candidate_id else None,
                        "sourceReleaseRef": release_id or None,
                        "dependsOnMemberIds": [],
                        "applicableCheckIds": [],
                        "applicableReviewRequestIds": [],
                        "memberTechnicalStatus": "passed"
                        if technical_status == "passed"
                        else "blocked",
                    }
                )
            )

    # Deduplicate members by memberId (stable order by id)
    by_id: dict[str, ManifestMember] = {m.member_id: m for m in members}
    members = [by_id[k] for k in sorted(by_id)]

    check_entries: list[ManifestCheckEntry] = []
    runs = list(
        session.scalars(
            select(IntegrationCheckRunRow).where(IntegrationCheckRunRow.cycle_id == cycle.id)
        ).all()
    )
    latest: dict[str, IntegrationCheckRunRow] = {}
    for row in runs:
        prev = latest.get(row.check_id)
        if prev is None or int(row.attempt_no) >= int(prev.attempt_no):
            latest[row.check_id] = row
    for req in cycle.required_checks or []:
        cid = str(req.get("checkId"))
        run_row = latest.get(cid)
        report = None
        if run_row is not None:
            report = session.scalar(
                select(IntegrationCheckReportRow).where(
                    IntegrationCheckReportRow.check_run_id == run_row.id
                )
            )
        check_entries.append(
            ManifestCheckEntry.model_validate(
                {
                    "checkId": cid,
                    "checkVersion": str(
                        req.get("checkVersion") or (run_row.check_version if run_row else "v1")
                    ),
                    "configHash": str(
                        req.get("configHash") or (run_row.config_hash if run_row else ("0" * 64))
                    ),
                    "checkRunId": run_row.id if run_row else None,
                    "reportId": report.id if report else None,
                    "reportHash": report.report_hash if report else None,
                    "outcome": (report.outcome if report else "not_run"),
                    "environmentHash": report.environment_hash
                    if report
                    else cycle.environment_fingerprint,
                    "inputFingerprint": tree_hash,
                }
            )
        )

    review_entries: list[ManifestReviewEntry] = []
    for req in session.scalars(
        select(IntegrationReviewRequestRow).where(IntegrationReviewRequestRow.cycle_id == cycle.id)
    ).all():
        review_report = session.scalar(
            select(IntegrationReviewReportRow)
            .where(IntegrationReviewReportRow.request_id == req.id)
            .order_by(IntegrationReviewReportRow.created_at.asc())
        )
        binding_fp = hashlib.sha256(
            canonical_json(
                {
                    "requestId": req.id,
                    "treeHash": req.tree_hash,
                    "policyHash": req.policy_hash,
                    "selectionHash": req.selection_hash,
                    "contractHash": req.contract_hash,
                }
            ).encode()
        ).hexdigest()
        review_entries.append(
            ManifestReviewEntry.model_validate(
                {
                    "requestId": req.id,
                    "reportId": review_report.id if review_report else None,
                    "reviewerIdentityRef": review_report.reviewer_identity_ref
                    if review_report
                    else None,
                    "verdict": review_report.verdict if review_report else "pending",
                    "bindingFingerprint": binding_fp,
                }
            )
        )

    risks: list[ManifestRisk] = []
    if not members:
        risks.append(
            ManifestRisk.model_validate(
                {"kind": "not_run", "detail": "no_delivery_members", "memberIds": []}
            )
        )
        technical_status = "blocked"
    if not check_entries and technical_status == "passed":
        technical_status = "blocked"
        risks.append(
            ManifestRisk.model_validate(
                {"kind": "not_run", "detail": "no_recorded_checks", "memberIds": []}
            )
        )
    for entry in check_entries:
        if entry.outcome in {"not_run", "unknown", "blocked", "skipped"}:
            risks.append(
                ManifestRisk.model_validate(
                    {
                        "kind": "not_run" if entry.outcome == "not_run" else "unsupported",
                        "detail": f"check:{entry.check_id}:{entry.outcome}",
                        "memberIds": [],
                    }
                )
            )
        elif entry.outcome in {"failed", "timeout"}:
            risks.append(
                ManifestRisk.model_validate(
                    {
                        "kind": "known_failure",
                        "detail": f"check:{entry.check_id}:{entry.outcome}",
                        "memberIds": [],
                    }
                )
            )

    # Blocked incomplete results must not look ready
    if technical_status == "passed" and (not members or not check_entries):
        technical_status = "blocked"

    revision = next_manifest_revision(session, version.run_id)
    payload = DeliveryManifestPayload.model_validate(
        {
            "schemaVersion": "1",
            "runId": version.run_id,
            "integrationVersion": version.id,
            "validationCycleId": cycle.id,
            "manifestRevision": revision,
            "planVersion": int(run.plan_version or 1),
            "assignmentVersion": int(run.assignment_version or 1),
            "gatePolicyVersion": int(run.gate_policy_version or 1),
            "selectionHash": selection_hash if len(selection_hash) == 64 else ("0" * 64),
            "baselineHash": baseline_hash if len(baseline_hash) == 64 else ("0" * 64),
            "treeManifestHash": tree_hash,
            "contractHash": contract_hash if len(contract_hash) == 64 else ("0" * 64),
            "integrationPolicyHash": cycle.policy_hash,
            "environmentFingerprint": cycle.environment_fingerprint,
            "members": [m.model_dump(mode="json", by_alias=True) for m in members],
            "checks": [c.model_dump(mode="json", by_alias=True) for c in check_entries],
            "reviews": [r.model_dump(mode="json", by_alias=True) for r in review_entries],
            "knownRisks": [r.model_dump(mode="json", by_alias=True) for r in risks],
            "technicalStatus": technical_status,
        }
    )
    raw = canonical_manifest_bytes(payload)
    digest = hashlib.sha256(raw).hexdigest()
    return ManifestBuildIntent(
        intent_id=_id("mint"),
        run_id=version.run_id,
        version_id=version.id,
        cycle_id=cycle.id,
        revision=revision,
        payload=payload,
        manifest_hash=digest,
        payload_bytes=raw,
        expected_delivery_revision=get_or_create_delivery(session, run.id).delivery_revision,
        expected_status_version=run.status_version,
        expected_plan_hash=current_plan.plan_hash or "",
    )


def publish_manifest(
    engine: Engine,
    store: ArtifactStore,
    *,
    intent: ManifestBuildIntent,
) -> DeliveryManifestView:
    """Write content-addressed payload outside lock, then publish pointer in a short UOW."""
    # Outside transaction: content-addressed write
    object_hash = store.put_bytes(intent.payload_bytes)
    if object_hash != intent.manifest_hash:
        # put_bytes hashes raw bytes; must match our digest of the same bytes
        raise ManifestError(
            "content_integrity_failed",
            "stored object hash diverged from manifest hash",
        )

    with unit_of_work_connection(engine) as uow:
        session = uow.session
        run = session.get(Run, intent.run_id, with_for_update=True, populate_existing=True)
        if run is None:
            raise ManifestError("run_not_found", intent.run_id)
        delivery = get_or_create_delivery(session, intent.run_id)
        # Idempotent publication returns historical data, never rewinds the current pointer.
        existing = session.scalar(
            select(DeliveryManifestRow).where(
                DeliveryManifestRow.manifest_hash == intent.manifest_hash
            )
        )
        if existing is not None:
            return _view(existing, intent.payload)
        version = session.get(IntegrationVersionRow, intent.version_id)
        cycle = session.get(IntegrationValidationCycleRow, intent.cycle_id)
        from agentforge.integration.authority import (
            assert_run_deliverable,
            assert_selection_current,
        )
        from agentforge.integration.objects import load_tree

        try:
            assert_run_deliverable(session, run)
            if (
                version is None
                or cycle is None
                or cycle.version_id != version.id
                or version.run_id != run.id
            ):
                raise ValueError("version_cycle_mismatch")
            plan = assert_selection_current(
                session, run, session.get(IntegrationSelectionRow, version.selection_id)
            )
            if (
                delivery.current_version_id != version.id
                or delivery.delivery_revision != intent.expected_delivery_revision
                or run.status_version != intent.expected_status_version
                or plan.plan_hash != intent.expected_plan_hash
            ):
                raise ValueError("delivery_revision_conflict")
            load_tree(store, version.content_hash, version.tree_manifest_hash or "")
            current = build_delivery_manifest(session, version_id=version.id, cycle_id=cycle.id)
            if current.manifest_hash != intent.manifest_hash:
                raise ValueError("manifest_evidence_changed")
        except (ValueError, KeyError) as exc:
            raise ManifestError("manifest_stale", str(exc)) from exc
        protect_content_object(
            session,
            content_hash=object_hash,
            size_bytes=len(intent.payload_bytes),
            protect_hours=24 * 365,
        )

        now = _now(session)
        row = DeliveryManifestRow(
            id=_id("dm"),
            run_id=intent.run_id,
            version_id=intent.version_id,
            cycle_id=intent.cycle_id,
            revision=intent.revision,
            manifest_hash=intent.manifest_hash,
            payload_ref=object_hash,
            technical_status=intent.payload.technical_status,
            created_at=now,
        )
        session.add(row)
        session.flush()

        delivery.current_version_id = intent.version_id
        delivery.current_manifest_id = row.id
        delivery.technical_status = intent.payload.technical_status
        delivery.delivery_revision = int(delivery.delivery_revision) + 1
        if intent.payload.technical_status == "passed":
            delivery.acceptance_status = "awaiting_acceptance"
            run.status = "awaiting_acceptance"
            run.status_version += 1
        delivery.updated_at = now

        append_domain_fact(
            session,
            intent.run_id,
            "delivery.manifest_published",
            {
                "manifestId": row.id,
                "manifestHash": row.manifest_hash,
                "versionId": row.version_id,
                "cycleId": row.cycle_id,
                "revision": row.revision,
                "technicalStatus": row.technical_status,
                "deliveryRevision": delivery.delivery_revision,
            },
        )
        # Only emit delivery_ready when technically passed with real members
        if intent.payload.technical_status == "passed" and intent.payload.members:
            append_domain_fact(
                session,
                intent.run_id,
                "run.delivery_ready",
                {
                    "manifestHash": row.manifest_hash,
                    "integrationVersion": row.version_id,
                    "artifactCount": len(intent.payload.members),
                },
            )
        session.flush()
        return _view(row, intent.payload)


def _view(row: DeliveryManifestRow, payload: DeliveryManifestPayload) -> DeliveryManifestView:
    return DeliveryManifestView.model_validate(
        {
            "manifestId": row.id,
            "runId": row.run_id,
            "versionId": row.version_id,
            "cycleId": row.cycle_id,
            "revision": row.revision,
            "manifestHash": row.manifest_hash,
            "payload": payload.model_dump(mode="json", by_alias=True),
            "technicalStatus": row.technical_status,
            "createdAt": row.created_at,
            "etag": row.manifest_hash,
        }
    )


def build_and_publish_manifest(
    engine: Engine,
    store: ArtifactStore,
    *,
    version_id: str,
    cycle_id: str,
) -> DeliveryManifestView:
    with unit_of_work_connection(engine) as uow:
        intent = build_delivery_manifest(uow.session, version_id=version_id, cycle_id=cycle_id)
    return publish_manifest(engine, store, intent=intent)
