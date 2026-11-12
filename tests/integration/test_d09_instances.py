"""D09.7: PostgreSQL acceptance for instance/session/delivery constraints."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from agentforge.core.config import Settings
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
            text("SELECT to_regclass('app.native_sessions') IS NOT NULL")
        ).scalar()
        if not exists:
            pytest.skip("D09 migration 20261112_0014 not applied")
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


def test_active_session_unique_per_instance(session_factory) -> None:
    """At most one active native session binding per instance."""
    suffix = uuid.uuid4().hex[:10]
    now = datetime.now(UTC)
    from agentforge.storage.models import (
        AgentInstanceRow,
        NativeSessionRow,
        Project,
        DirectoryGrantRow,
        Run,
    )

    with unit_of_work(session_factory) as uow:
        uow.session.execute(text("SET search_path TO app, public"))
        uow.session.add(
            DirectoryGrantRow(
                id=f"gr_d09_{suffix}",
                authorized_user="local",
                environment_id="env_local",
                normalized_root=f"D:/tmp/d09_{suffix}",
                directory_identity=f"dir_d09_{suffix}",
                allowed_operations=["read", "write"],
                grant_version=1,
                status="active",
                created_at=now,
                revoked_at=None,
            )
        )
        uow.session.add(
            Project(
                id=f"prj_d09_{suffix}",
                directory_grant_id=f"gr_d09_{suffix}",
                display_name="d09",
                root_path=f"D:/tmp/d09_{suffix}",
                workspace_root=f"D:/tmp/d09_{suffix}",
                trust_policy={},
                environment_id="env_local",
                grant_version=1,
                current_baseline_id=f"bl_d09_{suffix}",
                grant_status="active",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        uow.session.add(
            Run(
                id=f"run_d09_{suffix}",
                project_id=f"prj_d09_{suffix}",
                goal="d09",
                mode="team",
                status="preparing",
                status_version=1,
                active_generation=1,
                last_sequence=0,
                execution_profile="host_authorized",
                failure_policy="dependency-aware",
                budget={},
                outputs=["markdown"],
                created_at=now,
                updated_at=now,
            )
        )
        inst_id = f"inst_d09_{suffix}"
        uow.session.add(
            AgentInstanceRow(
                id=inst_id,
                assignment_id=f"asg_{suffix}",
                run_id=f"run_d09_{suffix}",
                role_id="role_dev",
                agent_profile_id=f"prof_{suffix}",
                lifecycle="active",
                activity="idle",
                status="running",
                execution_environment="env_local",
                created_at=now,
                updated_at=now,
            )
        )
        uow.session.add(
            NativeSessionRow(
                id=f"ns1_{suffix}",
                instance_id=inst_id,
                product_id="p_a",
                profile_id=f"prof_{suffix}",
                config_version="1",
                environment_id="env_local",
                session_ref="sess_1",
                create_operation_id=f"op1_{suffix}",
                binding_status="active",
                created_at=now,
            )
        )

    with pytest.raises(Exception):
        with unit_of_work(session_factory) as uow:
            uow.session.execute(text("SET search_path TO app, public"))
            uow.session.add(
                NativeSessionRow(
                    id=f"ns2_{suffix}",
                    instance_id=inst_id,
                    product_id="p_a",
                    profile_id=f"prof_{suffix}",
                    config_version="1",
                    environment_id="env_local",
                    session_ref="sess_2",
                    create_operation_id=f"op2_{suffix}",
                    binding_status="active",
                    created_at=now,
                )
            )
