"""Wait-status reconciliation queries (historical 0003/0004 ambiguity)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from agentforge.core.config import Settings
from agentforge.storage.models import RunStatusReconciliation, RunStatusReconciliationBatch


def pending_reconciliation_summary(settings: Settings) -> dict[str, Any]:
    """Return pending isolation counts. Missing tables → not_applicable."""
    if settings.database_url is None:
        return {
            "status": "unconfigured",
            "pendingCount": 0,
            "latestBatchId": None,
            "latestBatchCandidateCount": None,
            "message": "Database not configured; reconciliation not checked",
        }

    engine = create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'app' "
                    "AND table_name = 'run_status_reconciliations'"
                )
            ).scalar()
            if not exists:
                return {
                    "status": "missing_tables",
                    "pendingCount": 0,
                    "latestBatchId": None,
                    "latestBatchCandidateCount": None,
                    "message": "Reconciliation tables missing; run alembic upgrade head",
                }

        # Use ORM Session so entity selects return mapped objects, not bare column values.
        with Session(engine) as session:
            pending = session.scalar(
                select(func.count())
                .select_from(RunStatusReconciliation)
                .where(RunStatusReconciliation.resolution_status == "pending")
            )
            pending_count = int(pending or 0)
            batch = session.scalars(
                select(RunStatusReconciliationBatch)
                .order_by(RunStatusReconciliationBatch.checked_at.desc())
                .limit(1)
            ).first()
            return {
                "status": "pending" if pending_count else "clear",
                "pendingCount": pending_count,
                "latestBatchId": None if batch is None else batch.id,
                "latestBatchCandidateCount": None if batch is None else batch.candidate_count,
                "message": (
                    f"{pending_count} run(s) blocked pending wait-status reconciliation"
                    if pending_count
                    else "No pending wait-status reconciliations"
                ),
            }
    finally:
        engine.dispose()
