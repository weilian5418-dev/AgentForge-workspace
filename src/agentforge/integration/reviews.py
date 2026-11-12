"""Version-bound final integration reviews (D12.11).

Stage ReviewReports are not reused here — final reviews bind cycle/version/tree.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.integrations import (
    FinalReviewReportView,
    FinalReviewRequestView,
    FinalReviewResponseRequest,
    IntegrationEvaluation,
)
from agentforge.integration.checks import evaluate_check_requirements
from agentforge.storage.models import (
    AgentInstanceRow,
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
from agentforge.storage.uow import db_now


class ReviewError(Exception):
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


def _parse_binding(binding: str) -> tuple[str, str]:
    """reviewer_binding format: role_id:instance_id"""
    if ":" not in binding:
        raise ReviewError("review_binding_mismatch", "malformed reviewer_binding")
    role_id, instance_id = binding.split(":", 1)
    if not role_id or not instance_id:
        raise ReviewError("review_binding_mismatch", "malformed reviewer_binding")
    return role_id, instance_id


def _encode_binding(role_id: str, instance_id: str) -> str:
    return f"{role_id}:{instance_id}"


def _request_view(row: IntegrationReviewRequestRow) -> FinalReviewRequestView:
    return FinalReviewRequestView.model_validate(
        {
            "requestId": row.id,
            "cycleId": row.cycle_id,
            "versionId": row.version_id,
            "treeHash": row.tree_hash,
            "selectionHash": row.selection_hash,
            "contractHash": row.contract_hash,
            "policyHash": row.policy_hash,
            "reviewerBinding": row.reviewer_binding,
            "expiresAt": row.expires_at,
            "status": row.status,
        }
    )


def _report_view(row: IntegrationReviewReportRow) -> FinalReviewReportView:
    return FinalReviewReportView.model_validate(
        {
            "reportId": row.id,
            "requestId": row.request_id,
            "cycleId": row.cycle_id,
            "versionId": row.version_id,
            "treeHash": row.tree_hash,
            "verdict": row.verdict,
            "reviewerIdentityRef": row.reviewer_identity_ref,
            "evidenceRefs": list(row.evidence_refs or []),
            "createdAt": row.created_at,
        }
    )


@dataclass(frozen=True, slots=True)
class IntegrationFacts:
    """Server-assembled facts — never accepted from public HTTP."""

    cycle_id: str
    version_id: str
    run_id: str
    tree_hash: str
    selection_hash: str
    contract_hash: str
    policy_hash: str
    environment_fingerprint: str
    selection_still_valid: bool
    build_conflict_free: bool
    content_integrity_ok: bool
    required_checks: list[dict[str, Any]] = field(default_factory=list)
    check_reports: list[dict[str, Any]] = field(default_factory=list)
    required_review_roles: list[str] = field(default_factory=list)
    review_requests: list[dict[str, Any]] = field(default_factory=list)
    review_reports: list[dict[str, Any]] = field(default_factory=list)


def evaluate_integration_facts(facts: IntegrationFacts) -> IntegrationEvaluation:
    """Pure evaluation — empty required checks never all([]) pass."""
    blockers: list[str] = []
    status: str = "passed"

    if not facts.selection_still_valid:
        blockers.append("selection_stale")
        status = "blocked"
    if not facts.build_conflict_free:
        blockers.append("integration_conflict")
        status = "failed"
    if not facts.content_integrity_ok:
        blockers.append("content_integrity_failed")
        status = "failed"

    required_checks = [c for c in facts.required_checks if c.get("required", True)]
    checks_total = len(required_checks)
    check_agg = evaluate_check_requirements(
        required=required_checks,
        reports=facts.check_reports,
    )
    satisfied_checks = 0
    by_check = {str(r.get("checkId")): r for r in facts.check_reports}
    for item in required_checks:
        cid = str(item.get("checkId"))
        rep = by_check.get(cid)
        if (
            rep is not None
            and rep.get("outcome") == "passed"
            and rep.get("treeHash") == facts.tree_hash
            and rep.get("policyHash") == facts.policy_hash
            and rep.get("environmentHash") == facts.environment_fingerprint
            and rep.get("versionId") == facts.version_id
        ):
            satisfied_checks += 1
        elif rep is not None and (
            rep.get("treeHash") != facts.tree_hash
            or rep.get("policyHash") != facts.policy_hash
            or rep.get("environmentHash") != facts.environment_fingerprint
            or rep.get("versionId") != facts.version_id
        ):
            blockers.append("check_evidence_mismatch")
            if status == "passed":
                status = "blocked"

    if checks_total == 0:
        blockers.append("required_check_not_passed")
        if status == "passed":
            status = "blocked"
    elif check_agg == "failed":
        blockers.append("required_check_not_passed")
        status = "failed"
    elif check_agg != "passed":
        blockers.append("required_check_not_passed")
        if status == "passed":
            status = "blocked" if check_agg == "blocked" else "waiting"

    roles = list(facts.required_review_roles)
    reviews_total = len(roles)
    request_by_role = {
        str(r.get("roleId")): r for r in facts.review_requests if r.get("roleId")
    }
    satisfied_reviews = 0

    if reviews_total == 0:
        # No required final reviewers — do not invent a pass via empty set,
        # but also do not block solely for missing reviews when policy has none.
        pass
    else:
        for role in roles:
            req = request_by_role.get(role)
            if req is None:
                blockers.append("review_binding_mismatch")
                if status == "passed":
                    status = "waiting"
                continue
            if req.get("cycleId") != facts.cycle_id or req.get("versionId") != facts.version_id:
                blockers.append("review_binding_mismatch")
                if status == "passed":
                    status = "blocked"
                continue
            if req.get("treeHash") != facts.tree_hash:
                blockers.append("review_binding_mismatch")
                if status == "passed":
                    status = "blocked"
                continue
            reports_for = [
                r
                for r in facts.review_reports
                if r.get("requestId") == req.get("requestId")
                and r.get("cycleId") == facts.cycle_id
                and r.get("versionId") == facts.version_id
                and r.get("treeHash") == facts.tree_hash
            ]
            if not reports_for:
                if status == "passed":
                    status = "waiting"
                continue
            latest = reports_for[-1]
            if latest.get("verdict") == "approve":
                satisfied_reviews += 1
            elif latest.get("verdict") in {"reject", "needs_rework"}:
                blockers.append("final_review_rejected")
                status = "failed"
            else:
                if status == "passed":
                    status = "waiting"

    # Deduplicate blockers while preserving order
    seen: set[str] = set()
    unique_blockers: list[str] = []
    for b in blockers:
        if b not in seen:
            seen.add(b)
            unique_blockers.append(b)

    if status == "passed" and checks_total == 0:
        status = "blocked"
        if "required_check_not_passed" not in unique_blockers:
            unique_blockers.append("required_check_not_passed")

    return IntegrationEvaluation.model_validate(
        {
            "cycleId": facts.cycle_id,
            "versionId": facts.version_id,
            "status": status,
            "blockerCodes": unique_blockers[:32],
            "requiredChecksTotal": checks_total,
            "requiredChecksSatisfied": satisfied_checks,
            "requiredReviewsTotal": reviews_total,
            "requiredReviewsSatisfied": satisfied_reviews,
            "note": None,
        }
    )


def open_final_review(
    session: Session,
    *,
    cycle_id: str,
    reviewer_assignment_ref: str,
    expires_in_seconds: int = 86_400,
) -> FinalReviewRequestView:
    """Open a final review request bound to cycle/version/tree/contract/policy."""
    cycle = session.get(IntegrationValidationCycleRow, cycle_id)
    if cycle is None:
        raise ReviewError("cycle_not_found", cycle_id)
    version = session.get(IntegrationVersionRow, cycle.version_id)
    if version is None:
        raise ReviewError("version_not_found", cycle.version_id)
    run = session.get(Run, version.run_id)
    if run is None:
        raise ReviewError("run_not_found", version.run_id)

    instance = session.get(AgentInstanceRow, reviewer_assignment_ref)
    if (
        instance is None
        or instance.run_id != run.id
        or (
            instance.assignment_version is not None
            and run.assignment_version is not None
            and instance.assignment_version != run.assignment_version
        )
    ):
        raise ReviewError("review_binding_mismatch", "reviewer instance not current for run")

    required_roles = list(cycle.required_review_roles or [])
    if not required_roles:
        raise ReviewError(
            "review_binding_mismatch",
            "no required final review roles in cycle policy",
        )
    if instance.role_id not in required_roles:
        raise ReviewError(
            "review_binding_mismatch",
            f"role {instance.role_id} not in required_review_roles",
        )

    existing = session.scalar(
        select(IntegrationReviewRequestRow).where(
            IntegrationReviewRequestRow.cycle_id == cycle_id,
            IntegrationReviewRequestRow.reviewer_binding
            == _encode_binding(instance.role_id, instance.id),
            IntegrationReviewRequestRow.status.in_(("pending", "answered")),
        )
    )
    if existing is not None:
        return _request_view(existing)

    tree_hash = version.tree_manifest_hash or version.content_hash
    selection_hash = version.selection_hash or ""
    contract_hash = version.contract_hash or ""
    if not selection_hash or not contract_hash:
        raise ReviewError("review_binding_mismatch", "version missing selection/contract hash")

    now = _now(session)
    row = IntegrationReviewRequestRow(
        id=_id("irr"),
        cycle_id=cycle.id,
        version_id=version.id,
        tree_hash=tree_hash,
        selection_hash=selection_hash,
        contract_hash=contract_hash,
        policy_hash=cycle.policy_hash,
        reviewer_binding=_encode_binding(instance.role_id, instance.id),
        status="pending",
        expires_at=now + timedelta(seconds=max(60, min(expires_in_seconds, 7 * 86_400))),
        created_at=now,
    )
    session.add(row)
    session.flush()
    if cycle.status in {"open", "checking"}:
        cycle.status = "awaiting_review"
    return _request_view(row)


def submit_final_review(
    session: Session,
    *,
    trusted_identity: str,
    request_id: str,
    body: FinalReviewResponseRequest,
) -> FinalReviewReportView:
    """Accept a trusted-instance final review — ordinary user bearer must not call this."""
    if not trusted_identity:
        raise ReviewError("review_binding_mismatch", "trusted identity required")

    req = session.get(IntegrationReviewRequestRow, request_id)
    if req is None:
        raise ReviewError("review_request_not_found", request_id)

    role_id, instance_id = _parse_binding(req.reviewer_binding)
    if trusted_identity != instance_id:
        raise ReviewError(
            "review_binding_mismatch",
            "ticket subject is not the bound reviewer instance",
        )

    now = _now(session)
    if req.expires_at <= now:
        raise ReviewError("review_binding_mismatch", "review request expired")

    cycle = session.get(IntegrationValidationCycleRow, req.cycle_id)
    version = session.get(IntegrationVersionRow, req.version_id)
    if cycle is None or version is None:
        raise ReviewError("review_binding_mismatch", "cycle or version missing")
    if cycle.id != req.cycle_id or version.id != req.version_id:
        raise ReviewError("review_binding_mismatch", "request cycle/version mismatch")

    tree_hash = version.tree_manifest_hash or version.content_hash
    if (
        req.tree_hash != tree_hash
        or req.policy_hash != cycle.policy_hash
        or req.selection_hash != (version.selection_hash or "")
        or req.contract_hash != (version.contract_hash or "")
    ):
        raise ReviewError("review_binding_mismatch", "request fingerprints drifted")

    instance = session.get(AgentInstanceRow, instance_id)
    if instance is None or instance.role_id != role_id or instance.run_id != version.run_id:
        raise ReviewError("review_binding_mismatch", "reviewer instance invalid")

    existing = session.scalar(
        select(IntegrationReviewReportRow).where(
            IntegrationReviewReportRow.request_id == req.id,
            IntegrationReviewReportRow.response_key == body.response_key,
        )
    )
    if existing is not None:
        same = (
            existing.verdict == body.verdict
            and list(existing.evidence_refs or []) == list(body.evidence_refs)
        )
        if same:
            return _report_view(existing)
        raise ReviewError("idempotency_conflict", "same response_key with different content")

    # Same request already answered with different key → conflict if verdict differs
    prior = list(
        session.scalars(
            select(IntegrationReviewReportRow).where(
                IntegrationReviewReportRow.request_id == req.id
            )
        ).all()
    )
    if prior and any(p.verdict != body.verdict for p in prior):
        raise ReviewError("idempotency_conflict", "conflicting verdict for request")

    report = IntegrationReviewReportRow(
        id=_id("irrep"),
        request_id=req.id,
        cycle_id=req.cycle_id,
        version_id=req.version_id,
        tree_hash=req.tree_hash,
        verdict=body.verdict,
        reviewer_identity_ref=trusted_identity,
        response_key=body.response_key,
        evidence_refs=list(body.evidence_refs),
        created_at=now,
    )
    session.add(report)
    req.status = "answered"
    append_domain_fact(
        session,
        version.run_id,
        "integration.review_completed",
        {
            "requestId": req.id,
            "reportId": report.id,
            "cycleId": cycle.id,
            "versionId": version.id,
            "verdict": body.verdict,
            "treeHash": req.tree_hash,
        },
    )
    session.flush()
    return _report_view(report)


def assemble_integration_facts(session: Session, *, cycle_id: str) -> IntegrationFacts:
    """Load real tables into IntegrationFacts — never from client-supplied passed flags."""
    cycle = session.get(IntegrationValidationCycleRow, cycle_id)
    if cycle is None:
        raise ReviewError("cycle_not_found", cycle_id)
    version = session.get(IntegrationVersionRow, cycle.version_id)
    if version is None:
        raise ReviewError("version_not_found", cycle.version_id)

    tree_hash = version.tree_manifest_hash or version.content_hash
    selection_hash = version.selection_hash or ""
    contract_hash = version.contract_hash or ""

    selection_still_valid = True
    if version.selection_id:
        selection = session.get(IntegrationSelectionRow, version.selection_id)
        if (
            selection is None
            or selection.selection_hash != selection_hash
            or selection.run_id != version.run_id
        ):
            selection_still_valid = False
    else:
        selection_still_valid = False

    # Build considered conflict-free when version has a concrete tree hash (succeeded build).
    build_conflict_free = bool(version.tree_manifest_hash or version.content_hash)

    check_runs = list(
        session.scalars(
            select(IntegrationCheckRunRow).where(IntegrationCheckRunRow.cycle_id == cycle.id)
        ).all()
    )
    latest_run: dict[str, IntegrationCheckRunRow] = {}
    for row in check_runs:
        prev = latest_run.get(row.check_id)
        if prev is None or int(row.attempt_no) >= int(prev.attempt_no):
            latest_run[row.check_id] = row

    check_reports: list[dict[str, Any]] = []
    for run_row in latest_run.values():
        report = session.scalar(
            select(IntegrationCheckReportRow).where(
                IntegrationCheckReportRow.check_run_id == run_row.id
            )
        )
        if report is None:
            continue
        check_reports.append(
            {
                "checkId": run_row.check_id,
                "outcome": report.outcome,
                "treeHash": report.tree_hash,
                "policyHash": report.policy_hash,
                "environmentHash": report.environment_hash,
                "versionId": report.version_id,
                "reportHash": report.report_hash,
            }
        )

    content_integrity_ok = all(
        r.get("treeHash") == tree_hash for r in check_reports
    ) if check_reports else True

    review_reqs = list(
        session.scalars(
            select(IntegrationReviewRequestRow).where(
                IntegrationReviewRequestRow.cycle_id == cycle.id
            )
        ).all()
    )
    review_requests: list[dict[str, Any]] = []
    for req in review_reqs:
        role_id, _ = _parse_binding(req.reviewer_binding)
        review_requests.append(
            {
                "requestId": req.id,
                "roleId": role_id,
                "cycleId": req.cycle_id,
                "versionId": req.version_id,
                "treeHash": req.tree_hash,
                "status": req.status,
            }
        )

    review_reps = list(
        session.scalars(
            select(IntegrationReviewReportRow).where(
                IntegrationReviewReportRow.cycle_id == cycle.id
            )
        ).all()
    )
    # Exclude reports whose request was for a different cycle/version (replay protection)
    review_reports = [
        {
            "requestId": r.request_id,
            "cycleId": r.cycle_id,
            "versionId": r.version_id,
            "treeHash": r.tree_hash,
            "verdict": r.verdict,
        }
        for r in review_reps
        if r.cycle_id == cycle.id and r.version_id == version.id
    ]

    return IntegrationFacts(
        cycle_id=cycle.id,
        version_id=version.id,
        run_id=version.run_id,
        tree_hash=tree_hash,
        selection_hash=selection_hash,
        contract_hash=contract_hash,
        policy_hash=cycle.policy_hash,
        environment_fingerprint=cycle.environment_fingerprint,
        selection_still_valid=selection_still_valid,
        build_conflict_free=build_conflict_free,
        content_integrity_ok=content_integrity_ok,
        required_checks=list(cycle.required_checks or []),
        check_reports=check_reports,
        required_review_roles=list(cycle.required_review_roles or []),
        review_requests=review_requests,
        review_reports=review_reports,
    )
