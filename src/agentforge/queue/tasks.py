"""Procrastinate task definitions (Blueprint — bound by queue app)."""

from __future__ import annotations

from typing import Any

from procrastinate import Blueprint, RetryStrategy

from agentforge.queue.constants import (
    CONTROL_QUEUE_NAME,
    DEFAULT_QUEUE_NAME,
    DRIVER_QUEUE_NAME,
    TASK_MODEL_PROBE,
)

blueprint = Blueprint()


@blueprint.task(
    name="agentforge.execute_model_operation",
    queue=DEFAULT_QUEUE_NAME,
    pass_context=False,
)
def execute_model_operation(
    operation_id: str,
    outbox_id: str | None = None,
    task_type: str = TASK_MODEL_PROBE,
    task_version: int = 1,
    provider_config_version: int | None = None,
) -> str:
    from agentforge.core.config import Settings
    from agentforge.queue.executor import execute_operation

    return execute_operation(
        Settings(),
        operation_id=operation_id,
        outbox_id=outbox_id,
        task_type=task_type,
        task_version=task_version,
        provider_config_version=provider_config_version,
    )


@blueprint.task(name="agentforge.forward_approval", queue=DEFAULT_QUEUE_NAME, pass_context=False)
def forward_approval(approval_id: str, operation_id: str, task_version: int = 1) -> str:
    from agentforge.core.config import Settings
    from agentforge.permissions.service import process_pending_forward
    from agentforge.storage.database import create_database_engine

    if task_version != 1 or not operation_id:
        raise ValueError("Invalid approval dispatch version or operationId")
    engine = create_database_engine(Settings())
    if engine is None:
        raise RuntimeError("Database is not configured")
    try:
        return process_pending_forward(engine, approval_id, operation_id=operation_id).status
    finally:
        engine.dispose()


@blueprint.task(
    name="agentforge.collaboration_operation",
    queue=CONTROL_QUEUE_NAME,
    pass_context=False,
    retry=RetryStrategy(max_attempts=3, wait=2),
)
def collaboration_operation(
    intent_type: str, payload: dict[str, Any], task_version: int = 1
) -> str:
    from agentforge.artifacts.store import ArtifactStore
    from agentforge.core.config import Settings
    from agentforge.instances.worker import process_delivery, process_instance_operation
    from agentforge.stages.service import StageService
    from agentforge.stages.worker import process_submission
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    if task_version != 1:
        raise ValueError("Unsupported task version")
    settings = Settings()
    engine = create_database_engine(settings)
    if engine is None:
        raise ValueError("Database not configured")
    from agentforge.queue.control_identity import require_control_role

    try:
        require_control_role(engine)
        if intent_type.startswith("instance."):
            return str(process_instance_operation(engine, payload["operationId"])["status"])
        if intent_type == "message.deliver":
            return str(process_delivery(engine, payload["deliveryId"])["status"])
        if intent_type == "stage.stop_verify":
            from agentforge.stages.errors import StageError
            from agentforge.storage.models import StageRow, StageSubmissionRow

            try:
                result = process_submission(engine, settings, payload["submissionId"])
                return str(result["status"])
            except StageError as exc:
                with unit_of_work_connection(engine) as uow:
                    sub = uow.session.get(StageSubmissionRow, payload["submissionId"])
                    stage = uow.session.get(StageRow, sub.stage_id) if sub else None
                    if stage and exc.code != "submission_incomplete":
                        stage.status = "blocked"
                        stage.waiting_reason = "awaiting_stop_verification"
                raise
        if intent_type == "stage.evaluate":
            with unit_of_work_connection(engine) as uow:
                result = StageService(
                    uow.session, store=ArtifactStore(settings.state_dir / "artifact_store")
                ).apply_evaluation(payload["stageId"])
                return str(result.get("status", "released"))
        if intent_type == "stage.released":
            return "release_recorded"  # D11 consumes readiness, never bypasses eligibility.
        if intent_type == "workspace.prepare":
            from agentforge.driver.workspace_preparation import process_workspace_preparation

            return str(process_workspace_preparation(engine, payload, settings=settings)["status"])
        if intent_type == "recovery.inspect":
            return "recovery_inspect_deferred"  # D11.9 fills probe_case.
        raise ValueError("Unsupported collaboration intent")
    finally:
        engine.dispose()


@blueprint.task(
    name="agentforge.driver_step",
    queue=DRIVER_QUEUE_NAME,
    pass_context=False,
    retry=RetryStrategy(max_attempts=3, wait=2),
)
def driver_step(
    run_id: str, wake_signal_id: str | None = None, task_version: int = 1
) -> str:
    """Bounded Driver fragment. Full drive_once lands in D11.8; wake must not be dropped."""
    from agentforge.core.config import Settings
    from agentforge.driver.signals import claim_pending_signals, complete_observed_signals
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    if task_version != 1:
        raise ValueError("Unsupported task version")
    if not run_id:
        raise ValueError("driver_step requires run_id")
    settings = Settings()
    engine = create_database_engine(settings)
    if engine is None:
        raise ValueError("Database not configured")
    try:
        # Until drive_once exists, acknowledge the signal so sweeps do not storm.
        # D11.8 replaces this with fenced graph invoke.
        with unit_of_work_connection(engine) as uow:
            claimed = claim_pending_signals(
                uow.session,
                run_id=run_id,
                owner=f"driver_step:{wake_signal_id or 'none'}",
                limit=8,
            )
            ids = [s.wake_signal_id for s in claimed]
            if wake_signal_id and wake_signal_id not in ids:
                ids.append(wake_signal_id)
            complete_observed_signals(uow.session, ids)
            return f"acked:{len(ids)}"
    finally:
        engine.dispose()
