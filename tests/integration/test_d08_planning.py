"""D08.6: real PostgreSQL concurrency / side-effect acceptance for planning."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from agentforge.contracts.agents import RoleAssignmentPutItem
from agentforge.contracts.plans import PlanDecisionRequest, PlanProposalRequest
from agentforge.contracts.runs import RunCreateRequest
from agentforge.core.config import Settings
from agentforge.planning.service import PlanningError, PlanningService
from agentforge.storage.database import create_database_engine
from agentforge.storage.models import (
    AgentInstanceRow,
    DirectoryGrantRow,
    Project,
    ProjectBaselineRow,
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
    # Require D08 migration tables.
    with eng.connect() as conn:
        exists = conn.execute(
            text("SELECT to_regclass('app.plan_confirmation_requests') IS NOT NULL")
        ).scalar()
        if not exists:
            pytest.skip("D08 migration 20261111_0013 not applied")
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


def _seed_project(factory) -> str:
    suffix = uuid.uuid4().hex[:10]
    project_id = f"prj_d08_{suffix}"
    now = datetime.now(UTC)
    with unit_of_work(factory) as uow:
        uow.session.add(
            DirectoryGrantRow(
                id=f"gr_d08_{suffix}",
                authorized_user="local",
                environment_id="env_local",
                normalized_root=f"D:/tmp/d08_{suffix}",
                directory_identity=f"dir_d08_{suffix}",
                allowed_operations=["read", "write"],
                grant_version=1,
                status="active",
                created_at=now,
                revoked_at=None,
            )
        )
        uow.session.add(
            Project(
                id=project_id,
                directory_grant_id=f"gr_d08_{suffix}",
                display_name="d08",
                root_path=f"D:/tmp/d08_{suffix}",
                workspace_root=f"D:/tmp/d08_{suffix}",
                trust_policy={},
                environment_id="env_local",
                grant_version=1,
                current_baseline_id=f"bl_d08_{suffix}",
                grant_status="active",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        uow.session.add(
            ProjectBaselineRow(
                id=f"bl_d08_{suffix}",
                project_id=project_id,
                environment_id="env_local",
                dirty_policy="committed_only",
                status="ready",
                is_git=False,
                git_commit=None,
                git_dirty_summary={},
                manifest_hash=None,
                scope_strategy="authorized_root",
                notes=[],
                checked_at=now,
                created_at=now,
            )
        )
    return project_id


def test_approve_idempotent_and_no_agent_instances(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(
        "agentforge.planning.service.require_active_grant",
        lambda session, grant_id: session.get(DirectoryGrantRow, grant_id),
    )
    monkeypatch.setattr(
        "agentforge.planning.context.require_active_grant",
        lambda session, grant_id: session.get(DirectoryGrantRow, grant_id),
    )
    project_id = _seed_project(session_factory)
    with unit_of_work(session_factory) as uow:
        svc = PlanningService(uow.session)
        accepted = svc.create_run(
            RunCreateRequest.model_validate(
                {
                    "projectId": project_id,
                    "goal": "d08 integration",
                    "outputs": ["markdown"],
                    "budget": {},
                    "idempotencyKey": f"idem-{uuid.uuid4().hex[:8]}",
                }
            )
        )
        plan = svc.propose(accepted.run_id, PlanProposalRequest.model_validate({"source": "rules"}))
        assert plan.compilation and plan.compilation.status == "valid"
        svc.put_assignments(
            accepted.run_id,
            plan_version=plan.plan_version,
            items=[
                RoleAssignmentPutItem.model_validate(
                    {"roleId": "role_writer", "agentProfileId": "aprof_d08"}
                )
            ],
        )
        plan2 = svc.get_plan(accepted.run_id)
        run = svc.get_run(accepted.run_id)
        assert plan2.confirmation is not None
        body = {
            "decision": "approve",
            "confirmationRequestId": plan2.confirmation.request_id,
            "planVersion": plan2.plan_version,
            "planHash": plan2.plan_hash,
            "assignmentVersion": run.assignment_version,
            "gatePolicyVersion": plan2.gate_policy_version,
            "statusVersion": run.status_version,
        }
        first = svc.decide(accepted.run_id, PlanDecisionRequest.model_validate(body))
        second = svc.decide(accepted.run_id, PlanDecisionRequest.model_validate(body))
        assert first.status == "approved"
        assert second.status == "approved"
        assert svc.get_run(accepted.run_id).status == "preparing"
        agents = list(
            uow.session.scalars(
                select(AgentInstanceRow).where(AgentInstanceRow.run_id == accepted.run_id)
            ).all()
        )
        assert agents == []


def test_concurrent_decide_vs_stale_status_version(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(
        "agentforge.planning.service.require_active_grant",
        lambda session, grant_id: session.get(DirectoryGrantRow, grant_id),
    )
    monkeypatch.setattr(
        "agentforge.planning.context.require_active_grant",
        lambda session, grant_id: session.get(DirectoryGrantRow, grant_id),
    )
    project_id = _seed_project(session_factory)

    with unit_of_work(session_factory) as uow:
        svc = PlanningService(uow.session)
        accepted = svc.create_run(
            RunCreateRequest.model_validate(
                {
                    "projectId": project_id,
                    "goal": "d08 race",
                    "outputs": ["markdown"],
                    "budget": {},
                }
            )
        )
        plan = svc.propose(accepted.run_id, PlanProposalRequest.model_validate({"source": "rules"}))
        svc.put_assignments(
            accepted.run_id,
            plan_version=plan.plan_version,
            items=[
                RoleAssignmentPutItem.model_validate(
                    {"roleId": "role_writer", "agentProfileId": "aprof_d08"}
                )
            ],
        )
        plan2 = svc.get_plan(accepted.run_id)
        run = svc.get_run(accepted.run_id)
        payload = {
            "decision": "approve",
            "confirmationRequestId": plan2.confirmation.request_id,
            "planVersion": plan2.plan_version,
            "planHash": plan2.plan_hash,
            "assignmentVersion": run.assignment_version,
            "gatePolicyVersion": plan2.gate_policy_version,
            "statusVersion": run.status_version,
        }
        run_id = accepted.run_id

    def _decide(label: str) -> str:
        with unit_of_work(session_factory) as inner:
            try:
                PlanningService(inner.session).decide(
                    run_id, PlanDecisionRequest.model_validate(payload), actor=label
                )
                return "ok"
            except PlanningError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_decide, f"t{i}") for i in range(2)]
        results = [f.result() for f in as_completed(futures)]

    assert "ok" in results
    # Second either idempotent ok or loses on conflict / pending / version.
    assert all(
        r in {"ok", "decision_conflict", "confirmation_not_pending", "status_version_conflict"}
        for r in results
    )
