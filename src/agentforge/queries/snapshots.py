"""Consistent run snapshot in one REPEATABLE READ transaction (D12.3)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agentforge.contracts.common import BudgetLimits
from agentforge.contracts.snapshots import (
    DeliveryOverview,
    ExecutionBindingOverview,
    RunOverview,
    RunSnapshot,
    StageSummary,
    WaitSummary,
)
from agentforge.core.config import Settings
from agentforge.queries.agents import summarize_agent_counts, summarize_progress
from agentforge.queries.authorization import (
    AuthenticatedPrincipal,
    QueryAuthError,
    authorize_run_read,
)
from agentforge.storage.models import (
    AgentInstanceRow,
    DriverThreadRow,
    Event,
    EventReplayWindowRow,
    IntegrationSelectionRow,
    Run,
    RunDeliveryRow,
    RunWaitRow,
    StageRow,
)
from agentforge.storage.uow import db_now


class SnapshotError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now(session: Session) -> datetime:
    try:
        value = db_now(session)
        if getattr(value, "tzinfo", None) is None:
            return value.replace(tzinfo=UTC)
        return value
    except Exception:
        return datetime.now(UTC)


def _signing_secret(settings: Settings) -> bytes:
    key = settings.internal_signing_key
    if key is None or len(key.get_secret_value()) < 32:
        material = (
            settings.api_token.get_secret_value()
            if settings.api_token is not None
            else "dev-snapshot-binding-key-not-for-production"
        )
        return material.encode()
    return key.get_secret_value().encode()


def _sha_scope(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def make_snapshot_token(
    settings: Settings,
    *,
    run_id: str,
    latest_sequence: int,
    status_version: int,
    delivery_revision: int | None,
    query_scope: dict[str, Any] | None = None,
    ttl_seconds: int = 600,
) -> str:
    """HMAC token for pagination/resync binding — not an auth grant."""
    secret = _signing_secret(settings)
    claims = {
        "runId": run_id,
        "latestSequence": int(latest_sequence),
        "schemaVersion": "1",
        "queryScopeHash": _sha_scope(query_scope or {"resource": "run.snapshot"}),
        "expiresAt": int(time.time()) + min(ttl_seconds, 3600),
        "statusVersion": int(status_version),
        "deliveryRevision": delivery_revision,
    }
    raw = (
        base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    sig = hmac.new(secret, raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def validate_snapshot_binding(
    settings: Settings,
    token: str,
    *,
    run_id: str,
    expected_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if len(token) > 8192 or "\n" in token or "\r" in token:
        raise SnapshotError("snapshot_stale", "invalid snapshot token")
    secret = _signing_secret(settings)
    try:
        raw, sig = token.split(".", 1)
    except ValueError as exc:
        raise SnapshotError("snapshot_stale", "malformed snapshot token") from exc
    expected = hmac.new(secret, raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise SnapshotError("snapshot_stale", "snapshot token signature mismatch")
    pad = "=" * (-len(raw) % 4)
    claims = json.loads(base64.urlsafe_b64decode(raw + pad))
    if claims.get("runId") != run_id:
        raise SnapshotError("snapshot_stale", "snapshot token run mismatch")
    if int(claims.get("expiresAt") or 0) <= int(time.time()):
        raise SnapshotError("snapshot_stale", "snapshot token expired")
    scope_hash = _sha_scope(expected_scope or {"resource": "run.snapshot"})
    if claims.get("queryScopeHash") != scope_hash:
        raise SnapshotError("snapshot_stale", "snapshot token scope mismatch")
    return cast(dict[str, Any], claims)


def make_page_cursor(
    *,
    run_id: str,
    resource: str,
    last_key: dict[str, Any],
    filter_hash: str,
    as_of_sequence: int,
) -> str:
    payload = {
        "runId": run_id,
        "resource": resource,
        "lastKey": last_key,
        "filterHash": filter_hash,
        "asOfSequence": as_of_sequence,
    }
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).decode()
    return raw.rstrip("=")


def parse_page_cursor(cursor: str, *, run_id: str, resource: str) -> dict[str, Any]:
    if len(cursor) > 4096 or "\n" in cursor or "\r" in cursor:
        raise SnapshotError("snapshot_stale", "invalid page cursor")
    pad = "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + pad))
    except Exception as exc:  # noqa: BLE001
        raise SnapshotError("snapshot_stale", "malformed page cursor") from exc
    if payload.get("runId") != run_id or payload.get("resource") != resource:
        raise SnapshotError("snapshot_stale", "page cursor scope mismatch")
    return cast(dict[str, Any], payload)


def build_bounded_snapshot(
    session: Session,
    settings: Settings,
    run: Run,
) -> RunSnapshot:
    """Construct DTO inside the open transaction — no lazy ORM escape."""
    latest_sequence = int(run.last_sequence or 0)
    latest_event_id = None
    if latest_sequence > 0:
        event = session.scalar(
            select(Event).where(Event.run_id == run.id, Event.sequence == latest_sequence)
        )
        if event is None:
            window = session.get(EventReplayWindowRow, run.id)
            archived = int(window.last_archived_sequence) if window is not None else 0
            if latest_sequence > archived:
                raise SnapshotError(
                    "storage_integrity",
                    f"missing event for sequence {latest_sequence}",
                )
        else:
            latest_event_id = event.event_id

    floor = 0
    window = session.get(EventReplayWindowRow, run.id)
    if window is not None:
        floor = int(window.replay_floor_sequence)

    thread = session.scalar(
        select(DriverThreadRow).where(
            DriverThreadRow.run_id == run.id, DriverThreadRow.status == "active"
        )
    )
    selection = session.scalar(
        select(IntegrationSelectionRow)
        .where(IntegrationSelectionRow.run_id == run.id)
        .order_by(IntegrationSelectionRow.created_at.desc())
        .limit(1)
    )

    delivery = session.get(RunDeliveryRow, run.id)
    delivery_overview = None
    delivery_revision = None
    if delivery is not None:
        delivery_revision = int(delivery.delivery_revision)
        delivery_payload: dict[str, Any] = {
            "currentIntegrationVersion": delivery.current_version_id,
            "currentManifestId": delivery.current_manifest_id,
            "manifestHash": None,
            "manifestRevision": None,
            "technicalStatus": delivery.technical_status,
            "acceptanceStatus": delivery.acceptance_status,
            "deliveryRevision": delivery.delivery_revision,
            "invalidatedReason": delivery.invalidated_reason,
        }
        if delivery.current_manifest_id:
            from agentforge.storage.models import DeliveryManifestRow

            manifest = session.get(DeliveryManifestRow, delivery.current_manifest_id)
            if manifest is not None:
                delivery_payload["manifestHash"] = manifest.manifest_hash
                delivery_payload["manifestRevision"] = manifest.revision
        delivery_overview = DeliveryOverview.model_validate(delivery_payload)

    stages = list(session.scalars(select(StageRow).where(StageRow.run_id == run.id)).all())
    stage_summary = StageSummary(
        total=len(stages),
        pending=sum(1 for s in stages if s.status in {"pending", "ready"}),
        executing=sum(1 for s in stages if s.status in {"executing", "submission_requested"}),
        reviewing=sum(1 for s in stages if s.status in {"reviewing", "frozen"}),
        rework=sum(1 for s in stages if s.status == "rework"),
        released=sum(1 for s in stages if s.status == "released"),
        blocked=sum(1 for s in stages if s.status == "blocked"),
        failed=sum(1 for s in stages if s.status == "failed"),
    )

    open_waits = list(
        session.scalars(
            select(RunWaitRow).where(RunWaitRow.run_id == run.id, RunWaitRow.status == "open")
        ).all()
    )
    by_reason: dict[str, int] = {}
    for wait in open_waits:
        by_reason[wait.reason] = by_reason.get(wait.reason, 0) + 1

    instances = list(
        session.scalars(select(AgentInstanceRow).where(AgentInstanceRow.run_id == run.id)).all()
    )
    env_id = "local"
    if instances:
        env_id = instances[0].execution_environment or "local"
    agent_counts = summarize_agent_counts(
        session, environment_id=env_id, run_id=run.id, as_of=_now(session)
    )
    progress = summarize_progress(session, run)

    overview = RunOverview.model_validate(
        {
            "id": run.id,
            "projectId": run.project_id,
            "goal": run.goal,
            "mode": run.mode,
            "status": run.status,
            "statusVersion": run.status_version,
            "planVersion": run.plan_version,
            "assignmentVersion": run.assignment_version,
            "gatePolicyVersion": run.gate_policy_version,
            "activeGeneration": run.active_generation,
            "executionProfile": run.execution_profile,
            "failurePolicy": run.failure_policy,
            "budget": BudgetLimits.model_validate(run.budget or {}),
            "planningProviderProfileId": run.planning_provider_profile_id,
            "baselineId": run.baseline_id,
            "inputRefs": list(run.input_refs or []),
            "outputs": list(run.outputs or []),
            "cancelRequestedAt": run.cancel_requested_at,
            "collaborationState": run.collaboration_state,
            "collaborationReadyAt": run.collaboration_ready_at,
            "createdAt": run.created_at,
            "updatedAt": run.updated_at,
        }
    )
    handoff_hash = selection.handoff_hash if selection is not None else None
    if handoff_hash is not None and len(handoff_hash) != 64:
        handoff_hash = None
    binding = ExecutionBindingOverview.model_validate(
        {
            "planVersion": run.plan_version,
            "assignmentVersion": run.assignment_version,
            "gatePolicyVersion": run.gate_policy_version,
            "activeGeneration": run.active_generation,
            "handoffRef": selection.handoff_ref if selection is not None else None,
            "handoffHash": handoff_hash,
            "graphVersion": thread.graph_version if thread is not None else None,
            "bindingHash": thread.binding_hash if thread is not None else None,
        }
    )
    token = make_snapshot_token(
        settings,
        run_id=run.id,
        latest_sequence=latest_sequence,
        status_version=int(run.status_version),
        delivery_revision=delivery_revision,
    )
    return RunSnapshot.model_validate(
        {
            "schemaVersion": "1",
            "run": overview.model_dump(mode="json", by_alias=True),
            "binding": binding.model_dump(mode="json", by_alias=True),
            "agentCounts": agent_counts.model_dump(mode="json", by_alias=True),
            "stageSummary": stage_summary.model_dump(mode="json", by_alias=True),
            "progress": progress.model_dump(mode="json", by_alias=True),
            "currentDelivery": (
                delivery_overview.model_dump(mode="json", by_alias=True)
                if delivery_overview
                else None
            ),
            "waitSummary": WaitSummary(open_count=len(open_waits), by_reason=by_reason).model_dump(
                mode="json", by_alias=True
            ),
            "latestSequence": latest_sequence,
            "latestEventId": latest_event_id,
            "replayFloorSequence": floor,
            "snapshotToken": token,
            "inventoryRevision": agent_counts.inventory_revision,
        }
    )


def load_run_snapshot(
    engine: Engine,
    settings: Settings,
    *,
    principal: AuthenticatedPrincipal,
    run_id: str,
) -> RunSnapshot:
    """REPEATABLE READ + READ ONLY short transaction; returns plain DTO."""
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        with conn.begin():
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=conn, autoflush=False, expire_on_commit=False) as session:
                try:
                    run = authorize_run_read(session, principal, run_id)
                except QueryAuthError as exc:
                    raise SnapshotError(exc.code, exc.message) from exc
                return build_bounded_snapshot(session, settings, run)
