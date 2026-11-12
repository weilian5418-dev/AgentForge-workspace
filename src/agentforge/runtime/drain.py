"""Platform drain coordination (distinct from Agent request_stop)."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from agentforge.core.config import Settings
from agentforge.storage.models import (
    AgentInstanceRow,
    ApprovalMappingRow,
    DeliveryAttemptRow,
    InstanceOperationRow,
    MessageDeliveryRow,
    RunnerOperationRow,
)

DrainVerification = Literal["verified_empty", "pending", "unknown"]


def collect_drain_report(session: Session) -> dict[str, Any]:
    """DB-session drain report — includes unknown ops behind failed/created instances."""
    pending_instances: list[dict[str, str]] = []
    unconfirmed: list[dict[str, str]] = []
    unknown_ops: list[dict[str, str]] = []

    for inst in session.scalars(select(AgentInstanceRow)).all():
        # Lifecycle owns stop state — flat status alone is not authority.
        # failed/created are not pending execution, but their unknown ops still count below.
        if inst.lifecycle in {"stopped", "closed", "failed", "created"}:
            continue
        pending_instances.append(
            {
                "instanceId": inst.id,
                "status": inst.status,
                "lifecycle": inst.lifecycle,
                "roleId": inst.role_id,
                "sessionRef": inst.session_ref or "",
            }
        )

    for op in session.scalars(
        select(InstanceOperationRow).where(InstanceOperationRow.status == "unknown")
    ).all():
        unknown_ops.append(
            {
                "kind": "instance_operation",
                "operationId": op.id,
                "instanceId": op.instance_id,
                "opKind": op.kind,
                "status": op.status,
            }
        )

    for delivery in session.scalars(
        select(MessageDeliveryRow).where(MessageDeliveryRow.transmission_status == "unknown")
    ).all():
        unknown_ops.append(
            {
                "kind": "delivery",
                "operationId": delivery.id,
                "instanceId": delivery.recipient_instance_id,
                "opKind": "message.deliver",
                "status": delivery.transmission_status,
            }
        )

    for attempt in session.scalars(
        select(DeliveryAttemptRow).where(DeliveryAttemptRow.transmission_status == "unknown")
    ).all():
        unknown_ops.append(
            {
                "kind": "delivery_attempt",
                "operationId": attempt.id,
                "instanceId": "",
                "opKind": "delivery_attempt",
                "status": attempt.transmission_status,
            }
        )

    for runner in session.scalars(
        select(RunnerOperationRow).where(
            RunnerOperationRow.status.in_(
                ("unknown", "issued", "running", "needs_reconciliation")
            )
        )
    ).all():
        unknown_ops.append(
            {
                "kind": "runner",
                "operationId": runner.id,
                "instanceId": "",
                "opKind": "runner",
                "status": runner.status,
            }
        )

    for approval in session.scalars(
        select(ApprovalMappingRow).where(
            ApprovalMappingRow.status.in_(
                ("pending", "user_decided", "forwarding", "delivery_unknown")
            )
        )
    ).all():
        unconfirmed.append(
            {
                "approvalId": approval.id,
                "status": approval.status,
                "instanceId": approval.instance_id or "",
                "nativeRequestId": approval.native_request_id or "",
            }
        )

    if pending_instances or unconfirmed or unknown_ops:
        return _result(
            complete=False,
            verification="pending",
            pending=pending_instances,
            unconfirmed=unconfirmed,
            unknown_ops=unknown_ops,
            message=(
                "Platform stopped accepting new work; drain incomplete — "
                "pending instances, unknown operations, or unconfirmed side effects remain"
            ),
        )
    return _result(
        complete=True,
        verification="verified_empty",
        pending=pending_instances,
        unconfirmed=unconfirmed,
        unknown_ops=unknown_ops,
        message=(
            "Platform admission set to drain; no pending instances, unknown operations, "
            "or unconfirmed native approvals observed"
        ),
    )


def collect_drain_coordination(settings: Settings) -> dict[str, Any]:
    """Inspect managed agent instances / unconfirmed approvals for drain status.

    ``drainComplete`` is True only when collaboration tables are present and
    verified empty of pending work. Missing DB or missing tables → unknown
    (incomplete), never treated as "safely stopped".
    """
    pending_instances: list[dict[str, str]] = []
    unconfirmed: list[dict[str, str]] = []
    if settings.database_url is None:
        return _result(
            complete=False,
            verification="unknown",
            pending=pending_instances,
            unconfirmed=unconfirmed,
            unknown_ops=[],
            message=(
                "Platform admission set to drain; database not configured — "
                "cannot verify agent instance stop state"
            ),
        )

    engine = create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    try:
        with engine.connect() as conn:
            instances_exist = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'app' AND table_name = 'agent_instances'"
                )
            ).scalar()
            if not instances_exist:
                return _result(
                    complete=False,
                    verification="unknown",
                    pending=pending_instances,
                    unconfirmed=unconfirmed,
                    unknown_ops=[],
                    message=(
                        "Platform admission set to drain; collaboration tables missing — "
                        "agent stop state unknown (not treated as empty)"
                    ),
                )

        from agentforge.storage.uow import unit_of_work_connection

        with unit_of_work_connection(engine) as uow:
            return collect_drain_report(uow.session)
    except Exception as exc:  # noqa: BLE001 — drain must not crash CLI
        return _result(
            complete=False,
            verification="unknown",
            pending=pending_instances,
            unconfirmed=unconfirmed,
            unknown_ops=[],
            message=(
                "Platform admission set to drain; agent coordination check failed: "
                f"{type(exc).__name__}"
            ),
        )
    finally:
        engine.dispose()


def _result(
    *,
    complete: bool,
    verification: DrainVerification,
    pending: list[dict[str, str]],
    unconfirmed: list[dict[str, str]],
    message: str,
    unknown_ops: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "drainComplete": complete,
        "verification": verification,
        "pendingAgentInstances": pending,
        "unconfirmedSideEffects": unconfirmed,
        "unknownOperations": list(unknown_ops or []),
        "message": message,
    }
