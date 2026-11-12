"""Outbox claim → enqueue → confirm (short transactions; D02)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from procrastinate import App

from agentforge.core.config import Settings
from agentforge.queue.app import open_app
from agentforge.queue.constants import (
    DEFAULT_QUEUE_NAME,
    INTENT_APPROVAL_FORWARD,
    INTENT_MODEL_PROBE,
    TASK_MODEL_PROBE,
)
from agentforge.storage.database import create_database_engine
from agentforge.storage.exceptions import OutboxClaimError
from agentforge.storage.repositories.outbox import OutboxRepository
from agentforge.storage.uow import unit_of_work_connection

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ClaimSnapshot:
    outbox_id: str
    owner: str
    token: str
    intent_type: str
    payload: dict[str, Any]


def dispatch_once(
    settings: Settings, *, owner: str | None = None, limit: int = 20
) -> dict[str, Any]:
    """Claim a batch, commit, enqueue outside the claim txn, then confirm/fail."""
    engine = create_database_engine(settings)
    if engine is None:
        raise RuntimeError("Database is not configured")
    worker_owner = owner or f"dispatcher-{uuid.uuid4().hex[:8]}"

    snapshots: list[ClaimSnapshot] = []
    with unit_of_work_connection(engine) as uow:
        claimed = OutboxRepository(uow).claim_batch(owner=worker_owner, limit=limit)
        for row in claimed:
            snapshots.append(
                ClaimSnapshot(
                    outbox_id=row.id,
                    owner=worker_owner,
                    token=row.claim_token or "",
                    intent_type=row.intent_type,
                    payload=dict(row.payload or {}),
                )
            )

    if not snapshots:
        return {"claimed": 0, "enqueued": 0, "failed": 0, "owner": worker_owner}

    app = open_app(settings)
    enqueued = 0
    failed = 0
    try:
        for snap in snapshots:
            try:
                _enqueue(app, snap)
            except Exception as exc:  # noqa: BLE001 — record per-row failure
                logger.exception("enqueue failed for %s", snap.outbox_id)
                with unit_of_work_connection(engine) as uow:
                    try:
                        OutboxRepository(uow).report_failure(
                            outbox_id=snap.outbox_id,
                            owner=snap.owner,
                            token=snap.token,
                            error=str(exc),
                        )
                    except OutboxClaimError:
                        logger.warning("stale claim on failure report %s", snap.outbox_id)
                failed += 1
                continue

            with unit_of_work_connection(engine) as uow:
                try:
                    OutboxRepository(uow).confirm_dispatched(
                        outbox_id=snap.outbox_id,
                        owner=snap.owner,
                        token=snap.token,
                    )
                    enqueued += 1
                except OutboxClaimError:
                    # Job may already be in queue; do not blind re-enqueue. Mark uncertain via log.
                    logger.error(
                        "confirm_dispatched rejected for %s; job may exist — reconcile",
                        snap.outbox_id,
                    )
                    failed += 1
    finally:
        app.close()

    return {
        "claimed": len(snapshots),
        "enqueued": enqueued,
        "failed": failed,
        "owner": worker_owner,
    }


def _enqueue(app: App, snap: ClaimSnapshot) -> None:
    payload = snap.payload
    operation_id = str(payload.get("operationId") or "")
    if not operation_id:
        raise ValueError("outbox payload missing operationId")

    if snap.intent_type == INTENT_APPROVAL_FORWARD:
        approval_id = str(payload.get("approvalId") or "")
        if not approval_id:
            raise ValueError("approval outbox payload missing approvalId")
        app.tasks["agentforge.forward_approval"].configure(
            queue=DEFAULT_QUEUE_NAME,
            lock=f"approval:{approval_id}",
        ).defer(approval_id=approval_id, operation_id=operation_id, task_version=1)
        return

    if snap.intent_type == INTENT_MODEL_PROBE or payload.get("taskType") == TASK_MODEL_PROBE:
        task = app.tasks["agentforge.execute_model_operation"]
        task.configure(
            queue=DEFAULT_QUEUE_NAME,
            lock=f"operation:{operation_id}",
        ).defer(
            operation_id=operation_id,
            outbox_id=snap.outbox_id,
            task_type=str(payload.get("taskType") or TASK_MODEL_PROBE),
            task_version=int(payload.get("taskVersion") or 1),
            provider_config_version=(
                int(payload["providerConfigVersion"])
                if payload.get("providerConfigVersion") is not None
                else None
            ),
        )
        return

    raise ValueError(f"unsupported intent_type={snap.intent_type}")
