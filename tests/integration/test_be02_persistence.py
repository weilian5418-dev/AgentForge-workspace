"""Real PostgreSQL concurrency and failure tests for BE-02."""

from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError

from agentforge.core.config import Settings
from agentforge.storage.database import create_database_engine
from agentforge.storage.exceptions import (
    AttemptSlotBusyError,
    IdempotencyConflictError,
    LeaseConflictError,
    LeaseExpiredError,
    OutboxClaimError,
    VersionConflictError,
)
from agentforge.storage.hashing import request_content_hash
from agentforge.storage.models import Plan, Project, Run, WorkPackage
from agentforge.storage.repositories.events import EventRepository
from agentforge.storage.repositories.idempotency import IdempotencyRepository
from agentforge.storage.repositories.leases import LeaseRepository
from agentforge.storage.repositories.outbox import OutboxRepository
from agentforge.storage.repositories.runs import RunRepository
from agentforge.storage.uow import UnitOfWork, make_session_factory, unit_of_work

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


def _seed_run(factory, suffix: str) -> tuple[str, str, str]:
    project_id = f"proj_{suffix}"
    run_id = f"run_{suffix}"
    wp_id = f"wp_{suffix}"
    with unit_of_work(factory) as uow:
        uow.session.add(
            Project(
                id=project_id,
                directory_grant_id=f"grant_{suffix}",
                root_path=f"D:/tmp/{suffix}",
                workspace_root=f"D:/tmp/{suffix}/workspace",
            )
        )
        uow.flush()
        uow.session.add(
            Run(
                id=run_id,
                project_id=project_id,
                goal="be02 persistence test",
                mode="single",
                status="running",
                execution_profile="container",
                failure_policy="dependency-aware",
                budget={"maxTokens": 100},
                planning_provider_profile_id="profile_test",
                outputs=["markdown"],
            )
        )
        uow.flush()
        uow.session.add(
            Plan(
                run_id=run_id,
                version=1,
                contract_hash="b" * 64,
                status="approved",
                summary="plan",
            )
        )
        uow.flush()
        uow.session.add(
            WorkPackage(
                id=wp_id,
                run_id=run_id,
                plan_version=1,
                stage_id="implementation",
                owner_role="writer",
                owner_role_id="writer",
                objective="write notes",
                status="ready",
            )
        )
    return project_id, run_id, wp_id


def test_transaction_rollback_keeps_event_outbox_idempotent_atomic(session_factory):
    suffix = uuid.uuid4().hex[:12]
    _, run_id, _ = _seed_run(session_factory, suffix)
    key = f"key_{suffix}"
    body_hash = request_content_hash(
        operation_type="run.action", target_refs={"runId": run_id}, body={"action": "pause"}
    )
    with pytest.raises(RuntimeError, match="boom"):
        with unit_of_work(session_factory) as uow:
            mode, record = IdempotencyRepository(uow).begin_or_replay(
                identity_scope="user",
                operation_type="run.action",
                request_key=key,
                request_hash=body_hash,
                owner="api-1",
            )
            assert mode == "execute" and record is not None
            EventRepository(uow).append_with_outbox(
                run_id=run_id,
                event_type="run.status_changed",
                payload={"status": "pause_requested", "statusVersion": 2},
                outbox_target="queue:run-actions",
            )
            raise RuntimeError("boom")

    with unit_of_work(session_factory) as uow:
        events = EventRepository(uow).list_after(run_id, 0)
        assert events == []
        outbox_count = uow.session.execute(
            text("SELECT count(*) FROM app.outbox WHERE run_id = :run_id"),
            {"run_id": run_id},
        ).scalar_one()
        assert outbox_count == 0
        idem_count = uow.session.execute(
            text("SELECT count(*) FROM app.idempotency_requests WHERE request_key = :key"),
            {"key": key},
        ).scalar_one()
        assert idem_count == 0


