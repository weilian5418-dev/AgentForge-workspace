"""Real PostgreSQL and filesystem regressions, randomly isolated schema.

Columns/checks/indexes are copied; cross-domain FKs are intentionally omitted.
This proves transaction behavior, not a production migration or live Agent integration.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import ForeignKeyConstraint, MetaData, create_engine, select, text

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.delivery import AcceptanceDecisionRequest
from agentforge.contracts.integrations import (
    IntegrationBuildRequest,
    IntegrationSelectionRequest,
    ValidationCycleRequest,
)
from agentforge.contracts.plans import PlanSpec
from agentforge.core.config import Settings
from agentforge.delivery.acceptance import AcceptanceError, decide_acceptance
from agentforge.delivery.manifest import (
    build_and_publish_manifest,
    publish_manifest,
)
from agentforge.driver.binding import sha256_hex
from agentforge.integration.check_worker import (
    CheckWorkerError,
    process_check_run,
    start_validation_for_version,
)
from agentforge.integration.coordinator import process_integration_build, request_integration_build
from agentforge.integration.selection import create_selection
from agentforge.planning.hashing import compute_plan_hash
from agentforge.queries.authorization import api_principal
from agentforge.storage import models as m
from agentforge.storage.base import Base
from agentforge.storage.uow import unit_of_work_connection
from tests.support.collaboration import seed

pytestmark = pytest.mark.integration


@pytest.fixture
def env(tmp_path):
    url = os.environ.get("AGENTFORGE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Explicit test URL required; existing business DB is never migrated")
    base = create_engine(url, hide_parameters=True, pool_size=6)
    schema = "af_hardening_" + uuid.uuid4().hex
    try:
        with base.begin() as c:
            c.execute(text(f'CREATE SCHEMA "{schema}"'))
            meta = MetaData()
            for table in Base.metadata.tables.values():
                copy = table.to_metadata(meta, schema=schema)
                copy.foreign_keys.clear()
                for col in copy.columns:
                    col.foreign_keys.clear()
                for constraint in list(copy.constraints):
                    if isinstance(constraint, ForeignKeyConstraint):
                        copy.constraints.remove(constraint)
            meta.create_all(c)
        engine = base.execution_options(schema_translate_map={"app": schema})
        ledger, spec = seed(tmp_path / "project")
        repo = tmp_path / "source"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        (repo / "unchanged.txt").write_text("baseline survives", encoding="utf-8")
        test_dir = repo / "tests"
        test_dir.mkdir()
        path = spec.work_packages[0].deliverables[0].relative_path
        (test_dir / "test_delivery.py").write_text(
            f"from pathlib import Path\ndef test_delivered():\n    assert Path({path!r}).read_text() == 'released content'\n    assert Path('unchanged.txt').read_text() == 'baseline survives'\n",
            encoding="utf-8",
        )
        for args in [
            ["add", "."],
            [
                "-c",
                "user.name=Audit",
                "-c",
                "user.email=audit@example.invalid",
                "commit",
                "-m",
                "fixed baseline",
            ],
        ]:
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        state = tmp_path / "state"
        settings = Settings(_env_file=None, state_dir=state)
        store = ArtifactStore(state / "artifact_store")
        raw = spec.model_dump(mode="json", by_alias=True)
        raw["integrationPolicy"] = {
            "policyRef": "confirmed-policy",
            "executionMode": "host_authorized",
            "checks": [
                {
                    "checkId": "python_pytest",
                    "checkVersion": "v1",
                    "config": {"testPaths": ["tests"], "minimumCollectedTests": 1},
                }
            ],
            "requiredReviewRoles": [],
        }
        spec = PlanSpec.model_validate(raw)
        plan = ledger.get(m.Plan, ("run", 1))
        plan.spec_json = spec.model_dump(mode="json", by_alias=True)
        plan.plan_hash = compute_plan_hash(spec)
        baseline = ledger.get(m.ProjectBaselineRow, "baseline")
        baseline.git_commit = commit
        baseline.manifest_hash = "b" * 64
        baseline.is_git = True
        grant = ledger.get(m.DirectoryGrantRow, "grant")
        if grant is None:
            grant = next(r for r in ledger.added if isinstance(r, m.DirectoryGrantRow))
        grant.normalized_root = str(repo)
        project = ledger.get(m.Project, "project")
        project.root_path = str(repo)
        run = ledger.get(m.Run, "run")
        run.status = "running"
        run.collaboration_state = "ready_for_integration"
        stage = next(r for r in ledger.added if isinstance(r, m.StageRow))
        stage.status = "released"
        digest = store.put_bytes(b"released content")
        ledger.add(
            m.ArtifactCandidateRow(
                id="candidate",
                project_id="project",
                workspace_id="workspace-" + spec.roles[0].role_id,
                relative_path=path,
                kind="markdown",
                content_hash=digest,
                manifest_hash="c" * 64,
                contract_id=spec.work_packages[0].deliverables[0].id,
                contract_version=1,
                contract_hash=plan.contract_hash,
                environment_hash="e" * 64,
                state="frozen",
                operation_id="freeze",
                attempt_id="past_attempt",
                generation=1,
                stop_evidence_json={},
            )
        )
        ledger.add(
            m.ReleaseRecordRow(
                id="release",
                stage_id=stage.id,
                review_cycle_id="review",
                gate_decision_id="gate",
                scope_kind="stage",
                work_package_ids=[spec.work_packages[0].id],
                candidate_versions=[
                    {
                        "workPackageId": spec.work_packages[0].id,
                        "candidateId": "candidate",
                        "contentHash": digest,
                    }
                ],
                dependency_satisfied_refs=[],
                shared_interface_still_blocked=False,
            )
        )
        binding = {
            "runId": "run",
            "planVersion": 1,
            "assignmentVersion": 1,
            "gatePolicyVersion": 1,
            "executionGeneration": 1,
            "releaseRecordIds": ["release"],
            "candidateSetHashes": [],
            "contractHash": plan.contract_hash,
            "environmentFingerprint": "e" * 64,
        }
        handoff_hash = sha256_hex(binding)
        ledger.add(
            m.SchedulerOperationRow(
                id="handoff-op",
                run_id="run",
                kind="integration_handoff",
                operation_key="handoff",
                request_hash=handoff_hash,
                binding_json={**binding, "handoffId": "handoff", "handoffHash": handoff_hash},
                status="succeeded",
                result_refs=["handoff"],
            )
        )
        with unit_of_work_connection(engine) as u:
            for row in ledger.added:
                u.session.add(row)
        yield engine, settings, store, spec, handoff_hash
    finally:
        assert schema.startswith("af_hardening_") and schema[13:].isalnum()
        with base.begin() as c:
            c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        base.dispose()


def build(env):
    engine, settings, store, spec, digest = env
    with unit_of_work_connection(engine) as u:
        selection = create_selection(
            u.session,
            principal=api_principal(),
            run_id="run",
            request=IntegrationSelectionRequest(
                handoffRef="handoff", handoffHash=digest, idempotencyKey="selection"
            ),
        )
    accepted = request_integration_build(
        engine,
        principal=api_principal(),
        run_id="run",
        body=IntegrationBuildRequest(
            selectionId=selection.selection_id,
            selectionHash=selection.selection_hash,
            idempotencyKey="build",
        ),
        settings=settings,
    )
    result = process_integration_build(engine, settings, build_id=accepted.build_id)
    assert result.status == "succeeded"
    return result


def check(env):
    engine, settings, store, spec, _ = env
    result = build(env)
    cycle = start_validation_for_version(
        engine,
        settings,
        principal=api_principal(),
        version_id=result.result_version_id,
        body=ValidationCycleRequest(policyRef="confirmed-policy", idempotencyKey="checks"),
    )
    with unit_of_work_connection(engine) as u:
        check_id = u.session.scalar(
            select(m.IntegrationCheckRunRow.id).where(
                m.IntegrationCheckRunRow.cycle_id == cycle.cycle_id
            )
        )
    return result, cycle, check_id


def deliver(env):
    engine, settings, store, spec, _ = env
    result, cycle, check_id = check(env)
    report = process_check_run(engine, settings, check_run_id=check_id)
    assert report.outcome == "passed", report.structured_result
    manifest = build_and_publish_manifest(
        engine, store, version_id=result.result_version_id, cycle_id=cycle.cycle_id
    )
    assert manifest.technical_status == "passed"
    return result, cycle, manifest


def accept(env, manifest, key="accept"):
    engine, settings, store, *_ = env
    with unit_of_work_connection(engine) as u:
        run = u.session.get(m.Run, "run")
        delivery = u.session.get(m.RunDeliveryRow, "run")
        body = AcceptanceDecisionRequest(
            integrationVersion=manifest.version_id,
            manifestHash=manifest.manifest_hash,
            manifestRevision=manifest.revision,
            expectedStatusVersion=run.status_version,
            decision="accept",
        )
        revision = delivery.delivery_revision
    return decide_acceptance(
        engine,
        store,
        principal=api_principal(),
        run_id="run",
        expected_delivery_revision=revision,
        idempotency_key=key,
        body=body,
    )


def test_actual_baseline_checks_manifest_and_accept(env):
    engine, settings, store, *_ = env
    result, cycle, manifest = deliver(env)
    assert store.has(manifest.payload.members[0].object_hash)
    accepted = accept(env, manifest)
    assert accepted.run_status == "completed"


def test_cancelled_run_cannot_be_accepted(env):
    engine, *_ = env
    _, _, manifest = deliver(env)
    with unit_of_work_connection(engine) as u:
        u.session.get(m.Run, "run").status = "canceled"
    with pytest.raises(AcceptanceError):
        accept(env, manifest)
    with unit_of_work_connection(engine) as u:
        assert u.session.get(m.Run, "run").status == "canceled"


def test_unknown_check_does_not_reexecute(env):
    engine, settings, *_ = env
    _, _, check_id = check(env)
    calls = []

    def crash(request):
        calls.append(request)
        assert (Path(request.cwd) / "unchanged.txt").read_text() == "baseline survives"
        raise SystemExit("native started before observer died")

    with pytest.raises(SystemExit):
        process_check_run(engine, settings, check_run_id=check_id, executor=crash)
    with pytest.raises(CheckWorkerError):
        process_check_run(engine, settings, check_run_id=check_id, executor=crash)
    assert len(calls) == 1


def test_old_manifest_replay_does_not_rewind_current(env):
    engine, settings, store, *_ = env
    _, cycle, manifest = deliver(env)
    from agentforge.delivery.manifest import ManifestBuildIntent

    raw = store.get_bytes(manifest.manifest_hash)
    intent = ManifestBuildIntent(
        intent_id="retry",
        run_id="run",
        version_id=manifest.version_id,
        cycle_id=cycle.cycle_id,
        revision=manifest.revision,
        payload=manifest.payload,
        manifest_hash=manifest.manifest_hash,
        payload_bytes=raw,
    )
    with unit_of_work_connection(engine) as u:
        delivery = u.session.get(m.RunDeliveryRow, "run")
        delivery.current_version_id = "new-version"
        delivery.current_manifest_id = None
        delivery.delivery_revision = 50
    publish_manifest(engine, store, intent=intent)
    with unit_of_work_connection(engine) as u:
        delivery = u.session.get(m.RunDeliveryRow, "run")
        assert delivery.current_version_id == "new-version" and delivery.delivery_revision == 50


def test_actual_config_roundtrip_and_unconfirmed_policy_rejected(env):
    engine, settings, *_ = env
    result = build(env)
    with pytest.raises(CheckWorkerError):
        start_validation_for_version(
            engine,
            settings,
            principal=api_principal(),
            version_id=result.result_version_id,
            body=ValidationCycleRequest(policyRef="unconfirmed", idempotencyKey="bad"),
        )
    cycle = start_validation_for_version(
        engine,
        settings,
        principal=api_principal(),
        version_id=result.result_version_id,
        body=ValidationCycleRequest(policyRef="confirmed-policy", idempotencyKey="valid"),
    )
    with unit_of_work_connection(engine) as u:
        persisted = u.session.get(m.IntegrationValidationCycleRow, cycle.cycle_id)
        assert persisted.required_checks[0]["config"]["testPaths"] == ["tests"]


def test_resume_requires_completed_case(env):
    from agentforge.driver.control import RunControlError, RunControlService

    engine, *_ = env
    with unit_of_work_connection(engine) as u:
        u.session.get(m.Run, "run").status = "paused"
    with unit_of_work_connection(engine) as u:
        with pytest.raises(RunControlError):
            RunControlService(u.session).apply_verified_resume("run")
