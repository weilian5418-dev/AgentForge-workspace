"""Platform drain coordination (distinct from Agent request_stop)."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import create_engine, select, text

from agentforge.core.config import Settings
from agentforge.storage.models import AgentInstanceRow, ApprovalMappingRow

DrainVerification = Literal["verified_empty", "pending", "unknown"]


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
            approvals_exist = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'app' AND table_name = 'approval_mappings'"
                )
            ).scalar()
            if not instances_exist or not approvals_exist:
                return _result(
                    complete=False,
                    verification="unknown",
                    pending=pending_instances,
                    unconfirmed=unconfirmed,
                    message=(
                        "Platform admission set to drain; collaboration tables missing — "
                        "agent stop state unknown (not treated as empty)"
                    ),
                )

            instance_rows = conn.execute(
                select(
                    AgentInstanceRow.id,
                    AgentInstanceRow.status,
                    AgentInstanceRow.role_id,
                    AgentInstanceRow.session_ref,
                ).where(AgentInstanceRow.status.notin_(("stopped", "closed", "failed", "created")))
            ).all()
            for instance_row in instance_rows:
                pending_instances.append(
                    {
                        "instanceId": instance_row.id,
                        "status": instance_row.status,
                        "roleId": instance_row.role_id,
                        "sessionRef": instance_row.session_ref or "",
                    }
                )

            approval_rows = conn.execute(
                select(
                    ApprovalMappingRow.id,
                    ApprovalMappingRow.status,
                    ApprovalMappingRow.instance_id,
                    ApprovalMappingRow.native_request_id,
                ).where(
                    ApprovalMappingRow.status.in_(
                        ("pending", "user_decided", "forwarding", "delivery_unknown")
                    )
                )
            ).all()
            for approval_row in approval_rows:
                unconfirmed.append(
                    {
                        "approvalId": approval_row.id,
                        "status": approval_row.status,
                        "instanceId": approval_row.instance_id or "",
                        "nativeRequestId": approval_row.native_request_id or "",
                    }
                )
    except Exception as exc:  # noqa: BLE001 — drain must not crash CLI
        return _result(
            complete=False,
            verification="unknown",
            pending=pending_instances,
            unconfirmed=unconfirmed,
            message=(
                "Platform admission set to drain; agent coordination check failed: "
                f"{type(exc).__name__}"
            ),
        )
    finally:
        engine.dispose()

    if pending_instances or unconfirmed:
        return _result(
            complete=False,
            verification="pending",
            pending=pending_instances,
            unconfirmed=unconfirmed,
            message=(
                "Platform stopped accepting new work; agent drain coordination incomplete — "
                "pending instances or unconfirmed side effects remain"
            ),
        )
    return _result(
        complete=True,
        verification="verified_empty",
        pending=pending_instances,
        unconfirmed=unconfirmed,
        message=(
            "Platform admission set to drain; no pending agent instances or "
            "unconfirmed native approvals observed"
        ),
    )


def _result(
    *,
    complete: bool,
    verification: DrainVerification,
    pending: list[dict[str, str]],
    unconfirmed: list[dict[str, str]],
    message: str,
) -> dict[str, Any]:
    return {
        "drainComplete": complete,
        "verification": verification,
        "pendingAgentInstances": pending,
        "unconfirmedSideEffects": unconfirmed,
        "message": message,
    }
