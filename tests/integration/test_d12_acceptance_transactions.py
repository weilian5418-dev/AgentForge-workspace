"""D12.14: acceptance transaction negatives on real PostgreSQL when available."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.delivery import AcceptanceDecisionRequest, DeliveryManifestPayload
from agentforge.core.config import Settings
from agentforge.delivery.acceptance import AcceptanceError, decide_acceptance
from agentforge.delivery.manifest import canonical_manifest_bytes, compute_manifest_hash
from agentforge.delivery.repository import get_or_create_delivery, protect_content_object
from agentforge.queries.authorization import api_principal
from agentforge.storage.database import create_database_engine
from agentforge.storage.models import DeliveryManifestRow, Project, Run
from agentforge.storage.uow import make_session_factory, unit_of_work

pytestmark = pytest.mark.integration

H = "a" * 64


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


def _seed(factory, store: ArtifactStore, *, technical: str = "passed") -> dict[str, object]:
    suffix = uuid.uuid4().hex[:10]
    run_id = f"run_d12a_{suffix}"
    version_id = f"iv_{suffix}"
    cycle_id = f"ivc_{suffix}"
    payload = DeliveryManifestPayload.model_validate(
        {
            "schemaVersion": "1",
            "runId": run_id,
            "integrationVersion": version_id,
            "validationCycleId": cycle_id,
            "manifestRevision": 1,
            "planVersion": 1,
            "assignmentVersion": 1,
            "gatePolicyVersion": 1,
            "selectionHash": H,
            "baselineHash": H,
            "treeManifestHash": H,
            "contractHash": H,
            "integrationPolicyHash": H,
            "environmentFingerprint": H,
            "members": [
                {
                    "memberId": f"tree:{version_id}",
                    "kind": "code_tree",
                    "required": True,
                    "safeRelativePath": ".",
                    "objectHash": H,
                    "applicableCheckIds": ["python_pytest"],
                    "memberTechnicalStatus": technical,
                },
                {
                    "memberId": "docs",
                    "kind": "markdown",
                    "required": False,
                    "safeRelativePath": "README.md",
                    "objectHash": H,
                    "memberTechnicalStatus": "passed",
                },
            ],
            "checks": [
                {
                    "checkId": "python_pytest",
                    "checkVersion": "v1",
                    "configHash": H,
                    "outcome": "passed" if technical == "passed" else "failed",
                }
            ],
            "reviews": [],
            "knownRisks": [],
            "technicalStatus": technical,
        }
    )
    raw = canonical_manifest_bytes(payload)
    digest = compute_manifest_hash(payload)
    object_hash = store.put_bytes(raw)
    assert object_hash == digest

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
                goal="d12 acceptance",
                mode="single",
                status="awaiting_acceptance",
                execution_profile="host_authorized",
                failure_policy="dependency-aware",
                budget={"maxTokens": 100},
                status_version=1,
                plan_version=1,
                assignment_version=1,
                gate_policy_version=1,
                active_generation=1,
            )
        )
        uow.flush()
        delivery = get_or_create_delivery(uow.session, run_id)
        delivery.current_version_id = version_id
        delivery.technical_status = technical
        delivery.acceptance_status = "awaiting_acceptance"
        delivery.delivery_revision = 3
        protect_content_object(
            uow.session, content_hash=object_hash, size_bytes=len(raw)
        )
        row = DeliveryManifestRow(
            id=f"dm_{suffix}",
            run_id=run_id,
            version_id=version_id,
            cycle_id=cycle_id,
            revision=1,
            manifest_hash=digest,
            payload_ref=object_hash,
            technical_status=technical,
            created_at=datetime.now(UTC),
        )
        uow.session.add(row)
        uow.flush()
        delivery.current_manifest_id = row.id
        uow.flush()

    return {
        "run_id": run_id,
        "version_id": version_id,
        "manifest_hash": digest,
        "delivery_revision": 3,
        "status_version": 1,
    }


def test_stale_manifest_accept_conflicts(engine, session_factory, tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifact_store")
    seed = _seed(session_factory, store, technical="passed")
    body = AcceptanceDecisionRequest.model_validate(
        {
            "integrationVersion": seed["version_id"],
            "manifestHash": "b" * 64,  # wrong / stale
            "manifestRevision": 1,
            "expectedStatusVersion": seed["status_version"],
            "decision": "accept",
        }
    )
    with pytest.raises(AcceptanceError) as ei:
        decide_acceptance(
            engine,
            store,
            principal=api_principal(),
            run_id=str(seed["run_id"]),
            expected_delivery_revision=int(seed["delivery_revision"]),
            idempotency_key=f"k_{uuid.uuid4().hex}",
            body=body,
        )
    assert ei.value.code in {"manifest_stale", "content_integrity_failed"}


def test_full_accept_does_not_enqueue_push_or_deploy(
    engine, session_factory, tmp_path: Path
) -> None:
    from sqlalchemy import select

    from agentforge.storage.models import Outbox

    store = ArtifactStore(tmp_path / "artifact_store")
    seed = _seed(session_factory, store, technical="passed")
    body = AcceptanceDecisionRequest.model_validate(
        {
            "integrationVersion": seed["version_id"],
            "manifestHash": seed["manifest_hash"],
            "manifestRevision": 1,
            "expectedStatusVersion": seed["status_version"],
            "decision": "accept",
        }
    )
    result = decide_acceptance(
        engine,
        store,
        principal=api_principal(),
        run_id=str(seed["run_id"]),
        expected_delivery_revision=int(seed["delivery_revision"]),
        idempotency_key=f"k_{uuid.uuid4().hex}",
        body=body,
    )
    assert result.acceptance_status == "accepted"
    assert result.technical_status == "passed"
    assert result.run_status == "completed"
    with unit_of_work(session_factory) as uow:
        intents = list(
            uow.session.scalars(
                select(Outbox).where(Outbox.run_id == str(seed["run_id"]))
            ).all()
        )
    forbidden = {"git.push", "deploy", "publish_site", "push", "deploy_site"}
    assert not any(o.intent_type in forbidden for o in intents)


def test_idempotent_replay_same_key(engine, session_factory, tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifact_store")
    seed = _seed(session_factory, store, technical="passed")
    key = f"idem_{uuid.uuid4().hex}"
    body = AcceptanceDecisionRequest.model_validate(
        {
            "integrationVersion": seed["version_id"],
            "manifestHash": seed["manifest_hash"],
            "manifestRevision": 1,
            "expectedStatusVersion": seed["status_version"],
            "decision": "reject",
            "rejectedMemberIds": [f"tree:{seed['version_id']}", "docs"],
        }
    )
    first = decide_acceptance(
        engine,
        store,
        principal=api_principal(),
        run_id=str(seed["run_id"]),
        expected_delivery_revision=int(seed["delivery_revision"]),
        idempotency_key=key,
        body=body,
    )
    second = decide_acceptance(
        engine,
        store,
        principal=api_principal(),
        run_id=str(seed["run_id"]),
        expected_delivery_revision=int(seed["delivery_revision"]),
        idempotency_key=key,
        body=body,
    )
    assert first.decision_id == second.decision_id
    # Same key different content conflicts
    other = AcceptanceDecisionRequest.model_validate(
        {
            "integrationVersion": seed["version_id"],
            "manifestHash": seed["manifest_hash"],
            "manifestRevision": 1,
            "expectedStatusVersion": seed["status_version"],
            "decision": "accept",
        }
    )
    with pytest.raises(AcceptanceError) as ei:
        decide_acceptance(
            engine,
            store,
            principal=api_principal(),
            run_id=str(seed["run_id"]),
            expected_delivery_revision=int(seed["delivery_revision"]),
            idempotency_key=key,
            body=other,
        )
    assert ei.value.code == "idempotency_conflict"
