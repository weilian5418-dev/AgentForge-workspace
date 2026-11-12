"""Bounded detail pagination for instances / stages / messages (D12.3)."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.snapshots import PageCursorResponse
from agentforge.driver.binding import sha256_hex
from agentforge.queries.authorization import AuthenticatedPrincipal, authorize_run_read
from agentforge.queries.snapshots import SnapshotError, make_page_cursor, parse_page_cursor
from agentforge.storage.models import (
    AgentInstanceRow,
    CollaborationMessageRow,
    StageRow,
)

DetailResource = Literal["instances", "stages", "messages"]


def list_run_details(
    session: Session,
    principal: AuthenticatedPrincipal,
    *,
    run_id: str,
    resource: DetailResource,
    cursor: str | None = None,
    limit: int = 50,
    snapshot_token: str | None = None,
    settings: Any | None = None,
) -> PageCursorResponse:
    """Current-view pagination for mutable resources.

    When snapshot_token is provided and statusVersion drifted, returns snapshot_stale.
    """
    run = authorize_run_read(session, principal, run_id)
    if snapshot_token and settings is not None:
        from agentforge.queries.snapshots import validate_snapshot_binding

        claims = validate_snapshot_binding(
            settings, snapshot_token, run_id=run_id
        )
        if int(claims.get("statusVersion") or 0) != int(run.status_version):
            raise SnapshotError("snapshot_stale", "run statusVersion changed since snapshot")

    filter_hash = sha256_hex({"resource": resource, "runId": run_id})
    after_key: dict[str, Any] | None = None
    if cursor:
        parsed = parse_page_cursor(cursor, run_id=run_id, resource=resource)
        if parsed.get("filterHash") != filter_hash:
            raise SnapshotError("snapshot_stale", "page filter changed")
        after_key = parsed.get("lastKey")

    limit = max(1, min(int(limit), 100))
    items: list[dict[str, Any]] = []
    next_key: dict[str, Any] | None = None

    if resource == "instances":
        stmt = (
            select(AgentInstanceRow)
            .where(AgentInstanceRow.run_id == run_id)
            .order_by(AgentInstanceRow.created_at.asc(), AgentInstanceRow.id.asc())
        )
        rows = list(session.scalars(stmt).all())
        if after_key:
            rows = [
                r
                for r in rows
                if (r.created_at.isoformat(), r.id)
                > (after_key.get("createdAt", ""), after_key.get("id", ""))
            ]
        page = rows[:limit]
        items = [
            {
                "instanceId": r.id,
                "roleId": r.role_id,
                "lifecycle": r.lifecycle,
                "activity": r.activity,
                "status": r.status,
                "createdAt": r.created_at.isoformat(),
            }
            for r in page
        ]
        if len(page) == limit:
            last = page[-1]
            next_key = {"createdAt": last.created_at.isoformat(), "id": last.id}
    elif resource == "stages":
        stmt = (
            select(StageRow)
            .where(StageRow.run_id == run_id)
            .order_by(StageRow.id.asc())
        )
        rows = list(session.scalars(stmt).all())
        if after_key:
            rows = [r for r in rows if r.id > str(after_key.get("id", ""))]
        page = rows[:limit]
        items = [
            {
                "stageId": r.id,
                "status": r.status,
                "waitingReason": r.waiting_reason,
                "planVersion": r.plan_version,
            }
            for r in page
        ]
        if len(page) == limit:
            next_key = {"id": page[-1].id}
    elif resource == "messages":
        stmt = (
            select(CollaborationMessageRow)
            .where(CollaborationMessageRow.run_id == run_id)
            .order_by(
                CollaborationMessageRow.created_at.asc(), CollaborationMessageRow.id.asc()
            )
        )
        rows = list(session.scalars(stmt).all())
        if after_key:
            rows = [
                r
                for r in rows
                if (r.created_at.isoformat(), r.id)
                > (after_key.get("createdAt", ""), after_key.get("id", ""))
            ]
        page = rows[:limit]
        items = [
            {
                "messageId": r.id,
                "messageType": r.message_type,
                "createdAt": r.created_at.isoformat(),
            }
            for r in page
        ]
        if len(page) == limit:
            last = page[-1]
            next_key = {"createdAt": last.created_at.isoformat(), "id": last.id}
    else:
        raise SnapshotError("invalid_request", f"unknown resource {resource}")

    next_cursor = None
    if next_key is not None:
        next_cursor = make_page_cursor(
            run_id=run_id,
            resource=resource,
            last_key=next_key,
            filter_hash=filter_hash,
            as_of_sequence=int(run.last_sequence or 0),
        )
    return PageCursorResponse(
        next_cursor=next_cursor,
        page_as_of_sequence=int(run.last_sequence or 0),
        binding_revision=int(run.status_version),
        exact_snapshot_match=bool(snapshot_token),
        items=items,
    )
