"""D11.12 PostgreSQL constraints: wake/wait uniqueness, lease fence, active attempt."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from agentforge.contracts.collaboration import LeaseFence
from agentforge.core.config import Settings
from agentforge.driver.fencing import DriverFenceLost, assert_driver_fence
from agentforge.storage.database import create_database_engine
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
    with eng.connect() as conn:
        exists = conn.execute(
            text("SELECT to_regclass('app.run_wake_signals') IS NOT NULL")
        ).scalar()
        if not exists:
            pytest.skip("D11 migration 20261112_0017 not applied")
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


def _seed_run(session, suffix: str) -> str:
    from agentforge.storage.models import DirectoryGrantRow, Project, Run

    now = datetime.now(UTC)
    grant_id = f"gr_d11_{suffix}"
    project_id = f"prj_d11_{suffix}"
    run_id = f"run_d11_{suffix}"
    session.add(
        DirectoryGrantRow(
            id=grant_id,
            authorized_user="local",
            environment_id="env_local",
            normalized_root=f"D:/tmp/d11_{suffix}",
            directory_identity=f"dir_d11_{suffix}",
            allowed_operations=["read", "write"],
            grant_version=1,
            status="active",
            created_at=now,
            revoked_at=None,
        )
    )
    session.add(
        Project(
            id=project_id,
            directory_grant_id=grant_id,
            display_name="d11",
            root_path=f"D:/tmp/d11_{suffix}",
            workspace_root=f"D:/tmp/d11_{suffix}",
            trust_policy={},
            environment_id="env_local",
            grant_version=1,
            current_baseline_id=f"bl_d11_{suffix}",
            grant_status="active",
            revision=1,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        Run(
            id=run_id,
            project_id=project_id,
            goal="d11",
            mode="team",
            status="running",
            status_version=1,
            active_generation=1,
            last_sequence=0,
            execution_profile="host_authorized",
            failure_policy="dependency-aware",
            budget={},
            outputs=["markdown"],
            collaboration_state="running",
            plan_version=1,
            assignment_version=1,
            created_at=now,
            updated_at=now,
        )
    )
    return run_id


def test_wake_signal_source_unique(session_factory) -> None:
    from agentforge.storage.models import RunWakeSignalRow

    suffix = uuid.uuid4().hex[:10]
    now = datetime.now(UTC)
    with unit_of_work(session_factory) as uow:
        uow.session.execute(text("SET search_path TO app, public"))
        run_id = _seed_run(uow.session, suffix)
        uow.session.add(
            RunWakeSignalRow(
                id=f"wak_{suffix}_1",
                run_id=run_id,
                source_kind="control",
                source_ref="same-ref",
                binding_json={},
                priority=100,
                status="pending",
                created_at=now,
            )
        )
    with unit_of_work(session_factory) as uow:
        uow.session.execute(text("SET search_path TO app, public"))
        uow.session.add(
            RunWakeSignalRow(
                id=f"wak_{suffix}_2",
                run_id=f"run_d11_{suffix}",
                source_kind="control",
                source_ref="same-ref",
                binding_json={},
                priority=100,
                status="pending",
                created_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            uow.session.flush()


def test_wait_key_unique_per_run(session_factory) -> None:
    from agentforge.storage.models import RunWaitRow

    suffix = uuid.uuid4().hex[:10]
    now = datetime.now(UTC)
    with unit_of_work(session_factory) as uow:
        uow.session.execute(text("SET search_path TO app, public"))
        run_id = _seed_run(uow.session, suffix)
        uow.session.add(
            RunWaitRow(
                id=f"wait_{suffix}_1",
                run_id=run_id,
                wait_key="stop:ia_1",
                reason="stop_verification",
                scope_json={},
                predicate_version=1,
                after_sequence=0,
                status="open",
                created_at=now,
            )
        )
    with unit_of_work(session_factory) as uow:
        uow.session.execute(text("SET search_path TO app, public"))
        uow.session.add(
            RunWaitRow(
                id=f"wait_{suffix}_2",
                run_id=f"run_d11_{suffix}",
                wait_key="stop:ia_1",
                reason="stop_verification",
                scope_json={},
                predicate_version=1,
                after_sequence=0,
                status="open",
                created_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            uow.session.flush()


def test_stolen_lease_rejects_old_fence(session_factory) -> None:
    from agentforge.storage.models import RunLease

    suffix = uuid.uuid4().hex[:10]
    now = datetime.now(UTC)
    with unit_of_work(session_factory) as uow:
        uow.session.execute(text("SET search_path TO app, public"))
        run_id = _seed_run(uow.session, suffix)
        uow.session.add(
            RunLease(
                run_id=run_id,
                owner="owner_new",
                token="tok_new",
                generation=2,
                expires_at=now + timedelta(minutes=5),
                updated_at=now,
            )
        )

    old = LeaseFence(
        run_id=f"run_d11_{suffix}",
        owner="owner_old",
        token="tok_old",
        lease_generation=1,
    )
    with unit_of_work(session_factory) as uow:
        uow.session.execute(text("SET search_path TO app, public"))
        with pytest.raises(DriverFenceLost):
            assert_driver_fence(uow.session, old, require_active_thread=False)
