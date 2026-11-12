"""Transaction coordinator: claim + commit, execute without DB locks, then persist result."""

from typing import Any

from sqlalchemy import Engine

from agentforge.contracts.runner import RunnerExecuteRequest, RunnerTicket
from agentforge.runner.paths import RunnerPathError, resolve_workspace_root
from agentforge.runner.profiles import execute_profile
from agentforge.runner.service import RunnerDispatch, RunnerService
from agentforge.storage.uow import unit_of_work_connection


def perform_runner_dispatch(dispatch: RunnerDispatch) -> dict[str, Any]:
    try:
        return execute_profile(
            dispatch.profile_id,
            workspace_root=resolve_workspace_root(dispatch.workspace_root),
            params=dispatch.params,
        )
    except RunnerPathError as exc:
        return {"status": "failed", "code": exc.code, "detail": exc.message}
    except Exception:
        return {"status": "unknown", "detail": "Execution outcome requires reconciliation"}


def execute_runner(engine: Engine, body: RunnerExecuteRequest) -> RunnerTicket:
    with unit_of_work_connection(engine) as uow:
        claim = RunnerService(uow.session).claim_execution(body)
    if isinstance(claim, RunnerTicket):
        return claim
    result = perform_runner_dispatch(claim)
    # If this transaction fails, the committed running claim blocks blind execution.
    with unit_of_work_connection(engine) as uow:
        return RunnerService(uow.session).finish_execution(claim, result)
