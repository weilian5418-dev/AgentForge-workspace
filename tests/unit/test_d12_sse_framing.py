"""D12.5: SSE cursor resolution, public projection, and framing."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agentforge.contracts.event_stream import PublicEventEnvelope, SseControlFrame
from agentforge.queries.authorization import AuthenticatedPrincipal
from agentforge.streaming.cursor import CursorError, resolve_event_cursor
from agentforge.streaming.framing import (
    encode_business_event,
    encode_control_frame,
    encode_heartbeat,
)
from agentforge.streaming.projector import ProjectionError, project_public_event

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
PRINCIPAL = AuthenticatedPrincipal("c1", 1, "user:api")


def test_encode_business_event_escapes_payload_newlines() -> None:
    env = PublicEventEnvelope.model_validate(
        {
            "eventId": "evt_1",
            "runId": "run_1",
            "sequence": 1,
            "type": "run.status_changed",
            "timestamp": NOW,
            "payload": {"note": "line1\nline2", "status": "running", "statusVersion": 1},
        }
    )
    raw = encode_business_event(env).decode("utf-8")
    assert raw.startswith("id: evt_1\n")
    assert "event: run.status_changed\n" in raw
    assert raw.endswith("\n\n")
    # Payload newlines must be JSON-escaped, not raw SSE field breaks
    assert "\ndata: " not in raw.split("data: ", 1)[1].rstrip("\n")
    assert "\\n" in raw
    assert "id: evt_1\nevent:" in raw


def test_encode_control_frame_has_no_id() -> None:
    frame = SseControlFrame.model_validate(
        {
            "event": "resync_required",
            "reason": "cursor_expired",
            "snapshotUrl": "/api/v1/runs/run_1",
            "runId": "run_1",
        }
    )
    raw = encode_control_frame(frame).decode("utf-8")
    assert not raw.startswith("id:")
    assert "\nid:" not in raw
    assert raw.startswith("event: resync_required\n")
    assert "sequence" not in raw
    assert encode_heartbeat() == b": heartbeat\n\n"


def test_project_strips_secrets_and_internal_types() -> None:
    public = project_public_event(
        event_id="evt_1",
        run_id="run_1",
        sequence=2,
        event_type="run.status_changed",
        timestamp=NOW,
        payload={
            "status": "running",
            "statusVersion": 2,
            "apiToken": "secret-value",
            "nested": {"bearerToken": "x", "ok": True},
        },
    )
    assert public.type == "run.status_changed"
    assert "apiToken" not in public.payload
    assert "nested" not in public.payload

    redacted = project_public_event(
        event_id="evt_2",
        run_id="run_1",
        sequence=3,
        event_type="native_event.ingested",
        timestamp=NOW,
        payload={"raw": "should-not-leak"},
    )
    assert redacted.type == "state.invalidated"
    assert redacted.sequence == 3
    assert redacted.payload["originalType"] == "native_event.ingested"


def test_project_unknown_type_rejected() -> None:
    with pytest.raises(ProjectionError) as exc:
        project_public_event(
            event_id="evt_x",
            run_id="run_1",
            sequence=1,
            event_type="totally.unknown",
            timestamp=NOW,
            payload={},
        )
    assert exc.value.code == "unknown_event_type"


def test_resolve_cursor_rejects_crlf_and_cross_run() -> None:
    session = MagicMock()
    run = MagicMock(last_sequence=5)
    with patch("agentforge.streaming.cursor.authorize_run_read", return_value=run):
        session.get.return_value = None
        with pytest.raises(CursorError) as exc:
            resolve_event_cursor(
                session,
                principal=PRINCIPAL,
                run_id="run_1",
                last_event_id="evt\n1",
            )
        assert exc.value.code == "cursor_invalid"

        foreign = MagicMock(run_id="run_other", sequence=3, event_id="evt_f")
        session.scalar.return_value = foreign
        with pytest.raises(CursorError) as scope:
            resolve_event_cursor(
                session,
                principal=PRINCIPAL,
                run_id="run_1",
                last_event_id="evt_f",
            )
        assert scope.value.code == "cursor_scope_mismatch"


def test_resolve_cursor_ahead_and_empty_run_zero() -> None:
    session = MagicMock()
    run = MagicMock(last_sequence=0)
    with patch("agentforge.streaming.cursor.authorize_run_read", return_value=run):
        session.get.return_value = None
        both_none = resolve_event_cursor(session, principal=PRINCIPAL, run_id="run_1")
        assert both_none.resync_required is True
        assert both_none.reason == "snapshot_required"

        ok = resolve_event_cursor(session, principal=PRINCIPAL, run_id="run_1", after_sequence=0)
        assert ok.resync_required is False
        assert ok.after_sequence == 0
        assert ok.latest_sequence == 0

        run.last_sequence = 4
        with pytest.raises(CursorError) as ahead:
            resolve_event_cursor(session, principal=PRINCIPAL, run_id="run_1", after_sequence=9)
        assert ahead.value.code == "cursor_ahead"


def test_resolve_cursor_conflict_when_header_and_query_disagree() -> None:
    session = MagicMock()
    run = MagicMock(last_sequence=10)
    event = MagicMock(run_id="run_1", sequence=4, event_id="evt_4")
    with patch("agentforge.streaming.cursor.authorize_run_read", return_value=run):
        session.get.return_value = None
        session.scalar.return_value = event
        with pytest.raises(CursorError) as conflict:
            resolve_event_cursor(
                session,
                principal=PRINCIPAL,
                run_id="run_1",
                last_event_id="evt_4",
                after_sequence=7,
            )
        assert conflict.value.code == "cursor_conflict"
