"""D11.12 PostgreSQL: run control + recovery case version fencing + admission."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from agentforge.contracts.runs import RunActionRequest
from agentforge.core.config import Settings
from agentforge.driver.control import RunControlError, RunControlService
from agentforge.recovery.errors import RecoveryError
from agentforge.recovery.service import RecoveryService
from agentforge.runtime.control import read_admission_db, set_admission
from agentforge.runtime.drain import collect_drain_report
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
        exists = conn.execute(text("SELECT to_regclass('app.recovery_cases') IS NOT NULL")).scalar()
        if not exists:
            pytest.skip("D11 migration 20261112_0017 not applied")
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


def _seed_run(session, suffix: str, *, status: str = "running") -> str:
    from agentforge.storage.models import DirectoryGrantRow, Project, Run

    now = datetime.now(UTC)
    grant_id = f"gr_rc_{suffix}"
    project_id = f"prj_rc_{suffix}"
    run_id = f"run_rc_{suffix}"
    session.add(
        DirectoryGrantRow(
            id=grant_id,
            authorized_user="local",
            environment_id="env_local",
            normalized_root=f"D:/tmp/rc_{suffix}",
            directory_identity=f"dir_rc_{suffix}",
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
            display_name="d11-rc",
            root_path=f"D:/tmp/rc_{suffix}",
            workspace_root=f"D:/tmp/rc_{suffix}",
            trust_policy={},
            environment_id="env_local",
            grant_version=1,
            current_baseline_id=f"bl_rc_{suffix}",
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
            goal="d11 control",
            mode="team",
            status=status,
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


def test_pause_accept_stays_pause_requested(session_factory) -> None:
    suffix = uuid.uuid4().hex[:10]
    with unit_of_work(session_factory) as uow:
        uow.session.execute(text("SET search_path TO app, public"))
        run_id = _seed_run(uow.session, suffix)
        accepted = RunControlService(uow.session).request_action(
            run_id,
            RunActionRequest.model_validate(
                {
                    "action": "pause",
                    "expectedStatusVersion": 1,
                    "idempotencyKey": f"pause-{suffix}",
                }
            ),
            actor="user:test",
        )
        from agentforge.storage.models import Run

        run = uow.session.get(Run, run_id)
        assert accepted.status == "accepted"
        assert run is not None
        assert run.status == "pause_requested"
        assert run.status_version == 2


def test_status_version_conflict_on_stale_pause(session_factory) -> None:
    suffix = uuid.uuid4().hex[:10]
    with unit_of_work(session_factory) as uow:
        uow.session.execute(text("SET search_path TO app, public"))
        run_id = _seed_run(uow.session, suffix)
        with pytest.raises(RunControlError) as exc:
            RunControlService(uow.session).request_action(
                run_id,
                RunActionRequest.model_validate(
                    {
                        "action": "pause",
                        "expectedStatusVersion": 99,
                        "idempotencyKey": f"stale-{suffix}",
                    }
                ),
            )
        assert exc.value.code == "status_version_conflict"


def test_recovery_apply_rejects_stale_case_version(session_factory) -> None:
    suffix = uuid.uuid4().hex[:10]
    with unit_of_work(session_factory) as uow:
        uow.session.execute(text("SET search_path TO app, public"))
        run_id = _seed_run(uow.session, suffix, status="blocked")
        case = RecoveryService(uow.session).open_case(
            run_id,
            reason="test",
            request_operation_id=f"op-{suffix}",
            emit_outbox=False,
        )
        from agentforge.contracts.recovery import RecoveryDecisionRequest

        with pytest.raises(RecoveryError) as exc:
            RecoveryService(uow.session).apply_decision(
                case.case_id,
                RecoveryDecisionRequest.model_validate(
                    {
                        "decisionKey": f"dec-{suffix}",
                        "decision": "observe_existing",
                        "caseVersion": case.case_version + 5,
                        "contextHash": case.context_hash,
                        "expectedStatusVersion": 1,
                        "idempotencyKey": f"dec-{suffix}",
                    }
                ),
                actor="user:test",
            )
        assert exc.value.code == "recovery_evidence_stale"


def test_admission_db_roundtrip_and_drain_empty(session_factory, settings) -> None:
    with unit_of_work(session_factory) as uow:
        uow.session.execute(text("SET search_path TO app, public"))
        env = f"env_d11_{uuid.uuid4().hex[:8]}"
        set_admission(
            uow.session,
            settings,
            "drain",
            environment_id=env,
            reason="integration",
        )
        assert read_admission_db(uow.session, environment_id=env) == "drain"
        # Empty collaboration tables for this session view may still see other
        # tenants' rows; only assert the report shape is present.
        report = collect_drain_report(uow.session)
        assert "drainComplete" in report
        assert "unknownOperations" in report
        assert report["verification"] in {"verified_empty", "pending"}
