"""Unit coverage for wait-status reconciliation summary (no AttributeError on batch)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agentforge.core.config import Settings
from agentforge.storage.reconciliation import pending_reconciliation_summary


def test_summary_unconfigured_without_database(tmp_path) -> None:
    settings = Settings(_env_file=None, state_dir=tmp_path / "state")
    summary = pending_reconciliation_summary(settings)
    assert summary["status"] == "unconfigured"
    assert summary["pendingCount"] == 0
    assert summary["latestBatchId"] is None


def test_summary_reads_orm_batch_attributes(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        state_dir=tmp_path / "state",
        database_url="postgresql+psycopg://agentforge_api:x@127.0.0.1:5432/agentforge",
    )
    batch = SimpleNamespace(id="recon_batch_test", candidate_count=0)
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.scalar.return_value = 0
    session.scalars.return_value.first.return_value = batch

    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.execute.return_value.scalar.return_value = 1

    engine = MagicMock()
    engine.connect.return_value = conn

    with (
        patch("agentforge.storage.reconciliation.create_engine", return_value=engine),
        patch("agentforge.storage.reconciliation.Session", return_value=session),
    ):
        summary = pending_reconciliation_summary(settings)

    assert summary["status"] == "clear"
    assert summary["pendingCount"] == 0
    assert summary["latestBatchId"] == "recon_batch_test"
    assert summary["latestBatchCandidateCount"] == 0


def test_summary_pending_count_with_batch(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        state_dir=tmp_path / "state",
        database_url="postgresql+psycopg://agentforge_api:x@127.0.0.1:5432/agentforge",
    )
    batch = SimpleNamespace(id="recon_batch_pending", candidate_count=2)
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.scalar.return_value = 2
    session.scalars.return_value.first.return_value = batch

    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.execute.return_value.scalar.return_value = 1

    engine = MagicMock()
    engine.connect.return_value = conn

    with (
        patch("agentforge.storage.reconciliation.create_engine", return_value=engine),
        patch("agentforge.storage.reconciliation.Session", return_value=session),
    ):
        summary = pending_reconciliation_summary(settings)

    assert summary["status"] == "pending"
    assert summary["pendingCount"] == 2
    assert summary["latestBatchId"] == "recon_batch_pending"
    assert summary["latestBatchCandidateCount"] == 2
    assert "2 run(s) blocked" in summary["message"]
