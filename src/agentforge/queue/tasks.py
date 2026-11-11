"""Procrastinate task definitions (Blueprint — bound by queue app)."""

from __future__ import annotations

from procrastinate import Blueprint

from agentforge.queue.constants import DEFAULT_QUEUE_NAME, TASK_MODEL_PROBE

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