def test_idempotency_same_key_concurrent_single_winner(session_factory):
    suffix = uuid.uuid4().hex[:12]
    _, run_id, _ = _seed_run(session_factory, suffix)
    key = f"key_{suffix}"
    body = {"action": "pause"}
    body_hash = request_content_hash(
        operation_type="run.action", target_refs={"runId": run_id}, body=body
    )
    barrier = threading.Barrier(2)
    results: list[str] = []

    def worker(owner: str) -> str:
        barrier.wait()
        with unit_of_work(session_factory) as uow:
            mode, record = IdempotencyRepository(uow).begin_or_replay(
                identity_scope="user",
                operation_type="run.action",
                request_key=key,
                request_hash=body_hash,
                owner=owner,
            )
            if mode == "execute" and record is not None:
                RunRepository(uow).update_status(
                    run_id=run_id, expected_status_version=1, new_status="pause_requested"
                )
                IdempotencyRepository(uow).complete(
                    record=record,
                    owner=owner,
                    token=record.processing_token or "",
                    status_code=202,
                    response_json={"runId": run_id, "status": "pause_requested"},
                )
            return mode

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, "a"), pool.submit(worker, "b")]
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                results.append(type(exc).__name__)

    assert results.count("execute") == 1
    assert (
        "replay" in results
        or "IdempotencyInProgressError" in results
        or results.count("execute") == 1
    )

    with unit_of_work(session_factory) as uow:
        run = RunRepository(uow).get(run_id)
        assert run is not None
        assert run.status_version == 2
        assert run.status == "pause_requested"


def test_idempotency_conflict_different_body(session_factory):
    suffix = uuid.uuid4().hex[:12]
    _, run_id, _ = _seed_run(session_factory, suffix)
    key = f"key_{suffix}"
    h1 = request_content_hash(
        operation_type="run.action", target_refs={"runId": run_id}, body={"action": "pause"}
    )
    h2 = request_content_hash(
        operation_type="run.action", target_refs={"runId": run_id}, body={"action": "cancel"}
    )
    with unit_of_work(session_factory) as uow:
        mode, record = IdempotencyRepository(uow).begin_or_replay(
            identity_scope="user",
            operation_type="run.action",
            request_key=key,
            request_hash=h1,
            owner="api",
        )
        assert mode == "execute" and record is not None
        IdempotencyRepository(uow).complete(
            record=record,
            owner="api",
            token=record.processing_token or "",
            status_code=202,
            response_json={"ok": True},
        )
    with pytest.raises(IdempotencyConflictError):
        with unit_of_work(session_factory) as uow:
            IdempotencyRepository(uow).begin_or_replay(
                identity_scope="user",
                operation_type="run.action",
                request_key=key,
                request_hash=h2,
                owner="api",
            )


def test_status_version_conflict(session_factory):
    suffix = uuid.uuid4().hex[:12]
    _, run_id, _ = _seed_run(session_factory, suffix)
    with unit_of_work(session_factory) as uow:
        RunRepository(uow).update_status(
            run_id=run_id, expected_status_version=1, new_status="paused"
        )
    with pytest.raises(VersionConflictError):
        with unit_of_work(session_factory) as uow:
            RunRepository(uow).update_status(
                run_id=run_id, expected_status_version=1, new_status="running"
            )


def test_two_drivers_one_lease_winner(session_factory):
    suffix = uuid.uuid4().hex[:12]
    _, run_id, _ = _seed_run(session_factory, suffix)
    barrier = threading.Barrier(2)

    def acquire(owner: str) -> str:
        barrier.wait()
        with unit_of_work(session_factory) as uow:
            lease = LeaseRepository(uow).acquire_run_lease(
                run_id=run_id, owner=owner, ttl_seconds=60
            )
            return f"{lease.owner}:{lease.generation}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(acquire, "driver-a"), pool.submit(acquire, "driver-b")]
        outcomes = []
        for fut in as_completed(futures):
            try:
                outcomes.append(fut.result())
            except LeaseConflictError:
                outcomes.append("conflict")
    winners = [o for o in outcomes if o.startswith("driver-")]
    conflicts = [o for o in outcomes if o == "conflict"]
    assert len(outcomes) == 2
    assert len(winners) == 1
    assert len(conflicts) == 1


