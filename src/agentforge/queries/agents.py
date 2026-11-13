"""Agent inventory counts and progress summaries (D12.4).

Read-only evidence evaluation — never validates capabilities with write locks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.snapshots import AgentCounts, AgentSummaryResponse, ProgressSummary
from agentforge.storage.models import (
    AgentInstallationRow,
    AgentInstanceRow,
    AgentProfileRow,
    CapabilityCheckRow,
    IntegrationCheckRunRow,
    IntegrationReviewRequestRow,
    IntegrationValidationCycleRow,
    Plan,
    ReleaseRecordRow,
    Run,
    RunDeliveryRow,
    StageRow,
    WorkPackage,
)
from agentforge.storage.uow import db_now

SchedulabilityCategory = Literal[
    "schedulable",
    "manual_assisted",
    "limited_auto",
    "verified_but_busy",
    "unknown",
    "not_schedulable",
]

_ACTIVE_LIFECYCLES = frozenset({"active", "starting", "stop_requested"})
_BUSY_ACTIVITIES = frozenset({"busy", "waiting_input", "waiting_approval"})


@dataclass(frozen=True, slots=True)
class SchedulabilityExplanation:
    profile_id: str
    category: SchedulabilityCategory
    reasons: tuple[str, ...]


def _as_of(session: Session, as_of: datetime | None) -> datetime:
    if as_of is not None:
        if as_of.tzinfo is None:
            return as_of.replace(tzinfo=UTC)
        return as_of
    try:
        value = db_now(session)
        if getattr(value, "tzinfo", None) is None:
            return value.replace(tzinfo=UTC)
        return value
    except Exception:
        return datetime.now(UTC)


def explain_profile_schedulability(
    session: Session,
    profile: AgentProfileRow,
    required_capabilities: set[str],
    *,
    as_of: datetime | None = None,
    busy_profile_ids: set[str] | None = None,
) -> SchedulabilityExplanation:
    """Side-effect-free evidence assessment (no FOR UPDATE / no validate_capabilities)."""
    now = _as_of(session, as_of)
    reasons: list[str] = []

    if profile.auth_status == "unknown":
        return SchedulabilityExplanation(profile.id, "unknown", ("auth_unknown",))
    if profile.auth_status != "authenticated":
        return SchedulabilityExplanation(
            profile.id, "not_schedulable", (f"auth_{profile.auth_status}",)
        )

    installation = session.get(AgentInstallationRow, profile.installation_id)
    if installation is None:
        return SchedulabilityExplanation(profile.id, "not_schedulable", ("installation_missing",))

    check = (
        session.get(CapabilityCheckRow, profile.last_validation_id)
        if profile.last_validation_id
        else None
    )
    if check is None:
        return SchedulabilityExplanation(
            profile.id, "not_schedulable", ("capability_evidence_missing",)
        )
    if (
        check.profile_id != profile.id
        or check.adapter_id != profile.adapter_id
        or check.adapter_version != profile.adapter_version
        or check.environment_id != installation.environment_id
        or check.product_version != installation.product_version
    ):
        return SchedulabilityExplanation(
            profile.id, "not_schedulable", ("capability_evidence_mismatch",)
        )
    if check.checked_at is None or check.checked_at > now:
        return SchedulabilityExplanation(
            profile.id, "not_schedulable", ("capability_checked_at_invalid",)
        )
    if now - check.checked_at > timedelta(hours=24):
        return SchedulabilityExplanation(profile.id, "not_schedulable", ("capability_stale",))
    if check.valid_until is not None and check.valid_until <= now:
        return SchedulabilityExplanation(profile.id, "not_schedulable", ("capability_expired",))
    if f"config:{profile.config_hash}" not in (check.evidence_refs or []):
        return SchedulabilityExplanation(profile.id, "not_schedulable", ("config_hash_unbound",))

    results = list(check.results or [])
    supported = {r.get("capability") for r in results if r.get("support") == "supported"}
    unknown_caps = {
        r.get("capability")
        for r in results
        if r.get("support") == "unknown" and r.get("capability") in required_capabilities
    }
    if required_capabilities and unknown_caps:
        return SchedulabilityExplanation(
            profile.id,
            "unknown",
            ("required_capability_unknown",),
        )
    if required_capabilities and not required_capabilities <= supported:
        missing = sorted(required_capabilities - supported)
        return SchedulabilityExplanation(
            profile.id,
            "not_schedulable",
            (f"capability_missing:{','.join(missing)}",),
        )

    if busy_profile_ids and profile.id in busy_profile_ids:
        return SchedulabilityExplanation(profile.id, "verified_but_busy", ("instance_busy",))

    access = profile.access_level
    if access == "manual_assisted":
        return SchedulabilityExplanation(profile.id, "manual_assisted", ("access_manual",))
    if access == "limited_auto":
        return SchedulabilityExplanation(profile.id, "limited_auto", ("access_limited",))
    if access == "controlled_auto":
        return SchedulabilityExplanation(profile.id, "schedulable", tuple(reasons) or ("ok",))
    return SchedulabilityExplanation(profile.id, "unknown", (f"access_level_unknown:{access}",))


def summarize_agent_counts(
    session: Session,
    *,
    environment_id: str,
    run_id: str | None = None,
    required_capabilities: set[str] | frozenset[str] | None = None,
    as_of: datetime | None = None,
) -> AgentCounts:
    """Three inventory counts — products ≠ profiles ≠ instances."""
    now = _as_of(session, as_of)
    caps = set(required_capabilities or ())

    installations = list(
        session.scalars(
            select(AgentInstallationRow).where(
                AgentInstallationRow.environment_id == environment_id
            )
        ).all()
    )
    product_ids = {inst.product_id for inst in installations}
    install_ids = {inst.id for inst in installations}

    profiles = [
        p
        for p in session.scalars(select(AgentProfileRow)).all()
        if p.installation_id in install_ids
    ]

    instances: list[AgentInstanceRow] = []
    if run_id is not None:
        instances = list(
            session.scalars(select(AgentInstanceRow).where(AgentInstanceRow.run_id == run_id)).all()
        )

    busy_profile_ids = {
        i.agent_profile_id
        for i in instances
        if i.lifecycle in _ACTIVE_LIFECYCLES and i.activity in _BUSY_ACTIVITIES
    }

    schedulable = 0
    manual = 0
    limited = 0
    verified_busy = 0
    unknown_profiles = 0
    for profile in profiles:
        expl = explain_profile_schedulability(
            session,
            profile,
            caps,
            as_of=now,
            busy_profile_ids=busy_profile_ids,
        )
        if expl.category == "schedulable":
            schedulable += 1
        elif expl.category == "manual_assisted":
            manual += 1
        elif expl.category == "limited_auto":
            limited += 1
        elif expl.category == "verified_but_busy":
            verified_busy += 1
        elif expl.category == "unknown":
            unknown_profiles += 1

    active_instances = sum(1 for i in instances if i.lifecycle in _ACTIVE_LIFECYCLES)
    # Native idle still counts as an existing active instance when lifecycle is active.
    scope = (
        f"environment={environment_id}"
        + (f"; run={run_id}" if run_id else "; run=none")
        + (f"; requiredCaps={len(caps)}" if caps else "; requiredCaps=none")
    )
    # Inventory revision is independent of Run.last_sequence.
    inventory_revision = max(1, len(product_ids) + len(profiles) + len(installations))

    return AgentCounts.model_validate(
        {
            "environmentId": environment_id,
            "discoveredProductCount": len(product_ids),
            "schedulableProfileCount": schedulable,
            "activeInstanceCount": active_instances,
            "manualAssistedProfileCount": manual,
            "limitedAutoProfileCount": limited,
            "verifiedButBusyProfileCount": verified_busy,
            "unknownProfileCount": unknown_profiles,
            "busyInstanceCount": sum(1 for i in instances if i.activity == "busy"),
            "waitingApprovalInstanceCount": sum(
                1 for i in instances if i.activity == "waiting_approval"
            ),
            "stoppedInstanceCount": sum(
                1 for i in instances if i.lifecycle in {"stopped", "closed"}
            ),
            "unknownInstanceCount": sum(
                1
                for i in instances
                if i.lifecycle in {"unknown", "needs_reconciliation"} or i.status == "unknown"
            ),
            "needsReconciliationInstanceCount": sum(
                1 for i in instances if i.lifecycle == "needs_reconciliation"
            ),
            "inventoryRevision": inventory_revision,
            "asOf": now,
            "scopeNote": scope,
        }
    )


def summarize_progress(session: Session, run: Run) -> ProgressSummary:
    """Explicit counters — collaboration 100% ≠ Run completed / acceptance done."""
    packages: list[WorkPackage] = []
    if run.plan_version is not None:
        packages = list(
            session.scalars(
                select(WorkPackage).where(
                    WorkPackage.run_id == run.id,
                    WorkPackage.plan_version == run.plan_version,
                )
            ).all()
        )

    def _count(*statuses: str) -> int:
        return sum(1 for p in packages if p.status in statuses)

    releases = list(
        session.scalars(
            select(ReleaseRecordRow)
            .join(StageRow, StageRow.id == ReleaseRecordRow.stage_id)
            .where(
                StageRow.run_id == run.id,
                StageRow.plan_version == run.plan_version,
                StageRow.assignment_version == run.assignment_version,
            )
        ).all()
    )
    released_wp: set[str] = set()
    for rel in releases:
        for wp in rel.work_package_ids or []:
            released_wp.add(str(wp))

    required_ids: set[str] = set()
    if run.plan_version is not None:
        plan = session.get(Plan, (run.id, run.plan_version))
        if plan is not None and plan.spec_json:
            from agentforge.contracts.plans import PlanSpec

            spec = PlanSpec.model_validate(plan.spec_json)
            required_ids = {
                w.id for w in spec.work_packages if any(d.required for d in w.deliverables)
            }
        elif packages:
            required_ids = {p.id for p in packages if p.required}

    # Prefer ReleaseRecord for "released"; fall back to published WP status.
    released_count = len(released_wp)
    required_released = len(required_ids & released_wp) if released_wp else 0

    delivery = session.get(RunDeliveryRow, run.id)
    acceptance = delivery.acceptance_status if delivery is not None else "none"

    checks_required = 0
    checks_passed = 0
    checks_failed = 0
    checks_unknown = 0
    checks_not_run = 0
    reviews_required = 0
    reviews_passed = 0
    reviews_pending = 0

    if delivery is not None and delivery.current_version_id:
        cycle = session.scalar(
            select(IntegrationValidationCycleRow)
            .where(IntegrationValidationCycleRow.version_id == delivery.current_version_id)
            .order_by(IntegrationValidationCycleRow.created_at.desc())
            .limit(1)
        )
        if cycle is not None:
            required_checks = list(cycle.required_checks or [])
            checks_required = len(required_checks)
            runs = list(
                session.scalars(
                    select(IntegrationCheckRunRow).where(
                        IntegrationCheckRunRow.cycle_id == cycle.id
                    )
                ).all()
            )
            # Latest attempt per check_id
            latest: dict[str, IntegrationCheckRunRow] = {}
            for row in runs:
                prev = latest.get(row.check_id)
                if prev is None or int(row.attempt_no) >= int(prev.attempt_no):
                    latest[row.check_id] = row
            seen = set(latest)
            for _check_id, row in latest.items():
                status = row.status
                if status == "passed":
                    checks_passed += 1
                elif status == "failed":
                    checks_failed += 1
                elif status == "unknown":
                    checks_unknown += 1
                else:
                    checks_not_run += 1
            for item in required_checks:
                cid = item if isinstance(item, str) else str(item.get("checkId", item))
                if cid not in seen:
                    checks_not_run += 1

            review_roles = list(cycle.required_review_roles or [])
            reviews_required = len(review_roles)
            review_reqs = list(
                session.scalars(
                    select(IntegrationReviewRequestRow).where(
                        IntegrationReviewRequestRow.cycle_id == cycle.id
                    )
                ).all()
            )
            for req in review_reqs:
                if req.status in {"passed", "approved", "accepted"}:
                    reviews_passed += 1
                elif req.status in {"pending", "waiting", "open"}:
                    reviews_pending += 1
            if reviews_required and not review_reqs:
                reviews_pending = reviews_required

    required_total = len(required_ids) if required_ids else sum(1 for p in packages if p.required)
    ratio = (required_released / required_total) if required_total else None

    return ProgressSummary.model_validate(
        {
            "workPackagesTotal": len(packages),
            "workPackagesReady": _count("ready"),
            "workPackagesRunning": _count("running", "validating", "repairing"),
            "workPackagesSubmitted": _count("validating"),
            "workPackagesReleased": released_count,
            "workPackagesBlocked": _count("blocked", "blocked_dependency"),
            "workPackagesFailed": _count("failed"),
            "requiredPackagesTotal": required_total,
            "requiredPackagesReleased": required_released,
            "finalChecksRequired": checks_required,
            "finalChecksPassed": checks_passed,
            "finalChecksFailed": checks_failed,
            "finalChecksUnknown": checks_unknown,
            "finalChecksNotRun": checks_not_run,
            "finalReviewsRequired": reviews_required,
            "finalReviewsPassed": reviews_passed,
            "finalReviewsPending": reviews_pending,
            "acceptanceStatus": acceptance,
            "collaborationProgressRatio": ratio,
        }
    )


def build_agent_summary(
    session: Session,
    *,
    environment_id: str,
    run_id: str | None = None,
    required_capabilities: list[str] | None = None,
    as_of: datetime | None = None,
) -> AgentSummaryResponse:
    caps = set(required_capabilities or [])
    counts = summarize_agent_counts(
        session,
        environment_id=environment_id,
        run_id=run_id,
        required_capabilities=caps,
        as_of=as_of,
    )
    return AgentSummaryResponse(counts=counts, required_capabilities=sorted(caps))
