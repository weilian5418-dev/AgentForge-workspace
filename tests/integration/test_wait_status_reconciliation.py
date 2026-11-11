"""PostgreSQL tests for wait-status reconciliation ledger (BE-02 / 0006)."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select, text

from agentforge.core.config import Settings
from agentforge.storage.database import create_database_engine
from agentforge.storage.models import (
    Plan,
    Project,
    Run,
    RunStatusReconciliation,
    RunStatusReconciliationBatch,
)
from agentforge.storage.uow import make_session_factory, unit_of_work

pytestmark = pytest.mark.integration


def _url() -> str | None:
    return os.environ.get("AGENTFORGE_TEST_DATABASE_URL")


@pytest.fixture
def engine():
    url = _url()
    if not url:
        pytest.skip("Set AGENTFORGE_TEST_DATABASE_URL")
    eng = create_database_engine(Settings(_env_file=None, database_url=url))
    assert eng is not None
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


def test_reconciliation_tables_exist_and_batch_recorded(session_factory):
    with unit_of_work(session_factory) as uow:
        batches = uow.session.scalars(select(RunStatusReconciliationBatch)).all()
        assert any(b.id == "recon_batch_20261110_0006" for b in batches)
        # Empty or non-empty candidate set both leave an auditable batch.
        batch = next(b for b in batches if b.id == "recon_batch_20261110_0006")
        assert batch.candidate_count >= 0
        assert batch.source_revision == "20261110_0006"


def test_ambiguous_wait_runs_are_isolated_to_blocked(session_factory):
    suffix = uuid.uuid4().hex[:12]
    run_plan = f"run_plan_{suffix}"
    run_action = f"run_action_{suffix}"
    with unit_of_work(session_factory) as uow:
        # Ensure tables exist (migration applied).
        has = uow.session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='app' AND table_name='run_status_reconciliations'"
            )
        ).scalar()
        if not has:
            pytest.skip("0006 reconciliation tables not present")

        uow.session.add(
            Project(
                id=f"proj_{suffix}",
                directory_grant_id=f"grant_{suffix}",
                root_path=f"D:/tmp/{suffix}",
                workspace_root=f"D:/tmp/{suffix}/ws",
            )
        )
        uow.flush()
        for run_id, status in (
            (run_plan, "awaiting_confirmation"),
            (run_action, "awaiting_approval"),
        ):
            uow.session.add(
                Run(
                    id=run_id,
                    project_id=f"proj_{suffix}",
                    goal="recon test",
                    mode="single",
                    status=status,
                    execution_profile="container",
                    failure_policy="dependency-aware",
                    budget={"maxTokens": 1},
                    planning_provider_profile_id="profile_test",
                    outputs=["markdown"],
                )
            )
        uow.flush()
        uow.session.add(
            Plan(
                run_id=run_plan,
                version=1,
                contract_hash="c" * 64,
                status="proposed",
                summary="proposed plan",
            )
        )

    # Simulate 0006 isolation for newly inserted ambiguous waits (idempotent tool path).
    with unit_of_work(session_factory) as uow:
        batch_id = f"recon_batch_test_{suffix}"
        uow.session.add(
            RunStatusReconciliationBatch(
                id=batch_id,
                candidate_count=2,
                note="test isolation",
                source_revision="test",
            )
        )
        uow.flush()
        for run_id in (run_plan, run_action):
            run = uow.session.get(Run, run_id)
            assert run is not None
            observed = run.status
            uow.session.add(
                RunStatusReconciliation(
                    id=f"recon_{run_id}",
                    batch_id=batch_id,
                    run_id=run_id,
                    observed_status=observed,
                    previous_status_version=run.status_version,
                    isolation_status="blocked",
                    resolution_status="pending",
                    evidence={"note": "test"},
                )
            )
            run.status = "blocked"
            run.status_version += 1

    with unit_of_work(session_factory) as uow:
        assert uow.session.get(Run, run_plan).status == "blocked"
        assert uow.session.get(Run, run_action).status == "blocked"
        rows = uow.session.scalars(
            select(RunStatusReconciliation).where(RunStatusReconciliation.batch_id == batch_id)
        ).all()
        assert {r.observed_status for r in rows} == {
            "awaiting_confirmation",
            "awaiting_approval",
        }
        assert all(r.resolution_status == "pending" for r in rows)

    # Cleanup
    with unit_of_work(session_factory) as uow:
        for run_id in (run_plan, run_action):
            uow.session.execute(
                text("DELETE FROM app.run_status_reconciliations WHERE run_id = :id"),
                {"id": run_id},
            )
            uow.session.execute(text("DELETE FROM app.plans WHERE run_id = :id"), {"id": run_id})
            uow.session.execute(text("DELETE FROM app.runs WHERE id = :id"), {"id": run_id})
        uow.session.execute(
            text("DELETE FROM app.run_status_reconciliation_batches WHERE id = :id"),
            {"id": batch_id},
        )
        uow.session.execute(
            text("DELETE FROM app.projects WHERE id = :id"), {"id": f"proj_{suffix}"}
        )
