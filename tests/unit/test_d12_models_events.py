"""D12.2: model registration and typed event write path."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from agentforge.storage.models import (
    APP_V14_TABLES,
    AcceptanceDecisionRow,
    DeliveryManifestRow,
    EventReplayWindowRow,
    IntegrationBuildRow,
    IntegrationSelectionRow,
    IntegrationVersionRow,
    RunDeliveryRow,
)
from agentforge.storage.repositories.events import EventRepository


def test_d12_tables_registered() -> None:
    required = {
        "event_replay_windows",
        "integration_selections",
        "integration_builds",
        "integration_validation_cycles",
        "integration_check_runs",
        "integration_check_reports",
        "integration_review_requests",
        "integration_review_reports",
        "delivery_manifests",
        "run_deliveries",
        "acceptance_decisions",
    }
    assert required <= set(APP_V14_TABLES)
    assert IntegrationVersionRow.__tablename__ == "integration_versions"
    assert EventReplayWindowRow.__tablename__ == "event_replay_windows"
    assert IntegrationSelectionRow.__tablename__ == "integration_selections"
    assert IntegrationBuildRow.__tablename__ == "integration_builds"
    assert DeliveryManifestRow.__tablename__ == "delivery_manifests"
    assert RunDeliveryRow.__tablename__ == "run_deliveries"
    assert AcceptanceDecisionRow.__tablename__ == "acceptance_decisions"


def test_append_with_outbox_validates_payload_before_sequence() -> None:
    uow = MagicMock()
    session = MagicMock()
    uow.session = session
    repo = EventRepository(uow)
    with pytest.raises(ValidationError):
        repo.append_with_outbox(
            run_id="run_1",
            event_type="acceptance.decided",
            payload={"decisionId": "x"},  # incomplete
            outbox_target="unused",
        )
    session.execute.assert_not_called()


def test_append_with_outbox_writes_typed_payload() -> None:
    uow = MagicMock()
    session = MagicMock()
    uow.session = session
    run = MagicMock(last_sequence=3)
    session.execute.return_value.scalar_one.return_value = run
    repo = EventRepository(uow)
    event, outbox = repo.append_with_outbox(
        run_id="run_1",
        event_type="acceptance.decided",
        payload={
            "decisionId": "ad_1",
            "decision": "accept",
            "manifestHash": "a" * 64,
            "versionId": "iv_1",
            "deliveryRevision": 2,
            "acceptanceStatus": "accepted",
            "isCurrent": True,
        },
        outbox_target="driver",
    )
    assert event.sequence == 4
    assert event.payload["decisionId"] == "ad_1"
    assert outbox.idempotency_key.startswith("event:")
    session.add.assert_called()


def test_list_after_rejects_below_replay_floor() -> None:
    uow = MagicMock()
    session = MagicMock()
    uow.session = session
    session.get.return_value = MagicMock(replay_floor_sequence=10)
    repo = EventRepository(uow)
    with pytest.raises(ValueError, match="cursor_expired"):
        repo.list_after("run_1", 5)


def test_migration_0018_revises_0017() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / (
        "migrations/versions/20261112_0018_d12_delivery_stream.py"
    )
    spec = importlib.util.spec_from_file_location("m0018", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "20261112_0018"
    assert mod.down_revision == "20261112_0017"
    with pytest.raises(RuntimeError, match="downgrade refused"):
        mod.downgrade()
