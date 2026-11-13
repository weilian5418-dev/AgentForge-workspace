"""User acceptance decisions bound to immutable manifests (D12.13)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.delivery import (
    AcceptanceDecisionRequest,
    AcceptanceResult,
    DeliveryManifestPayload,
    DeliveryPointerView,
)
from agentforge.delivery.repository import get_manifest_by_hash, get_or_create_delivery
from agentforge.queries.authorization import (
    AuthenticatedPrincipal,
    QueryAuthError,
    authorize_run_read,
)
from agentforge.storage.models import (
    AcceptanceDecisionRow,
    DeliveryManifestRow,
    Outbox,
    Run,
    RunDeliveryRow,
)
from agentforge.storage.repositories.events import append_domain_fact
from agentforge.storage.uow import db_now, unit_of_work_connection


class AcceptanceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now(session: Session) -> datetime:
    from datetime import UTC, datetime

    try:
        value = db_now(session)
        if getattr(value, "tzinfo", None) is None:
            return value.replace(tzinfo=UTC)
        return value
    except Exception:
        return datetime.now(UTC)


def _request_hash(
    *,
    run_id: str,
    expected_delivery_revision: int,
    body: AcceptanceDecisionRequest,
) -> str:
    payload = {
        "runId": run_id,
        "deliveryRevision": expected_delivery_revision,
        "integrationVersion": body.integration_version,
        "manifestHash": body.manifest_hash,
        "manifestRevision": body.manifest_revision,
        "expectedStatusVersion": body.expected_status_version,
        "decision": body.decision,
        "acceptedMemberIds": sorted(body.accepted_member_ids),
        "rejectedMemberIds": sorted(body.rejected_member_ids),
        "note": body.note,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_member_dependency_closure(
    *,
    payload: DeliveryManifestPayload,
    accepted_ids: set[str],
) -> None:
    """Accepted members must include dependency closure; shared failed checks block code."""
    by_id = {m.member_id: m for m in payload.members}
    unknown = accepted_ids - set(by_id)
    if unknown:
        raise AcceptanceError(
            "acceptance_scope_invalid",
            f"unknown members: {sorted(unknown)}",
        )

    # Dependency closure
    pending = set(accepted_ids)
    closed: set[str] = set()
    while pending:
        mid = pending.pop()
        if mid in closed:
            continue
        closed.add(mid)
        member = by_id[mid]
        for dep in member.depends_on_member_ids:
            if dep not in by_id:
                raise AcceptanceError(
                    "acceptance_scope_invalid",
                    f"missing dependency {dep} for {mid}",
                )
            if dep not in accepted_ids:
                raise AcceptanceError(
                    "acceptance_scope_invalid",
                    f"dependency {dep} of {mid} not in accepted set",
                )
            pending.add(dep)

    checks = {c.check_id: c for c in payload.checks}
    reviews = {r.request_id: r for r in payload.reviews}
    for mid in accepted_ids:
        member = by_id[mid]
        if member.member_technical_status != "passed":
            raise AcceptanceError(
                "required_check_not_passed", f"Member {mid} is not technically passed"
            )
        for cid in member.applicable_check_ids:
            entry = checks.get(cid)
            if (
                entry is None
                or entry.outcome != "passed"
                or not entry.report_id
                or not entry.report_hash
            ):
                raise AcceptanceError(
                    "required_check_not_passed", f"Missing valid report for {mid}/{cid}"
                )
        for rid in member.applicable_review_request_ids:
            review = reviews.get(rid)
            if review is None or review.verdict != "approve" or not review.report_id:
                raise AcceptanceError(
                    "required_check_not_passed", f"Missing final review for {mid}"
                )


def resolve_operation_status(session: Session, *, operation_id: str) -> dict[str, Any]:
    """Resolve integration/check/manifest operation refs — not model-only."""
    # Prefer outbox intent rows
    outbox = session.get(Outbox, operation_id)
    if outbox is not None:
        return {
            "operationId": operation_id,
            "kind": outbox.intent_type,
            "status": outbox.delivery_status,
            "payload": outbox.payload if isinstance(outbox.payload, dict) else {},
        }
    # Manifest id
    manifest = session.get(DeliveryManifestRow, operation_id)
    if manifest is not None:
        return {
            "operationId": operation_id,
            "kind": "delivery.manifest",
            "status": "published",
            "manifestHash": manifest.manifest_hash,
            "technicalStatus": manifest.technical_status,
        }
    raise AcceptanceError("operation_not_found", operation_id)


def load_manifest_payload(
    session: Session,
    store: ArtifactStore,
    *,
    manifest_hash: str,
) -> tuple[DeliveryManifestRow, DeliveryManifestPayload]:
    row = get_manifest_by_hash(session, manifest_hash)
    if row is None:
        raise AcceptanceError("manifest_stale", manifest_hash)
    try:
        raw = store.get_bytes(row.payload_ref)
    except Exception as exc:
        raise AcceptanceError("content_integrity_failed", str(exc)) from exc
    actual = hashlib.sha256(raw).hexdigest()
    if actual != row.manifest_hash or actual != manifest_hash:
        raise AcceptanceError("content_integrity_failed", "manifest payload hash mismatch")
    data = json.loads(raw.decode("utf-8"))
    payload = DeliveryManifestPayload.model_validate(data)
    return row, payload


def get_delivery_pointer(session: Session, *, run_id: str) -> DeliveryPointerView:
    delivery = get_or_create_delivery(session, run_id)
    manifest_hash = None
    if delivery.current_manifest_id:
        m = session.get(DeliveryManifestRow, delivery.current_manifest_id)
        if m is not None:
            manifest_hash = m.manifest_hash
    return DeliveryPointerView.model_validate(
        {
            "runId": run_id,
            "currentVersionId": delivery.current_version_id,
            "currentManifestId": delivery.current_manifest_id,
            "manifestHash": manifest_hash,
            "deliveryRevision": delivery.delivery_revision,
            "technicalStatus": delivery.technical_status,
            "acceptanceStatus": delivery.acceptance_status,
            "invalidatedReason": delivery.invalidated_reason,
            "etag": f"delivery:{delivery.delivery_revision}",
        }
    )


def decide_acceptance(
    engine: Engine,
    store: ArtifactStore,
    *,
    principal: AuthenticatedPrincipal,
    run_id: str,
    expected_delivery_revision: int,
    idempotency_key: str,
    body: AcceptanceDecisionRequest,
) -> AcceptanceResult:
    """Lock-ordered acceptance: Run → delivery → manifest. Never writes push/deploy outbox."""
    if not principal.credential_id or not principal.actor_ref:
        raise AcceptanceError("unauthorized", "principal required")
    if not idempotency_key:
        raise AcceptanceError("invalid_request", "idempotency key required")
    if expected_delivery_revision < 1:
        raise AcceptanceError("delivery_revision_conflict", "invalid If-Match revision")

    req_hash = _request_hash(
        run_id=run_id,
        expected_delivery_revision=expected_delivery_revision,
        body=body,
    )

    with unit_of_work_connection(engine) as uow:
        session = uow.session
        try:
            authorize_run_read(session, principal, run_id)
        except QueryAuthError as exc:
            raise AcceptanceError(exc.code, exc.message) from exc

        # Lock run then delivery
        run = session.get(Run, run_id, with_for_update=True)
        assert run is not None
        delivery = session.get(RunDeliveryRow, run_id, with_for_update=True)
        if delivery is None:
            raise AcceptanceError("manifest_stale", "no delivery pointer")

        # Idempotency: same actor + key
        prior = session.scalar(
            select(AcceptanceDecisionRow).where(
                AcceptanceDecisionRow.run_id == run_id,
                AcceptanceDecisionRow.actor_ref == principal.actor_ref,
                AcceptanceDecisionRow.request_key == idempotency_key,
            )
        )
        if prior is not None:
            if prior.request_hash != req_hash:
                raise AcceptanceError(
                    "idempotency_conflict",
                    "same key with different acceptance content",
                )
            current_hash = None
            if delivery.current_manifest_id:
                cm = session.get(DeliveryManifestRow, delivery.current_manifest_id)
                current_hash = cm.manifest_hash if cm else None
            is_current = (
                prior.delivery_revision + 1 == delivery.delivery_revision
                and prior.manifest_hash == current_hash
                and prior.version_id == delivery.current_version_id
                and delivery.acceptance_status in {"accepted", "rejected", "partially_accepted"}
            )
            return _to_result(prior, run=run, delivery=delivery, is_current=is_current)

        # Version / revision guards
        if int(delivery.delivery_revision) != int(expected_delivery_revision):
            raise AcceptanceError(
                "delivery_revision_conflict",
                f"expected {expected_delivery_revision}, current {delivery.delivery_revision}",
            )
        if int(run.status_version) != int(body.expected_status_version):
            raise AcceptanceError(
                "delivery_revision_conflict",
                f"statusVersion mismatch expected={body.expected_status_version} current={run.status_version}",
            )
        if delivery.current_version_id != body.integration_version:
            raise AcceptanceError(
                "manifest_stale",
                "integrationVersion is not current delivery version",
            )

        row, payload = load_manifest_payload(session, store, manifest_hash=body.manifest_hash)
        if row.id != delivery.current_manifest_id:
            raise AcceptanceError("manifest_stale", "manifest is not current delivery pointer")
        if row.revision != body.manifest_revision:
            raise AcceptanceError("manifest_stale", "manifestRevision mismatch")
        if row.version_id != body.integration_version:
            raise AcceptanceError("manifest_stale", "manifest version mismatch")

        from agentforge.integration.authority import (
            assert_run_deliverable,
            assert_selection_current,
        )
        from agentforge.integration.objects import load_tree
        from agentforge.integration.reviews import (
            assemble_integration_facts,
            evaluate_integration_facts,
        )
        from agentforge.storage.models import IntegrationSelectionRow, IntegrationVersionRow

        try:
            assert_run_deliverable(session, run)
            version = session.get(IntegrationVersionRow, row.version_id)
            if version is None or version.run_id != run.id:
                raise ValueError("version_missing")
            assert_selection_current(
                session, run, session.get(IntegrationSelectionRow, version.selection_id)
            )
            load_tree(store, version.content_hash, version.tree_manifest_hash or "")
            if (payload.plan_version, payload.assignment_version, payload.gate_policy_version) != (
                run.plan_version,
                run.assignment_version,
                run.gate_policy_version,
            ):
                raise ValueError("stale_plan_binding")
            if (
                delivery.invalidated_reason
                or payload.tree_manifest_hash != version.tree_manifest_hash
            ):
                raise ValueError("delivery_invalidated")
            evaluation = evaluate_integration_facts(
                assemble_integration_facts(session, cycle_id=row.cycle_id)
            )
            if body.decision == "accept" and evaluation.status != "passed":
                raise ValueError("required_check_not_passed")
        except (ValueError, KeyError) as exc:
            raise AcceptanceError("manifest_stale", str(exc)) from exc
        # Reject forged technical claims — technical_status comes from stored manifest only
        technical_status = row.technical_status

        member_ids = {m.member_id for m in payload.members}
        required_ids = {m.member_id for m in payload.members if m.required}

        if body.decision == "accept":
            if technical_status != "passed":
                raise AcceptanceError(
                    "required_check_not_passed",
                    "full accept requires technicalStatus=passed",
                )
            if not payload.members or not payload.checks:
                raise AcceptanceError(
                    "acceptance_scope_invalid",
                    "blocked empty delivery cannot be fully accepted",
                )
            accepted = set(required_ids) | set(body.accepted_member_ids) | member_ids
            # Full accept = all members
            accepted = set(member_ids)
            rejected: set[str] = set()
            validate_member_dependency_closure(payload=payload, accepted_ids=accepted)
            acceptance_status = "accepted"
            run_status_target = "completed"
        elif body.decision == "reject":
            accepted = set()
            rejected = set(body.rejected_member_ids) or set(member_ids)
            acceptance_status = "rejected"
            run_status_target = "blocked"
        elif body.decision == "partial_accept":
            accepted = set(body.accepted_member_ids)
            rejected = set(body.rejected_member_ids)
            if not accepted or accepted >= member_ids:
                raise AcceptanceError(
                    "acceptance_scope_invalid",
                    "partial_accept must be a non-empty proper subset",
                )
            validate_member_dependency_closure(payload=payload, accepted_ids=accepted)
            acceptance_status = "partially_accepted"
            run_status_target = "awaiting_acceptance"
        else:
            raise AcceptanceError("invalid_request", f"unknown decision {body.decision}")

        undecided = member_ids - accepted - rejected
        now = _now(session)
        decision = AcceptanceDecisionRow(
            id=_id("acd"),
            run_id=run_id,
            manifest_id=row.id,
            manifest_hash=row.manifest_hash,
            version_id=row.version_id,
            delivery_revision=expected_delivery_revision,
            actor_ref=principal.actor_ref,
            decision=body.decision,
            accepted_member_ids=sorted(accepted),
            rejected_member_ids=sorted(rejected),
            request_hash=req_hash,
            request_key=idempotency_key,
            note=body.note,
            created_at=now,
        )
        session.add(decision)

        delivery.acceptance_status = acceptance_status
        # technical_status unchanged by user opinion
        delivery.delivery_revision = int(delivery.delivery_revision) + 1
        delivery.updated_at = now
        run.status_version = int(run.status_version) + 1
        if run_status_target == "completed":
            # Only complete when no conflicting active cancel/replan markers
            if run.status not in {
                "canceled",
                "cancel_requested",
                "pause_requested",
                "paused",
                "failed",
                "completed",
            }:
                run.status = "completed"
        elif run_status_target == "blocked":
            if run.status not in {"completed", "canceled", "cancel_requested"}:
                run.status = "blocked"
        elif run_status_target == "awaiting_acceptance":
            if run.status not in {"completed", "canceled", "cancel_requested", "failed"}:
                run.status = "awaiting_acceptance"

        append_domain_fact(
            session,
            run_id,
            "acceptance.decided",
            {
                "decisionId": decision.id,
                "decision": body.decision,
                "manifestHash": row.manifest_hash,
                "versionId": row.version_id,
                "deliveryRevision": delivery.delivery_revision,
                "acceptanceStatus": acceptance_status,
                "isCurrent": True,
            },
        )
        # Explicitly do NOT create git.push / deploy / publish_site outbox intents.
        session.flush()
        return AcceptanceResult.model_validate(
            {
                "decisionId": decision.id,
                "runId": run_id,
                "decision": body.decision,
                "integrationVersion": row.version_id,
                "manifestHash": row.manifest_hash,
                "deliveryRevision": delivery.delivery_revision,
                "acceptanceStatus": acceptance_status,
                "technicalStatus": technical_status,
                "isCurrent": True,
                "runStatus": run.status,
                "acceptedMemberIds": sorted(accepted),
                "rejectedMemberIds": sorted(rejected),
                "undecidedMemberIds": sorted(undecided),
                "createdAt": decision.created_at,
            }
        )


def _to_result(
    prior: AcceptanceDecisionRow,
    *,
    run: Run,
    delivery: RunDeliveryRow,
    is_current: bool,
) -> AcceptanceResult:
    return AcceptanceResult.model_validate(
        {
            "decisionId": prior.id,
            "runId": prior.run_id,
            "decision": prior.decision,
            "integrationVersion": prior.version_id,
            "manifestHash": prior.manifest_hash,
            "deliveryRevision": delivery.delivery_revision,
            "acceptanceStatus": delivery.acceptance_status if is_current else "none",
            "technicalStatus": delivery.technical_status,
            "isCurrent": is_current,
            "runStatus": run.status,
            "acceptedMemberIds": list(prior.accepted_member_ids or []),
            "rejectedMemberIds": list(prior.rejected_member_ids or []),
            "undecidedMemberIds": [],
            "createdAt": prior.created_at,
        }
    )


def list_acceptance_decisions(session: Session, *, run_id: str) -> list[AcceptanceResult]:
    delivery = get_or_create_delivery(session, run_id)
    current_hash = None
    if delivery.current_manifest_id:
        cm = session.get(DeliveryManifestRow, delivery.current_manifest_id)
        current_hash = cm.manifest_hash if cm else None
    rows = list(
        session.scalars(
            select(AcceptanceDecisionRow)
            .where(AcceptanceDecisionRow.run_id == run_id)
            .order_by(AcceptanceDecisionRow.created_at.asc())
        ).all()
    )
    run = session.get(Run, run_id)
    results: list[AcceptanceResult] = []
    for r in rows:
        is_current = r.manifest_hash == current_hash and r.version_id == delivery.current_version_id
        results.append(
            AcceptanceResult.model_validate(
                {
                    "decisionId": r.id,
                    "runId": r.run_id,
                    "decision": r.decision,
                    "integrationVersion": r.version_id,
                    "manifestHash": r.manifest_hash,
                    "deliveryRevision": delivery.delivery_revision,
                    "acceptanceStatus": delivery.acceptance_status if is_current else "none",
                    "technicalStatus": delivery.technical_status,
                    "isCurrent": is_current,
                    "runStatus": run.status if run else None,
                    "acceptedMemberIds": list(r.accepted_member_ids or []),
                    "rejectedMemberIds": list(r.rejected_member_ids or []),
                    "undecidedMemberIds": [],
                    "createdAt": r.created_at,
                }
            )
        )
    return results
