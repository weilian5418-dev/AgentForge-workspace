"""Integration validation chain application entry (D12.11)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from agentforge.contracts.integrations import (
    FinalReviewReportView,
    FinalReviewRequestView,
    FinalReviewResponseRequest,
    IntegrationEvaluation,
)
from agentforge.integration.reviews import (
    ReviewError,
    assemble_integration_facts,
    evaluate_integration_facts,
    open_final_review,
    submit_final_review,
)
from agentforge.storage.models import IntegrationValidationCycleRow, RunDeliveryRow


def evaluate_integration_cycle(session: Session, *, cycle_id: str) -> IntegrationEvaluation:
    """Assemble facts from DB and evaluate — updates cycle/delivery technical projection."""
    facts = assemble_integration_facts(session, cycle_id=cycle_id)
    evaluation = evaluate_integration_facts(facts)

    cycle = session.get(IntegrationValidationCycleRow, cycle_id)
    if cycle is None:
        raise ReviewError("cycle_not_found", cycle_id)

    if evaluation.status == "passed":
        cycle.status = "passed"
    elif evaluation.status == "failed":
        cycle.status = "failed"
    elif evaluation.status == "blocked":
        cycle.status = "blocked"
    elif evaluation.status == "waiting":
        if cycle.status not in {"awaiting_review", "checking"}:
            cycle.status = "awaiting_review"

    # A historical evaluation may update its own cycle, never a newer delivery projection.
    delivery = session.get(RunDeliveryRow, facts.run_id)
    if delivery is not None and delivery.current_version_id == facts.version_id:
        target = {
            "passed": "passed",
            "failed": "failed",
            "blocked": "blocked",
            "waiting": "waiting_review",
        }[evaluation.status]
        if delivery.technical_status != target:
            from agentforge.storage.repositories.events import append_domain_fact

            delivery.technical_status = target
            delivery.delivery_revision += 1
            append_domain_fact(
                session,
                facts.run_id,
                "integration.validated",
                {
                    "integrationVersion": facts.version_id,
                    "manifestHash": facts.tree_hash,
                    "passed": evaluation.status == "passed",
                },
            )
    session.flush()
    return evaluation


def open_cycle_final_reviews(
    session: Session,
    *,
    cycle_id: str,
    reviewer_assignment_refs: list[str],
) -> list[FinalReviewRequestView]:
    """Open one final-review request per confirmed reviewer assignment."""
    return [
        open_final_review(session, cycle_id=cycle_id, reviewer_assignment_ref=ref)
        for ref in reviewer_assignment_refs
    ]


def submit_trusted_final_review(
    session: Session,
    *,
    trusted_identity: str,
    request_id: str,
    body: FinalReviewResponseRequest,
) -> FinalReviewReportView:
    return submit_final_review(
        session,
        trusted_identity=trusted_identity,
        request_id=request_id,
        body=body,
    )


__all__ = [
    "ReviewError",
    "evaluate_integration_cycle",
    "open_cycle_final_reviews",
    "open_final_review",
    "submit_trusted_final_review",
]
