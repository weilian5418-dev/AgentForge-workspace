"""Real PostgreSQL concurrency, expiry and committed dispatch regression checks."""

from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import ForeignKeyConstraint, MetaData, create_engine, text
from sqlalchemy.orm import Session

from agentforge.contracts.runner import RunnerExecuteRequest, RunnerTicketIssueRequest
from agentforge.permissions.service import process_pending_forward, try_consume_once
from agentforge.runner.executor import execute_runner
from agentforge.runner.service import RunnerError, RunnerService
from agentforge.storage.base import Base
from agentforge.storage.models import ApprovalMappingRow, RunnerOperationRow

pytestmark = pytest.mark.integration


@pytest.fixture
def isolated_engine():
    url = os.environ.get("AGENTFORGE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set AGENTFORGE_TEST_DATABASE_URL to an isolated PostgreSQL test role")
    base = create_engine(url, pool_size=5, max_overflow=0)
    schema = "af_review_" + uuid.uuid4().hex
    tables = [
        "approval_mappings",
        "outbox",
        "runner_operations",
        "git_workspaces",
        "agent_instances",
        "task_attempts",
        "runs",
        "instance_attempts",
        "stages",
        "platform_admission",
    ]
    try:
        with base.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            metadata = MetaData()
            for name in tables:
                table = Base.metadata.tables[f"app.{name}"].to_metadata(metadata, schema=schema)
                # Test only the selected ledger structures, independent of app migration level.
                # This does not alter production tables or certify cross-domain foreign keys.
                table.foreign_keys.clear()
                for column in table.columns:
                    column.foreign_keys.clear()
                for constraint in list(table.constraints):
                    if isinstance(constraint, ForeignKeyConstraint):
                        table.constraints.remove(constraint)
            metadata.create_all(conn)
        engine = base.execution_options(schema_translate_map={"app": schema})
        yield engine
    finally:
        # Only the validated random schema owned by this fixture can be removed.
        assert schema.startswith("af_review_") and len(schema) == 42 and schema[10:].isalnum()
        with base.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        base.dispose()


def seed_grant(engine, *, expired=False):
    with Session(engine) as session, session.begin():
        row = ApprovalMappingRow(
            id="grant",
            action="read",
            action_digest="a" * 64,
            native_request_id="native",
            status="confirmed",
            user_decision="approve",
            native_confirmed=True,
            mapping_json={"semantic": "once"},
            expires_at=datetime.now(UTC) + timedelta(minutes=-1 if expired else 5),
        )
        session.add(row)


def test_once_authorization_consumed_by_only_one_connection(isolated_engine):
    seed_grant(isolated_engine)
    barrier = threading.Barrier(2)

    def consume(operation):
        with Session(isolated_engine) as session, session.begin():
            row = session.get(ApprovalMappingRow, "grant")
            barrier.wait(timeout=10)
            return try_consume_once(session, row, operation_id=operation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(consume, operation) for operation in ["first", "second"]]
        results = [f.result(timeout=15) for f in futures]
    assert sorted(results) == [False, True]
    with Session(isolated_engine) as session:
        row = session.get(ApprovalMappingRow, "grant")
        winner = row.mapping_json["consumedByOperationId"]
        assert winner in {"first", "second"}
        assert row.status == "consumed"


def test_expiry_rechecked_by_sql_with_stale_orm(isolated_engine):
    seed_grant(isolated_engine)
    # Stale ORM state must not override the database's expired authorization.
    with Session(isolated_engine) as stale:
        row = stale.get(ApprovalMappingRow, "grant")
        with Session(isolated_engine) as updater, updater.begin():
            fresh = updater.get(ApprovalMappingRow, "grant")
            fresh.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert not try_consume_once(stale, row, operation_id="late")
        stale.commit()
        assert stale.get(ApprovalMappingRow, "grant").status == "confirmed"


