"""D12.14: SSE replay floor / gap behavior on real PostgreSQL."""

from __future__ import annotations

import os
import uuid

import pytest

from agentforge.core.config import Settings
from agentforge.queries.authorization import AuthenticatedPrincipal
from agentforge.storage.database import create_database_engine
from agentforge.storage.models import EventReplayWindowRow, Project, Run
from agentforge.storage.repositories.events import append_domain_fact, ensure_replay_window
from agentforge.storage.uow import make_session_factory, unit_of_work
from agentforge.streaming.service import read_bounded_batch

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


def test_replay_below_floor_requires_resync(engine) -> None:
    """窗口推进: afterSequence < floor → resync_required, no fake continuity."""
    factory = make_session_factory(engine)
    suffix = uuid.uuid4().hex[:10]
    run_id = f"run_sse_{suffix}"
    principal = AuthenticatedPrincipal("c1", 1, "user:api")
    with unit_of_work(factory) as uow:
        uow.session.add(
            Project(
                id=f"proj_{suffix}",
                directory_grant_id=f"grant_{suffix}",
                root_path=f"D:/tmp/{suffix}",
                workspace_root=f"D:/tmp/{suffix}/workspace",
            )
        )
        uow.flush()
        uow.session.add(
            Run(
                id=run_id,
                project_id=f"proj_{suffix}",
                goal="sse replay",
                mode="single",
                status="running",
                execution_profile="container",
                failure_policy="dependency-aware",
                budget={"maxTokens": 10},
            )
        )
        uow.flush()
        for i in range(3):
            append_domain_fact(
                uow.session,
                run_id,
                "run.status_changed",
                {"status": "running", "statusVersion": i + 1},
            )
        ensure_replay_window(uow.session, run_id)
        win = uow.session.get(EventReplayWindowRow, run_id)
        assert win is not None
        win.replay_floor_sequence = 2
        uow.session.flush()

    batch = read_bounded_batch(
        engine,
        principal=principal,
        run_id=run_id,
        after_sequence=0,
    )
    assert batch.resync_required is True
