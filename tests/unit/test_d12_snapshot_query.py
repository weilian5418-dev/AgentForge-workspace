"""D12.3: snapshot token binding, page cursors, bounded DTO construction."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agentforge.core.config import Settings
from agentforge.queries.authorization import AuthenticatedPrincipal, QueryAuthError, authorize_run_read
from agentforge.queries.snapshots import (
    SnapshotError,
    build_bounded_snapshot,
    make_page_cursor,
    make_snapshot_token,
    parse_page_cursor,
    validate_snapshot_binding,
)


def _settings() -> Settings:
    return Settings(api_token="test-token-" + "x" * 48, internal_signing_key="k" * 48)


def test_snapshot_token_roundtrip_and_expiry() -> None:
    settings = _settings()
    token = make_snapshot_token(
        settings,
        run_id="run_1",
        latest_sequence=7,
        status_version=2,
        delivery_revision=1,
        ttl_seconds=60,
    )
    claims = validate_snapshot_binding(settings, token, run_id="run_1")
    assert claims["latestSequence"] == 7
    assert claims["statusVersion"] == 2

    with pytest.raises(SnapshotError) as exc:
        validate_snapshot_binding(settings, token, run_id="run_other")
    assert exc.value.code == "snapshot_stale"

    bad = token[:-4] + "dead"
    with pytest.raises(SnapshotError):
        validate_snapshot_binding(settings, bad, run_id="run_1")


def test_page_cursor_rejects_cross_resource() -> None:
    cursor = make_page_cursor(
        run_id="run_1",
        resource="instances",
        last_key={"id": "i1", "createdAt": "2026-01-01T00:00:00+00:00"},
        filter_hash="a" * 64,
        as_of_sequence=3,
    )
    parsed = parse_page_cursor(cursor, run_id="run_1", resource="instances")
    assert parsed["lastKey"]["id"] == "i1"
    with pytest.raises(SnapshotError) as exc:
        parse_page_cursor(cursor, run_id="run_1", resource="stages")
    assert exc.value.code == "snapshot_stale"
    with pytest.raises(SnapshotError):
        parse_page_cursor(cursor + "\n", run_id="run_1", resource="instances")


def test_authorize_run_read_deny_scope() -> None:
    session = MagicMock()
    run = MagicMock()
    run.project_id = "proj_1"
    session.get.side_effect = lambda model, key: run if key == "run_1" else MagicMock()
    principal = AuthenticatedPrincipal(
        credential_id="c1",
        credential_version=1,
        actor_ref="user:api",
        scopes=frozenset({"deny_all"}),
    )
    with pytest.raises(QueryAuthError) as exc:
        authorize_run_read(session, principal, "run_1")
    assert exc.value.code == "forbidden"


def test_build_bounded_snapshot_returns_plain_dto() -> None:
    settings = _settings()
    now = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
    run = MagicMock()
    run.id = "run_1"
    run.project_id = "proj_1"
    run.goal = "ship D12"
    run.mode = "team"
    run.status = "running"
    run.status_version = 1
    run.plan_version = None
    run.assignment_version = None
    run.gate_policy_version = None
    run.active_generation = 1
    run.last_sequence = 0
    run.execution_profile = "host_authorized"
    run.failure_policy = "dependency-aware"
    run.budget = {}
    run.planning_provider_profile_id = None
    run.baseline_id = None
    run.input_refs = []
    run.outputs = ["code"]
    run.cancel_requested_at = None
    run.collaboration_state = "idle"
    run.collaboration_ready_at = None
    run.created_at = now
    run.updated_at = now

    session = MagicMock()
    session.get.return_value = None

    def _scalars(stmt):  # noqa: ANN001, ARG001
        result = MagicMock()
        result.all.return_value = []
        return result

    session.scalars.side_effect = _scalars
    # thread + selection (counts/progress use scalars)
    session.scalar.side_effect = [None, None]

    with patch("agentforge.queries.snapshots.db_now", return_value=now):
        snap = build_bounded_snapshot(session, settings, run)

    assert snap.schema_version == "1"
    assert snap.run.id == "run_1"
    assert snap.latest_sequence == 0
    assert snap.latest_event_id is None
    assert snap.snapshot_token
    assert snap.stage_summary.total == 0
    assert snap.progress.acceptance_status == "none"
    # DTO must not retain ORM session
    assert not hasattr(snap, "_sa_instance_state")
    validate_snapshot_binding(settings, snap.snapshot_token, run_id="run_1")


def test_build_bounded_snapshot_missing_event_raises() -> None:
    settings = _settings()
    run = MagicMock()
    run.id = "run_1"
    run.last_sequence = 5
    run.status_version = 1
    session = MagicMock()
    session.scalar.return_value = None  # no event
    session.get.return_value = None  # no replay window
    with pytest.raises(SnapshotError) as exc:
        build_bounded_snapshot(session, settings, run)
    assert exc.value.code == "storage_integrity"


def test_list_run_details_rejects_filter_change() -> None:
    from agentforge.driver.binding import sha256_hex
    from agentforge.queries.details import list_run_details
    from agentforge.queries.snapshots import make_page_cursor

    settings = _settings()
    run = MagicMock()
    run.id = "run_1"
    run.status_version = 1
    run.last_sequence = 2
    session = MagicMock()
    with patch(
        "agentforge.queries.details.authorize_run_read", return_value=run
    ):
        foreign = make_page_cursor(
            run_id="run_1",
            resource="instances",
            last_key={"id": "i0", "createdAt": "2026-01-01T00:00:00+00:00"},
            filter_hash=sha256_hex({"resource": "other", "runId": "run_1"}),
            as_of_sequence=1,
        )
        with pytest.raises(SnapshotError) as exc:
            list_run_details(
                session,
                AuthenticatedPrincipal("c", 1, "user:api"),
                run_id="run_1",
                resource="instances",
                cursor=foreign,
                limit=10,
                settings=settings,
            )
        assert exc.value.code == "snapshot_stale"