def test_runner_committed_claim_blocks_replay_after_process_death(
    isolated_engine, monkeypatch, tmp_path
):
    body = RunnerTicketIssueRequest.model_validate(
        {
            "profileId": "render",
            "workspaceId": "w",
            "workspaceRoot": str(tmp_path),
            "scope": "file:write:result.txt",
            "params": {"path": "result.txt", "content": "result"},
            "expiresAt": datetime.now(UTC) + timedelta(minutes=5),
        }
    )
    with Session(isolated_engine) as session, session.begin():
        ticket = RunnerService(session).issue(body)
    request = RunnerExecuteRequest(
        operationId=ticket.operation_id, actionDigest=ticket.action_digest
    )

    def crash(dispatch):
        with Session(isolated_engine) as observer:
            assert observer.get(RunnerOperationRow, dispatch.operation_id).status == "running"
        (tmp_path / "result.txt").write_text("effect-before-crash")
        raise SystemExit("simulated process death")

    monkeypatch.setattr("agentforge.runner.executor.perform_runner_dispatch", crash)
    with pytest.raises(SystemExit):
        execute_runner(isolated_engine, request)
    with pytest.raises(RunnerError, match="Prior execution may have started"):
        execute_runner(isolated_engine, request)
    assert (tmp_path / "result.txt").read_text() == "effect-before-crash"


def test_native_forward_claim_committed_and_redelivery_does_not_send(isolated_engine, monkeypatch):
    with Session(isolated_engine) as session, session.begin():
        session.add(
            ApprovalMappingRow(
                id="approval",
                action="read",
                action_digest="a" * 64,
                native_request_id="native",
                status="forwarding",
                user_decision="approve",
                native_confirmed=False,
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                mapping_json={
                    "targetSummary": "file",
                    "parameterSummary": "read",
                    "sessionRef": "s",
                    "adapterId": "fake",
                    "semantic": "once",
                    "forwardPending": True,
                    "bridgeNativeDecision": "allow",
                    "bridgeMappedOptions": {"once": True},
                },
            )
        )
    adapter = Mock()

    def crash(**kwargs):
        with Session(isolated_engine) as observer:
            assert observer.get(ApprovalMappingRow, "approval").status == "delivery_unknown"
        raise SystemExit("native accepted, process died")

    adapter.respond_approval.side_effect = crash
    monkeypatch.setattr("agentforge.permissions.service.get_adapter", lambda _: adapter)
    with pytest.raises(SystemExit):
        process_pending_forward(isolated_engine, "approval")
    assert process_pending_forward(isolated_engine, "approval").status == "delivery_unknown"
    assert adapter.respond_approval.call_count == 1


def test_pending_approval_claim_is_exclusive_across_connections(isolated_engine, monkeypatch):
    with Session(isolated_engine) as session, session.begin():
        session.add(
            ApprovalMappingRow(
                id="approval",
                action="read",
                action_digest="a" * 64,
                native_request_id="native",
                status="forwarding",
                user_decision="approve",
                native_confirmed=False,
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                mapping_json={
                    "targetSummary": "file",
                    "parameterSummary": "read",
                    "sessionRef": "s",
                    "adapterId": "fake",
                    "semantic": "once",
                    "forwardPending": True,
                    "bridgeNativeDecision": "allow",
                },
            )
        )
    started = threading.Event()
    release = threading.Event()
    adapter = Mock()

    def native(**kwargs):
        started.set()
        assert release.wait(timeout=10)
        return {"status": "acked", "confirmationRef": "receipt"}

    adapter.respond_approval.side_effect = native
    monkeypatch.setattr("agentforge.permissions.service.get_adapter", lambda _: adapter)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(process_pending_forward, isolated_engine, "approval")
        assert started.wait(timeout=10)
        try:
            assert process_pending_forward(isolated_engine, "approval").status == "delivery_unknown"
        finally:
            release.set()
        assert first.result(timeout=10).status == "confirmed"
    assert adapter.respond_approval.call_count == 1


