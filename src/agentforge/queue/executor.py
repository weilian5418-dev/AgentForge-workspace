"""Execute durable model operations via ModelGateway (driver/API credentials)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from agentforge.contracts.providers import ProviderProbeRequest
from agentforge.core.config import Settings
from agentforge.models.gateway import ModelGateway, ModelGatewayError
from agentforge.queue.constants import TASK_MODEL_PROBE
from agentforge.queue.operations import complete_operation, mark_running
from agentforge.storage.database import create_database_engine
from agentforge.storage.models import AsyncOperationRow, ModelCallRow
from agentforge.storage.uow import unit_of_work_connection

logger = logging.getLogger(__name__)


def execute_operation(
    settings: Settings,
    *,
    operation_id: str,
    outbox_id: str | None = None,
    task_type: str = TASK_MODEL_PROBE,
    task_version: int = 1,
    provider_config_version: int | None = None,
) -> str:
    """Business-idempotent executor. Never blind-redoes unknown model calls."""
    del task_version  # reserved for future payload evolution
    engine = create_database_engine(settings)
    if engine is None:
        raise RuntimeError("Database is not configured")

    with unit_of_work_connection(engine) as uow:
        op = uow.session.get(AsyncOperationRow, operation_id)
        if op is None:
            logger.error("operation %s missing — dropping job", operation_id)
            return "missing"
        if op.status == "succeeded":
            return "already_succeeded"
        if op.status == "needs_reconciliation" or op.uncertain_side_effects:
            logger.warning(
                "operation %s needs reconciliation; refusing to re-call provider",
                operation_id,
            )
            return "needs_reconciliation"
        # F02: running with no result_ref means dispatch may have already happened.
        if op.status == "running" and not op.result_ref:
            complete_operation(
                uow.session,
                operation_id,
                status="needs_reconciliation",
                result_ref=None,
                message="Prior running attempt has no result_ref; reconcile before retry",
                uncertain=True,
            )
            return "needs_reconciliation"
        if op.result_ref:
            call = uow.session.get(ModelCallRow, op.result_ref)
            if call is not None and call.status == "unknown":
                complete_operation(
                    uow.session,
                    operation_id,
                    status="needs_reconciliation",
                    result_ref=call.id,
                    message="Prior call status unknown; reconcile before retry",
                    uncertain=True,
                )
                return "needs_reconciliation"
            if call is not None and call.status == "succeeded":
                complete_operation(
                    uow.session,
                    operation_id,
                    status="succeeded",
                    result_ref=call.id,
                    message="Replayed from existing call ledger",
                    uncertain=False,
                )
                return "already_succeeded"

        if (
            provider_config_version is not None
            and op.provider_config_version is not None
            and provider_config_version != op.provider_config_version
        ):
            complete_operation(
                uow.session,
                operation_id,
                status="blocked",
                result_ref=None,
                message="Provider config version mismatch; refusing dispatch",
                uncertain=False,
            )
            return "blocked"

        mark_running(uow.session, operation_id)

    if task_type != TASK_MODEL_PROBE:
        with unit_of_work_connection(engine) as uow:
            complete_operation(
                uow.session,
                operation_id,
                status="failed",
                result_ref=None,
                message=f"Unsupported taskType={task_type}",
            )
        return "failed"

    # Network outside the long ledger lock: open a new UoW around gateway.probe.
    try:
        with unit_of_work_connection(engine) as uow:
            op = uow.session.get(AsyncOperationRow, operation_id)
            assert op is not None
            if not op.provider_id or not op.grant_id:
                complete_operation(
                    uow.session,
                    operation_id,
                    status="failed",
                    result_ref=None,
                    message="Operation missing providerId/grantId",
                )
                return "failed"
            gateway = ModelGateway(uow.session, settings)
            probe = gateway.probe(
                op.provider_id,
                ProviderProbeRequest.model_validate(
                    {
                        "authorizeNetwork": True,
                        "grantId": op.grant_id,
                        "probeTools": bool((op.payload or {}).get("probeTools")),
                    }
                ),
                idempotency_key=f"probe_op_{operation_id}",
            )
            call_id = probe.call_id
            if call_id:
                call = uow.session.get(ModelCallRow, call_id)
                if call is not None and call.status == "unknown":
                    complete_operation(
                        uow.session,
                        operation_id,
                        status="needs_reconciliation",
                        result_ref=call_id,
                        message="Model call finished unknown; do not auto-retry",
                        uncertain=True,
                    )
                    return "needs_reconciliation"
            status = "succeeded" if probe.status == "succeeded" else "failed"
            if probe.status == "blocked":
                status = "blocked"
            complete_operation(
                uow.session,
                operation_id,
                status=status,
                result_ref=call_id,
                message=probe.message,
                uncertain=False,
            )
            if outbox_id:
                op = uow.session.get(AsyncOperationRow, operation_id)
                if op is not None:
                    op.updated_at = datetime.now(UTC)
            return status
    except ModelGatewayError as exc:
        with unit_of_work_connection(engine) as uow:
            # After dispatch_started inside gateway, failures may be uncertain.
            uncertain = exc.code in {
                "PROVIDER_UNAVAILABLE",
                "MODEL_CALL_UNKNOWN",
            }
            complete_operation(
                uow.session,
                operation_id,
                status="needs_reconciliation" if uncertain else "failed",
                result_ref=None,
                message=f"{exc.code}: {exc.message}",
                uncertain=uncertain,
            )
        return "needs_reconciliation" if uncertain else "failed"
