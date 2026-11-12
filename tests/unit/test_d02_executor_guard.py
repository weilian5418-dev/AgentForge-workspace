"""Executor must not re-call providers for unknown/reconcile states."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from agentforge.core.config import Settings
from agentforge.queue.executor import execute_operation
from agentforge.storage.models import AsyncOperationRow, ModelCallRow


def test_executor_refuses_unknown_call(tmp_path) -> None:
    settings = Settings(state_dir=tmp_path / "st")
    op = AsyncOperationRow(
        id="op_x",
        operation_type="model.probe",
        status="running",
        result_ref="mcall_x",
        uncertain_side_effects=False,
        provider_id="prov",
        grant_id="mgr",
        provider_config_version=1,
        payload={},
        replay_count=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    call = ModelCallRow(
        id="mcall_x",
        provider_id="prov",
        provider_config_version=1,
        grant_id="mgr",
        grant_version=1,
        purpose="capability_probe",
        status="unknown",
        settlement_status="unresolved",
        request_hash="a" * 64,
        idempotency_key="idem_x",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    class _Session:
        def get(self, model: type, key: str) -> object | None:
            if model is AsyncOperationRow and key == "op_x":
                return op
            if model is ModelCallRow and key == "mcall_x":
                return call
            return None

        def flush(self) -> None:
            return None

    class _Uow:
        def __init__(self) -> None:
            self.session = _Session()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with (
        patch("agentforge.queue.executor.create_database_engine", return_value=object()),
        patch("agentforge.queue.executor.unit_of_work_connection", return_value=_Uow()),
        patch("agentforge.queue.executor.ModelGateway") as gw,
    ):
        result = execute_operation(settings, operation_id="op_x")
    assert result == "needs_reconciliation"
    gw.assert_not_called()
    assert op.status == "needs_reconciliation"
    assert op.uncertain_side_effects is True