def test_runner_authorization_and_claim_commit_before_side_effect(
    isolated_engine, monkeypatch, tmp_path
):
    from agentforge.permissions.action import action_descriptor_digest, descriptor_from_runner

    descriptor = descriptor_from_runner(
        profile_id="render",
        workspace_id="workspace",
        workspace_root=str(tmp_path),
        scope="file:write:result.txt",
        params={"path": "result.txt", "content": "result"},
        attempt_id=None,
        generation=1,
        session_ref="native-session",
    )
    with Session(isolated_engine) as session, session.begin():
        session.add(
            ApprovalMappingRow(
                id="grant",
                action="write",
                action_digest=action_descriptor_digest(descriptor),
                native_request_id="n",
                status="confirmed",
                user_decision="approve",
                native_confirmed=True,
                mapping_json={
                    "semantic": "once",
                    "sessionRef": "native-session",
                    "platformScope": "file:write:result.txt",
                },
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
    with Session(isolated_engine) as session, session.begin():
        ticket = RunnerService(session).issue(
            RunnerTicketIssueRequest.model_validate(
                {
                    "profileId": "render",
                    "workspaceId": "workspace",
                    "workspaceRoot": str(tmp_path),
                    "scope": "file:write:result.txt",
                    "params": {"path": "result.txt", "content": "result"},
                    "sessionRef": "native-session",
                    "approvalId": "grant",
                    "requireEffectiveApproval": True,
                    "expiresAt": datetime.now(UTC) + timedelta(minutes=5),
                }
            )
        )

    def crash(dispatch):
        with Session(isolated_engine) as observer:
            grant = observer.get(ApprovalMappingRow, "grant")
            assert grant.status == "consumed"
            assert grant.mapping_json["consumedByOperationId"] == ticket.operation_id
            assert observer.get(RunnerOperationRow, ticket.operation_id).status == "running"
        (tmp_path / "result.txt").write_text("effect")
        raise SystemExit("lost result")

    monkeypatch.setattr("agentforge.runner.executor.perform_runner_dispatch", crash)
    req = RunnerExecuteRequest(operationId=ticket.operation_id, actionDigest=ticket.action_digest)
    with pytest.raises(SystemExit):
        execute_runner(isolated_engine, req)
    with Session(isolated_engine) as observer:
        assert observer.get(ApprovalMappingRow, "grant").status == "consumed"
    with pytest.raises(RunnerError):
        execute_runner(isolated_engine, req)


def test_revoked_db_record_cannot_be_consumed_via_cached_grant(isolated_engine):
    seed_grant(isolated_engine)
    with Session(isolated_engine) as stale:
        row = stale.get(ApprovalMappingRow, "grant")
        with Session(isolated_engine) as updater, updater.begin():
            grant = updater.get(ApprovalMappingRow, "grant")
            grant.mapping_json = {**grant.mapping_json, "revokedAt": datetime.now(UTC).isoformat()}
        assert not try_consume_once(stale, row, operation_id="late")
        stale.commit()


def test_once_grant_expiring_while_waiting_for_row_lock_is_rejected(isolated_engine):
    seed_grant(isolated_engine)
    loaded = threading.Event()
    with Session(isolated_engine) as locker, locker.begin():
        grant = locker.get(ApprovalMappingRow, "grant", with_for_update=True)
        grant.expires_at = datetime.now(UTC) + timedelta(seconds=0.3)
        locker.flush()

        def consume():
            with Session(isolated_engine) as waiter, waiter.begin():
                stale = waiter.get(ApprovalMappingRow, "grant")
                loaded.set()
                return try_consume_once(waiter, stale, operation_id="after-wait")

        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(consume)
        assert loaded.wait(timeout=5)
        time.sleep(0.5)
    try:
        assert future.result(timeout=10) is False
    finally:
        pool.shutdown(wait=True)