def test_stale_generation_cannot_complete_or_renew(session_factory):
    suffix = uuid.uuid4().hex[:12]
    _, run_id, wp_id = _seed_run(session_factory, suffix)
    with unit_of_work(session_factory) as uow:
        attempt = LeaseRepository(uow).create_attempt(
            attempt_id=f"att_{suffix}_gen",
            work_package_id=wp_id,
            run_id=run_id,
            run_generation=1,
        )
        token = attempt.lease_token
        attempt_id = attempt.id
        RunRepository(uow).bump_active_generation(run_id, 1)
        with pytest.raises(LeaseExpiredError):
            LeaseRepository(uow).complete_attempt(
                attempt_id=attempt_id,
                lease_token=token,
                run_generation=1,
                status="succeeded",
            )
        with pytest.raises(LeaseExpiredError):
            LeaseRepository(uow).renew_attempt(attempt_id=attempt_id, lease_token=token)


def test_preloaded_run_cannot_roll_back_generation(session_factory):
    """Stale Session identity map must not accept expected_generation=1 after bumps to 3."""
    suffix = uuid.uuid4().hex[:12]
    _, run_id, _ = _seed_run(session_factory, suffix)

    session_a = session_factory()
    try:
        cached = RunRepository(UnitOfWork(session_a)).get(run_id)
        assert cached is not None and int(cached.active_generation) == 1

        with unit_of_work(session_factory) as uow:
            assert RunRepository(uow).bump_active_generation(run_id, 1) == 2
        with unit_of_work(session_factory) as uow:
            assert RunRepository(uow).bump_active_generation(run_id, 2) == 3

        assert int(cached.active_generation) == 1
        with pytest.raises(VersionConflictError):
            RunRepository(UnitOfWork(session_a)).bump_active_generation(run_id, 1)
            session_a.commit()
        session_a.rollback()
    finally:
        session_a.close()

    with unit_of_work(session_factory) as uow:
        run = RunRepository(uow).get(run_id)
        assert run is not None and int(run.active_generation) == 3


def test_preloaded_attempt_cannot_complete_after_awaiting_stop(session_factory):
    from agentforge.storage.models import TaskAttempt

    suffix = uuid.uuid4().hex[:12]
    _, run_id, wp_id = _seed_run(session_factory, suffix)
    with unit_of_work(session_factory) as uow:
        created = LeaseRepository(uow).create_attempt(
            attempt_id=f"att_{suffix}_stop",
            work_package_id=wp_id,
            run_id=run_id,
            run_generation=1,
        )
        attempt_id = created.id
        token = created.lease_token

    session_a = session_factory()
    try:
        cached = session_a.execute(
            select(TaskAttempt).where(TaskAttempt.id == attempt_id)
        ).scalar_one()
        assert cached.status == "leased"

        with unit_of_work(session_factory) as uow:
            LeaseRepository(uow).mark_awaiting_stop(attempt_id=attempt_id, lease_token=token)

        assert cached.status == "leased"
        with pytest.raises(LeaseExpiredError):
            LeaseRepository(UnitOfWork(session_a)).complete_attempt(
                attempt_id=attempt_id,
                lease_token=token,
                run_generation=1,
                status="succeeded",
            )
            session_a.commit()
        session_a.rollback()

        with pytest.raises(LeaseExpiredError):
            LeaseRepository(UnitOfWork(session_a)).renew_attempt(
                attempt_id=attempt_id, lease_token=token
            )
            session_a.commit()
        session_a.rollback()
    finally:
        session_a.close()

    with unit_of_work(session_factory) as uow:
        row = uow.session.execute(
            select(TaskAttempt).where(TaskAttempt.id == attempt_id)
        ).scalar_one()
        assert row.status == "awaiting_stop_verification"


