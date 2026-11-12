"""D12.14: snapshot/event visibility consistency on real PostgreSQL."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agentforge.core.config import Settings
from agentforge.storage.database import create_database_engine
from agentforge.storage.models import Event, Project, Run
from agentforge.storage.repositories.events import append_domain_fact
from agentforge.storage.uow import make_session_factory, unit_of_work

pytestmark = pytest.mark.integration


@pytest.fixture
def engine():
    url = os.environ.get("AGENTFORGE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set AGENTFORGE_TEST_DATABASE_URL")
    eng = create_database_engine(Settings(_env_file=None, database_url=url))
    assert eng is not None
    yield eng
    eng.dispose()


def test_uncommitted_event_invisible_until_commit(engine) -> None:
    """未提交事件: SSE/query path must not observe uncommitted Event rows."""
    factory = make_session_factory(engine)
    suffix = uuid.uuid4().hex[:10]
    run_id = f"run_snap_{suffix}"
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
                goal="snapshot consistency",
                mode="single",
                status="running",
                execution_profile="container",
                failure_policy="dependency-aware",
                budget={"maxTokens": 10},
            )
        )

    # Writer holds an open transaction with a flushed event
    writer_conn = engine.connect()
    writer_trans = writer_conn.begin()
    WriterSession = sessionmaker(bind=writer_conn, expire_on_commit=False, class_=Session)
    writer = WriterSession()
    try:
        append_domain_fact(
            writer,
            run_id,
            "run.status_changed",
            {"status": "running", "statusVersion": 1},
        )
        writer.flush()

        # Reader on a separate connection (committed snapshot)
        ReaderSession = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
        reader = ReaderSession()
        try:
            visible = list(
                reader.scalars(select(Event).where(Event.run_id == run_id)).all()
            )
            assert visible == []
        finally:
            reader.close()
    finally:
        writer.rollback()
        writer.close()
        writer_trans.rollback()
        writer_conn.close()

    # After rollback, still empty
    with unit_of_work(factory) as uow:
        leftover = list(
            uow.session.scalars(select(Event).where(Event.run_id == run_id)).all()
        )
        assert leftover == []
