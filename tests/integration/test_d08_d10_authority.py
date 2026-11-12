"""Real PostgreSQL authority/concurrency checks in randomly isolated schemas.

Copies current model columns/checks/indexes; cross-domain foreign keys are omitted.
This suite does not migrate app or certify deployment foreign keys/product interfaces.
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import ForeignKeyConstraint, MetaData, create_engine, select, text
from sqlalchemy.orm import Session

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.agents import ExecutionRequest, RoleAssignmentPutItem
from agentforge.contracts.plans import PlanDecisionRequest
from agentforge.contracts.runs import RunCreateRequest
from agentforge.contracts.stages import GateEvaluationResult, StageSubmissionRequest
from agentforge.instances.preparation import ExecutionPreparationService
from agentforge.instances.worker import process_instance_operation
from agentforge.planning.service import PlanningError, PlanningService
from agentforge.stages.errors import StageError
from agentforge.stages.service import StageService
from agentforge.storage import models as m
from agentforge.storage.base import Base
from agentforge.storage.uow import unit_of_work_connection
from tests.support.collaboration import seed

pytestmark = pytest.mark.integration


@pytest.fixture
def env(tmp_path, monkeypatch):
    url = os.environ.get("AGENTFORGE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set AGENTFORGE_TEST_DATABASE_URL; no default production migration")
    base = create_engine(url, pool_size=6, max_overflow=0)
    schema = "af_authority_" + uuid.uuid4().hex
    try:
        with base.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            metadata = MetaData()
            for table in Base.metadata.tables.values():
                copied = table.to_metadata(metadata, schema=schema)
                copied.foreign_keys.clear()
                for column in copied.columns:
                    column.foreign_keys.clear()
                for constraint in list(copied.constraints):
                    if isinstance(constraint, ForeignKeyConstraint):
                        copied.constraints.remove(constraint)
            metadata.create_all(connection)
        engine = base.execution_options(schema_translate_map={"app": schema})
        ledger, spec = seed(tmp_path / "project")
        with Session(engine) as session, session.begin():
            for row in ledger.added:
                session.add(row)
        monkeypatch.setattr(
            "agentforge.projects.grants.require_active_grant",
            lambda session, grant_id: session.get(m.DirectoryGrantRow, grant_id),
        )
        monkeypatch.setattr(
            "agentforge.planning.service.require_active_grant",
            lambda session, grant_id: session.get(m.DirectoryGrantRow, grant_id),
        )
        monkeypatch.setattr(
            "agentforge.planning.context.require_active_grant",
            lambda session, grant_id: session.get(m.DirectoryGrantRow, grant_id),
        )
        yield engine, spec, ArtifactStore(tmp_path / "objects")
    finally:
        assert schema.startswith("af_authority_") and schema[13:].isalnum()
        with base.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        base.dispose()


def test_concurrent_run_creation_same_key_has_single_result(env):
    engine, spec, store = env
    body = RunCreateRequest.model_validate(
        {
            "projectId": "project",
            "goal": "another goal",
            "outputs": ["markdown"],
            "budget": {"maxTokens": 1000},
            "idempotencyKey": "concurrent",
        }
    )
    barrier = threading.Barrier(2)

    def create():
        barrier.wait(timeout=10)
        with unit_of_work_connection(engine) as uow:
            return PlanningService(uow.session).create_run(body).run_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result(timeout=15) for f in [pool.submit(create), pool.submit(create)]]
    assert results[0] == results[1]
    with unit_of_work_connection(engine) as uow:
        with pytest.raises(PlanningError, match="different request"):
            PlanningService(uow.session).create_run(body.model_copy(update={"goal": "changed"}))


def test_confirmation_requires_current_verified_profile_and_is_atomic(env):
    engine, spec, store = env
    with unit_of_work_connection(engine) as uow:
        plan = uow.session.get(m.Plan, ("run", 1))
        plan.status = "proposed"
        plan.compilation_json = {"status": "valid", "issues": [], "warnings": []}
        run = uow.session.get(m.Run, "run")
        run.status = "awaiting_confirmation"
        service = PlanningService(uow.session)
        service.put_assignments(
            "run",
            plan_version=1,
            items=[
                RoleAssignmentPutItem(
                    roleId=spec.roles[0].role_id, agentProfileId="profile-" + spec.roles[0].role_id
                )
            ],
        )
        req = uow.session.get(m.PlanConfirmationRequestRow, run.current_confirmation_request_id)
        decision = PlanDecisionRequest.model_validate(
            {
                "decision": "approve",
                "confirmationRequestId": req.id,
                "planVersion": 1,
                "planHash": req.plan_hash,
                "assignmentVersion": req.assignment_version,
                "gatePolicyVersion": 1,
                "statusVersion": run.status_version,
            }
        )
    with unit_of_work_connection(engine) as uow:
        profile = uow.session.get(m.AgentProfileRow, "profile-" + spec.roles[0].role_id)
        profile.auth_status = "expired"
    with pytest.raises(PlanningError):
        with unit_of_work_connection(engine) as uow:
            PlanningService(uow.session).decide("run", decision)
    with Session(engine) as session:
        assert session.get(m.Plan, ("run", 1)).status == "proposed"
        assert session.scalar(select(m.PlanDecisionRow)) is None


def test_session_native_io_after_claim_commit_and_death_blocks_recreation(env):
    engine, spec, store = env
    with unit_of_work_connection(engine) as uow:
        plan = uow.session.get(m.Plan, ("run", 1))
        ExecutionPreparationService(uow.session).prepare(
            "run",
            ExecutionRequest(
                planHash=plan.plan_hash, planVersion=1, assignmentVersion=1, gatePolicyVersion=1
            ),
            actor="test",
            idempotency_key="prepare",
        )
        uow.session.flush()
        opid = uow.session.scalar(select(m.InstanceOperationRow.id))
    adapter = Mock()

    def native(**kwargs):
        with Session(engine) as observer:
            assert observer.get(m.InstanceOperationRow, opid).status == "unknown"
        assert Path(kwargs["context"]["workspaceRoot"]).is_dir()
        raise SystemExit("native created session; caller died")

    adapter.create_session.side_effect = native
    with pytest.raises(SystemExit):
        process_instance_operation(engine, opid, adapter_factory=lambda _: adapter)
    assert (
        process_instance_operation(engine, opid, adapter_factory=lambda _: adapter)["status"]
        == "unknown"
    )
    assert adapter.create_session.call_count == 1


def seed_stage(engine, spec, store):
    stage_id = "run:p1:" + spec.stages[0].stage_id
    wp = spec.work_packages[0]
    role = spec.roles[0]
    workspace = "workspace-" + role.role_id
    digest = store.put_bytes(b"immutable actual data")
    with unit_of_work_connection(engine) as uow:
        s = uow.session
        stage = s.get(m.StageRow, stage_id)
        stage.status = "executing"
        s.add(
            m.AgentInstanceRow(
                id="agent",
                assignment_id="assignment-" + role.role_id,
                run_id="run",
                role_id=role.role_id,
                agent_profile_id="profile-" + role.role_id,
                plan_version=1,
                assignment_version=1,
                gate_policy_version=1,
                generation=1,
                workspace_id=workspace,
                lifecycle="stopped",
                status="stopped",
                activity="idle",
                current_attempt_id="attempt",
                session_ref="native",
                execution_environment="env",
            )
        )
        s.add(
            m.InstanceAttemptRow(
                id="attempt",
                instance_id="agent",
                run_id="run",
                work_package_id=wp.id,
                stage_id=stage_id,
                generation=1,
                workspace_id=workspace,
            )
        )
        s.add(
            m.InstanceOperationRow(
                id="stop",
                instance_id="agent",
                attempt_id="attempt",
                kind="request_stop",
                status="succeeded",
                payload_json={
                    "stopProof": {
                        "source": "adapter_host",
                        "writesQuiesced": True,
                        "generation": 1,
                        "workspaceId": workspace,
                        "attemptId": "attempt",
                        "sessionRef": "native",
                        "checkedAt": datetime.now(UTC).isoformat(),
                    }
                },
            )
        )
        s.add(
            m.ArtifactCandidateRow(
                id="candidate",
                project_id="project",
                workspace_id=workspace,
                relative_path=wp.deliverables[0].relative_path,
                kind="markdown",
                content_hash=digest,
                manifest_hash="a" * 64,
                contract_id=wp.deliverables[0].id,
                contract_version=1,
                contract_hash="c" * 64,
                environment_hash="e" * 64,
                state="frozen",
                operation_id="stop",
                attempt_id="attempt",
                generation=1,
                stop_evidence_json={},
            )
        )
    with unit_of_work_connection(engine) as uow:
        service = StageService(uow.session, store=store)
        service.submit(
            stage_id,
            StageSubmissionRequest(
                workPackageIds=[wp.id], attemptRefs=["attempt"], idempotencyKey="submit"
            ),
            submitter_ref="user:test",
        )
        uow.session.flush()
        sub = uow.session.scalar(select(m.StageSubmissionRow))
        service.complete_stop_and_freeze(sub.id)
    return stage_id


def test_stage_without_reports_cannot_be_released_by_caller(env):
    engine, spec, store = env
    stage_id = seed_stage(engine, spec, store)
    with pytest.raises(StageError, match="validation_incomplete"):
        with unit_of_work_connection(engine) as uow:
            service = StageService(uow.session, store=store)
            assert service.evaluate(stage_id).outcome == "waiting"
            service.commit_release(
                stage_id,
                GateEvaluationResult(
                    outcome="release", reason="forged", allowedReleaseWorkPackageIds=["fake"]
                ),
            )
    with Session(engine) as s:
        assert s.get(m.StageRow, stage_id).status == "reviewing"


def test_release_double_controller_is_idempotent_and_preserves_evidence(env):
    engine, spec, store = env
    stage_id = seed_stage(engine, spec, store)
    with unit_of_work_connection(engine) as uow:
        c = uow.session.get(m.ArtifactCandidateRow, "candidate")
        uow.session.add(
            m.ArtifactValidationReportRow(
                id="report",
                candidate_id=c.id,
                validator_id="sha256_presence",
                validator_version="1",
                status="passed",
                content_hash=c.content_hash,
                contract_hash=c.contract_hash,
                environment_hash=c.environment_hash,
                source="validator_registry",
                evidence_refs=["content:" + c.content_hash],
                message="Integrity only",
            )
        )
    barrier = threading.Barrier(2)

    def release():
        barrier.wait(timeout=10)
        with unit_of_work_connection(engine) as uow:
            return StageService(uow.session, store=store).commit_release(stage_id).decision_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = [f.result(timeout=15) for f in [pool.submit(release), pool.submit(release)]]
    assert ids[0] == ids[1]
    with Session(engine) as s:
        assert len(s.scalars(select(m.ReleaseRecordRow)).all()) == 1
        assert s.get(m.GateDecisionRow, ids[0]).validation_refs == ["report"]


def test_confirmation_state_and_events_commit_together(env):
    engine, spec, store = env
    with unit_of_work_connection(engine) as uow:
        s = uow.session
        plan = s.get(m.Plan, ("run", 1))
        plan.status = "proposed"
        plan.compilation_json = {"status": "valid", "issues": [], "warnings": []}
        run = s.get(m.Run, "run")
        run.status = "awaiting_confirmation"
        service = PlanningService(s)
        service.put_assignments(
            "run",
            plan_version=1,
            items=[
                RoleAssignmentPutItem(
                    roleId=spec.roles[0].role_id, agentProfileId="profile-" + spec.roles[0].role_id
                )
            ],
        )
        req = s.get(m.PlanConfirmationRequestRow, run.current_confirmation_request_id)
        decision = PlanDecisionRequest.model_validate(
            {
                "decision": "approve",
                "confirmationRequestId": req.id,
                "planVersion": 1,
                "planHash": req.plan_hash,
                "assignmentVersion": req.assignment_version,
                "gatePolicyVersion": 1,
                "statusVersion": run.status_version,
            }
        )
    with unit_of_work_connection(engine) as uow:
        PlanningService(uow.session).decide("run", decision)
    with Session(engine) as s:
        run = s.get(m.Run, "run")
        assert run.status == "preparing" and run.assignment_version == decision.assignment_version
        events = s.scalars(
            select(m.Event).where(m.Event.run_id == "run").order_by(m.Event.sequence)
        ).all()
        assert [e.sequence for e in events] == [1, 2]
        assert events[-1].payload["status"] == "preparing"
        assert s.get(m.Plan, ("run", 1)).status == "approved"


def test_native_lane_serializes_send_and_stop_and_unlocks_after_exception(env):
    from agentforge.instances.errors import InstanceError
    from agentforge.instances.lanes import native_lane

    engine, spec, store = env

    def competing_stop():
        with pytest.raises(InstanceError) as error:
            with native_lane(engine, "agent"):
                pytest.fail("Stop overtook a pending native send")
        assert error.value.code == "native_lane_busy"

    with pytest.raises(RuntimeError, match="dispatch died"):
        with native_lane(engine, "agent"):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(competing_stop).result(timeout=10)
            raise RuntimeError("dispatch died")
    with native_lane(engine, "agent"):
        pass