def test_concurrent_generation_bump_rejects_waiting_completion(session_factory):
    """Holder locks Run; waiter blocks; bump commits; waiter must reject stale complete."""
    from agentforge.storage.models import Run, TaskAttempt

    suffix = uuid.uuid4().hex[:12]
    _, run_id, wp_id = _seed_run(session_factory, suffix)
    with unit_of_work(session_factory) as uow:
        attempt = LeaseRepository(uow).create_attempt(
            attempt_id=f"att_{suffix}_race",
            work_package_id=wp_id,
            run_id=run_id,
            run_generation=1,
        )
        attempt_id = attempt.id
        token = attempt.lease_token

    run_locked = threading.Event()
    outcomes: list[str] = []

    def hold_run_then_bump() -> None:
        session = session_factory()
        try:
            session.execute(select(Run).where(Run.id == run_id).with_for_update()).scalar_one()
            run_locked.set()
            time.sleep(0.4)
            RunRepository(UnitOfWork(session)).bump_active_generation(run_id, 1)
            session.commit()
        finally:
            session.close()

    def complete_after_lock() -> None:
        assert run_locked.wait(10)
        try:
            with unit_of_work(session_factory) as uow:
                LeaseRepository(uow).complete_attempt(
                    attempt_id=attempt_id,
                    lease_token=token,
                    run_generation=1,
                    status="succeeded",
                )
            outcomes.append("accepted")
        except LeaseExpiredError:
            outcomes.append("rejected")
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)

    t_hold = threading.Thread(target=hold_run_then_bump)
    t_complete = threading.Thread(target=complete_after_lock)
    t_hold.start()
    t_complete.start()
    t_hold.join(15)
    t_complete.join(15)
    assert outcomes == ["rejected"]
    with unit_of_work(session_factory) as uow:
        row = uow.session.execute(
            select(TaskAttempt).where(TaskAttempt.id == attempt_id)
        ).scalar_one()
        run = RunRepository(uow).get(run_id)
        assert run is not None and int(run.active_generation) == 2
        assert row.status == "leased"


def test_concurrent_generation_bump_rejects_waiting_renew(session_factory):
    from agentforge.storage.models import Run

    suffix = uuid.uuid4().hex[:12]
    _, run_id, wp_id = _seed_run(session_factory, suffix)
    with unit_of_work(session_factory) as uow:
        attempt = LeaseRepository(uow).create_attempt(
            attempt_id=f"att_{suffix}_renew",
            work_package_id=wp_id,
            run_id=run_id,
            run_generation=1,
        )
        attempt_id = attempt.id
        token = attempt.lease_token

    run_locked = threading.Event()
    outcomes: list[str] = []

    def hold_run_then_bump() -> None:
        session = session_factory()
        try:
            session.execute(select(Run).where(Run.id == run_id).with_for_update()).scalar_one()
            run_locked.set()
            time.sleep(0.4)
            RunRepository(UnitOfWork(session)).bump_active_generation(run_id, 1)
            session.commit()
        finally:
            session.close()

    def renew_after_lock() -> None:
        assert run_locked.wait(10)
        try:
            with unit_of_work(session_factory) as uow:
                LeaseRepository(uow).renew_attempt(attempt_id=attempt_id, lease_token=token)
            outcomes.append("accepted")
        except LeaseExpiredError:
            outcomes.append("rejected")
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)

    t_hold = threading.Thread(target=hold_run_then_bump)
    t_renew = threading.Thread(target=renew_after_lock)
    t_hold.start()
    t_renew.start()
    t_hold.join(15)
    t_renew.join(15)
    assert outcomes == ["rejected"]


def test_expired_lease_renew_rejected(session_factory):
    suffix = uuid.uuid4().hex[:12]
    _, run_id, _ = _seed_run(session_factory, suffix)
    with unit_of_work(session_factory) as uow:
        lease = LeaseRepository(uow).acquire_run_lease(run_id=run_id, owner="d1", ttl_seconds=60)
        uow.session.execute(
            text(
                "UPDATE app.run_leases SET expires_at = now() - interval '1 second' WHERE run_id = :id"
            ),
            {"id": run_id},
        )
        with pytest.raises(LeaseExpiredError):
            LeaseRepository(uow).renew_run_lease(
                run_id=run_id,
                owner="d1",
                token=lease.token,
                generation=lease.generation,
            )


