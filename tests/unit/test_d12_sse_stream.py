"""D12.6: SSE client parser, cursor files, stream batching."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentforge.contracts.event_stream import PublicEventEnvelope, SseControlFrame
from agentforge.core.config import Settings
from agentforge.core.credentials import CredentialVerifier
from agentforge.queries.authorization import AuthenticatedPrincipal
from agentforge.streaming.client import (
    ClientRunProjection,
    apply_public_event,
    load_cursor_file,
    parse_sse_stream,
    reconnect_from_cursor,
    save_cursor_file,
)
from agentforge.streaming.framing import (
    encode_business_event,
    encode_control_frame,
    encode_heartbeat,
)
from agentforge.streaming.service import (
    EventStreamService,
    StreamLimitError,
    StreamSlotRegistry,
    read_bounded_batch,
)

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def test_parse_sse_across_chunk_boundaries() -> None:
    env = PublicEventEnvelope.model_validate(
        {
            "eventId": "evt_1",
            "runId": "run_1",
            "sequence": 1,
            "type": "run.status_changed",
            "timestamp": NOW,
            "payload": {"status": "running", "statusVersion": 1},
        }
    )
    frame = encode_business_event(env)
    # Split mid-frame
    mid = len(frame) // 2
    chunks = [frame[:mid], frame[mid:], encode_heartbeat()]
    events = list(parse_sse_stream(chunks))
    assert len(events) == 2
    assert events[0].id == "evt_1"
    assert events[0].event == "run.status_changed"
    assert events[1].comment is True
    payload = json.loads(events[0].data)
    assert payload["sequence"] == 1


def test_control_frame_parsed_without_id() -> None:
    raw = encode_control_frame(
        SseControlFrame.model_validate(
            {
                "event": "resync_required",
                "reason": "cursor_expired",
                "snapshotUrl": "/api/v1/runs/run_1",
            }
        )
    )
    events = list(parse_sse_stream([raw]))
    assert len(events) == 1
    assert events[0].id is None
    assert events[0].event == "resync_required"


def test_cursor_file_rejects_secrets_and_cross_run(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    proj = ClientRunProjection(run_id="run_1", applied_sequence=3, last_applied_event_id="evt_3")
    save_cursor_file(path, proj)
    loaded = load_cursor_file(path, run_id="run_1")
    assert loaded.applied_sequence == 3
    with pytest.raises(ValueError, match="runId"):
        load_cursor_file(path, run_id="run_other")
    path.write_text(json.dumps({"runId": "run_1", "bearer": "nope"}), encoding="utf-8")
    with pytest.raises(ValueError, match="secret"):
        load_cursor_file(path, run_id="run_1")
    assert "bearer" not in reconnect_from_cursor(proj)


def test_apply_public_event_dedupe_and_gap() -> None:
    proj = ClientRunProjection(run_id="run_1", applied_sequence=2)
    assert (
        apply_public_event(
            proj,
            {"runId": "run_1", "sequence": 2, "eventId": "evt_2"},
        )
        is False
    )
    assert (
        apply_public_event(
            proj,
            {"runId": "run_1", "sequence": 3, "eventId": "evt_3"},
        )
        is True
    )
    assert proj.applied_sequence == 3
    with pytest.raises(ValueError, match="gap"):
        apply_public_event(
            proj,
            {"runId": "run_1", "sequence": 5, "eventId": "evt_5"},
        )


@pytest.mark.asyncio
async def test_stream_slot_limits_and_release() -> None:
    slots = StreamSlotRegistry(total_limit=1, per_credential_limit=1)
    await slots.acquire("c1")
    with pytest.raises(StreamLimitError):
        await slots.acquire("c2")
    await slots.release("c1")
    await slots.acquire("c2")
    await slots.release("c2")


def test_read_bounded_batch_resync_when_below_floor() -> None:
    from agentforge.streaming.retention import WindowReadResult

    engine = MagicMock()
    conn = MagicMock()
    session = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    with patch("agentforge.streaming.service.Session") as session_cls:
        session_cls.return_value.__enter__.return_value = session
        with patch("agentforge.streaming.service.authorize_run_read", return_value=MagicMock()):
            with patch(
                "agentforge.streaming.service.read_after_in_window",
                return_value=WindowReadResult(
                    events=[],
                    next_after_sequence=2,
                    status="cursor_expired",
                    reason="cursor_expired",
                ),
            ):
                batch = read_bounded_batch(
                    engine,
                    principal=AuthenticatedPrincipal("c", 1, "user:api"),
                    run_id="run_1",
                    after_sequence=2,
                )
    assert batch.resync_required is True
    assert batch.reason == "cursor_expired"


@pytest.mark.asyncio
async def test_iter_frames_auth_revoked_emits_control() -> None:
    engine = MagicMock()
    settings = Settings(api_token="t" * 48)
    verifier = MagicMock(spec=CredentialVerifier)
    verifier.verify.side_effect = Exception("boom")
    # Make CredentialError path
    from agentforge.core.credentials import CredentialError

    verifier.verify.side_effect = CredentialError("revoked")
    service = EventStreamService(engine, settings, verifier=verifier, slots=StreamSlotRegistry())
    principal = AuthenticatedPrincipal("c1", 1, "cred:c1")

    with patch.object(
        service, "ensure_current_authorization", side_effect=StreamLimitError("auth_revoked", "x")
    ):
        frames = []
        async for frame in service.iter_frames(
            principal=principal,
            run_id="run_1",
            after_sequence=0,
            bearer_token="secret",
            max_stream_seconds=1,
        ):
            frames.append(frame)
            break
    assert frames
    text = frames[0].decode()
    assert text.startswith("event: auth_revoked\n")
    assert "id:" not in text.split("\n")[0]
