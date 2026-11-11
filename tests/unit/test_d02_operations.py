"""D02 async operation ledger and unknown-result guards."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentforge.queue.operations import (
    OperationError,
    accept_model_probe,
    complete_operation,
    get_operation,
    request_manual_replay,
)
from agentforge.storage.models import AsyncOperationRow, Outbox, ProviderProfileRow


class _MemSession:
    def __init__(self) -> None:
        self.store: dict[tuple[type, str], object] = {}
        self.added: list[object] = []

    def add(self, row: object) -> None:
        self.added.append(row)
        ident = getattr(row, "id", None)
        if ident is not None:
            self.store[(type(row), str(ident))] = row

    def flush(self) -> None:
        return None

    def get(self, model: type, key: str) -> object | None:
        return self.store.get((model, key))


def test_accept_probe_writes_outbox_without_secrets() -> None:
    session = _MemSession()
    provider = ProviderProfileRow(
        id="prov_1",
        adapter_type="ollama",
        base_url="http://127.0.0.1:11434",
        model_id="m",
        auth_mode="none",
        config_version=3,
        network_policy={},
        status="enabled",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(provider)
    accepted = accept_model_probe(
        session,  # type: ignore[arg-type]
        provider_id="prov_1",
        grant_id="mgr_1",
        probe_tools=False,
    )
    assert accepted.status == "accepted"
    outboxes = [r for r in session.added if isinstance(r, Outbox)]
    assert len(outboxes) == 1
    payload = outboxes[0].payload
    assert "operationId" in payload
    assert "secret" not in str(payload).lower()
    assert "password" not in str(payload).lower()
    assert payload["providerConfigVersion"] == 3


def test_replay_blocked_when_uncertain() -> None:
    session = _MemSession()
    op = AsyncOperationRow(
        id="op_1",
        operation_type="model.probe",
        status="failed",
        uncertain_side_effects=True,
        replay_count=0,
        payload={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(op)
    with pytest.raises(OperationError) as exc:
        request_manual_replay(session, "op_1")  # type: ignore[arg-type]
    assert exc.value.code == "replay_blocked"


def test_complete_keeps_succeeded_sticky() -> None:
    session = _MemSession()
    op = AsyncOperationRow(
        id="op_2",
        operation_type="model.probe",
        status="succeeded",
        result_ref="mcall_1",
        uncertain_side_effects=False,
        replay_count=0,
        payload={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(op)
    complete_operation(
        session,  # type: ignore[arg-type]
        "op_2",
        status="failed",
        result_ref=None,
        message="should ignore",
    )
    assert op.status == "succeeded"
    status = get_operation(session, "op_2")  # type: ignore[arg-type]
    assert status.status == "succeeded"