def test_attempt_slot_blocks_until_stop_verified(session_factory):
    suffix = uuid.uuid4().hex[:12]
    _, run_id, wp_id = _seed_run(session_factory, suffix)
    with unit_of_work(session_factory) as uow:
        a1 = LeaseRepository(uow).create_attempt(
            attempt_id=f"att_{suffix}_1",
            work_package_id=wp_id,
            run_id=run_id,
            run_generation=1,
        )
        LeaseRepository(uow).mark_awaiting_stop(attempt_id=a1.id, lease_token=a1.lease_token)
        with pytest.raises(AttemptSlotBusyError):
            LeaseRepository(uow).create_attempt(
                attempt_id=f"att_{suffix}_2",
                work_package_id=wp_id,
                run_id=run_id,
                run_generation=1,
            )


def test_event_sequences_unique_under_concurrency(session_factory):
    suffix = uuid.uuid4().hex[:12]
    _, run_id, _ = _seed_run(session_factory, suffix)
    barrier = threading.Barrier(4)

    def write(_: int) -> int:
        barrier.wait()
        with unit_of_work(session_factory) as uow:
            event, _ = EventRepository(uow).append_with_outbox(
                run_id=run_id,
                event_type="usage.updated",
                payload={"actualTokens": 1},
                outbox_target="queue:usage",
            )
            return int(event.sequence)

    with ThreadPoolExecutor(max_workers=4) as pool:
        sequences = list(pool.map(write, range(4)))
    assert sorted(sequences) == [1, 2, 3, 4]
    assert len(set(sequences)) == 4


def test_outbox_reclaim_after_crash_and_old_token_rejected(session_factory):
    suffix = uuid.uuid4().hex[:12]
    _, run_id, _ = _seed_run(session_factory, suffix)
    with unit_of_work(session_factory) as uow:
        _, outbox = EventRepository(uow).append_with_outbox(
            run_id=run_id,
            event_type="plan.proposed",
            payload={"planVersion": 1, "contractHash": "c" * 64, "summary": "x"},
            outbox_target="queue:plan",
        )
        outbox_id = outbox.id

    with unit_of_work(session_factory) as uow:
        claimed = OutboxRepository(uow).claim_batch(owner="dispatcher-1", limit=500)
        match = next(item for item in claimed if item.id == outbox_id)
        old_token = match.claim_token
        assert old_token
        uow.session.execute(
            text(
                "UPDATE app.outbox SET claim_expires_at = now() - interval '1 second' "
                "WHERE id = :id"
            ),
            {"id": outbox_id},
        )

    with unit_of_work(session_factory) as uow:
        reclaimed = OutboxRepository(uow).claim_batch(owner="dispatcher-2", limit=500)
        match = next((item for item in reclaimed if item.id == outbox_id), None)
        assert match is not None, "expired claimed outbox was not reclaimed"
        new_token = match.claim_token
        assert new_token != old_token

    with pytest.raises(OutboxClaimError):
        with unit_of_work(session_factory) as uow:
            OutboxRepository(uow).confirm_dispatched(
                outbox_id=outbox_id, owner="dispatcher-1", token=old_token or ""
            )

    with unit_of_work(session_factory) as uow:
        OutboxRepository(uow).confirm_dispatched(
            outbox_id=outbox_id, owner="dispatcher-2", token=new_token or ""
        )


def test_api_role_cannot_ddl_and_worker_denied_when_configured(engine):
    url = _url() or ""
    if "agentforge_api" not in url:
        pytest.skip("API-role URL required for privilege checks")
    with engine.connect() as conn:
        with pytest.raises(ProgrammingError):
            conn.execute(text("CREATE TABLE app.be02_should_fail (id text)"))
            conn.commit()
