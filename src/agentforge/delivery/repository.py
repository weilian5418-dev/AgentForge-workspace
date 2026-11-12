"""Delivery pointer and manifest persistence helpers (D12.12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentforge.storage.models import (
    ContentObjectRow,
    DeliveryManifestRow,
    RunDeliveryRow,
)
from agentforge.storage.uow import db_now


def _now(session: Session) -> datetime:
    try:
        value = db_now(session)
        if getattr(value, "tzinfo", None) is None:
            return value.replace(tzinfo=UTC)  # type: ignore[union-attr]
        return value  # type: ignore[return-value]
    except Exception:
        return datetime.now(UTC)


def get_or_create_delivery(session: Session, run_id: str) -> RunDeliveryRow:
    row = session.get(RunDeliveryRow, run_id)
    if row is not None:
        return row
    row = RunDeliveryRow(
        run_id=run_id,
        current_version_id=None,
        current_manifest_id=None,
        delivery_revision=1,
        technical_status="not_started",
        acceptance_status="none",
        invalidated_reason=None,
        updated_at=_now(session),
    )
    session.add(row)
    session.flush()
    return row


def next_manifest_revision(session: Session, run_id: str) -> int:
    current = session.scalar(
        select(func.coalesce(func.max(DeliveryManifestRow.revision), 0)).where(
            DeliveryManifestRow.run_id == run_id
        )
    )
    return int(current or 0) + 1


def get_manifest_by_hash(session: Session, manifest_hash: str) -> DeliveryManifestRow | None:
    return session.scalar(
        select(DeliveryManifestRow).where(DeliveryManifestRow.manifest_hash == manifest_hash)
    )


def get_manifest_by_id(session: Session, manifest_id: str) -> DeliveryManifestRow | None:
    return session.get(DeliveryManifestRow, manifest_id)


def protect_content_object(
    session: Session,
    *,
    content_hash: str,
    size_bytes: int,
    protect_hours: int = 24 * 365,
) -> None:
    """Pin content-addressed blob against GC while delivery is live or accepted."""
    now = _now(session)
    until = now + timedelta(hours=max(1, protect_hours))
    existing = session.get(ContentObjectRow, content_hash)
    if existing is None:
        session.add(
            ContentObjectRow(
                content_hash=content_hash,
                size_bytes=size_bytes,
                created_at=now,
                gc_marked_at=None,
                gc_protect_until=until,
            )
        )
    else:
        existing.gc_marked_at = None
        if existing.gc_protect_until is None or existing.gc_protect_until < until:
            existing.gc_protect_until = until
    session.flush()
