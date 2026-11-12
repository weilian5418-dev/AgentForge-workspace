"""D12.7: replay window, gap detection, archive watermark safety."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentforge.streaming.retention import (
    RetentionError,
    ReplayWindow,
    advance_replay_floor,
    archive_event_range,
    get_replay_window,
    read_after_in_window,
)


def test_read_after_expired_gap_ahead_caught_up() -> None:
    session = MagicMock()
    base = ReplayWindow(
        run_id="run_1",
        replay_floor_sequence=5,
        last_archived_sequence=5,
        retention_version=1,
        latest_sequence=10,
    )
    with patch(
        "agentforge.streaming.retention.get_replay_window", return_value=base
    ):
        assert (
            read_after_in_window(session, run_id="run_1", after_sequence=2).status
            == "cursor_expired"
        )
        assert (
            read_after_in_window(session, run_id="run_1", after_sequence=12).status
            == "cursor_ahead"
        )
        assert (
            read_after_in_window(session, run_id="run_1", after_sequence=10).status
            == "caught_up"
        )
        result = MagicMock()
        result.all.return_value = []
        session.scalars.return_value = result
        assert (
            read_after_in_window(session, run_id="run_1", after_sequence=5).status
            == "gap_detected"
        )


def test_read_after_contiguous_ok() -> None:
    session = MagicMock()
    base = ReplayWindow(
        run_id="run_1",
        replay_floor_sequence=0,
        last_archived_sequence=0,
        retention_version=1,
        latest_sequence=3,
    )
    e1 = MagicMock(sequence=1)
    e2 = MagicMock(sequence=2)
    result = MagicMock()
    result.all.return_value = [e1, e2]
    session.scalars.return_value = result
    with patch(
        "agentforge.streaming.retention.get_replay_window", return_value=base
    ):
        out = read_after_in_window(session, run_id="run_1", after_sequence=0)
    assert out.status == "ok"
    assert out.next_after_sequence == 2
    assert len(out.events) == 2


def test_advance_floor_refuses_delete_without_archive() -> None:
    session = MagicMock()
    run = MagicMock(last_sequence=20)
    window = MagicMock(
        replay_floor_sequence=0,
        last_archived_sequence=0,
        retention_version=1,
    )

    def _get(model, key, **kwargs):  # noqa: ANN001, ARG001
        name = getattr(model, "__name__", "")
        if name == "Run":
            return run
        return window

    session.get.side_effect = _get
    with patch(
        "agentforge.streaming.retention.ensure_replay_window", return_value=window
    ):
        with patch("agentforge.streaming.retention.db_now", return_value=MagicMock()):
            with pytest.raises(RetentionError) as exc:
                advance_replay_floor(
                    session, run_id="run_1", new_floor=5, delete_online=True
                )
            assert exc.value.code == "archive_required"

            with patch(
                "agentforge.streaming.retention.get_replay_window",
                return_value=ReplayWindow(
                    run_id="run_1",
                    replay_floor_sequence=5,
                    last_archived_sequence=0,
                    retention_version=2,
                    latest_sequence=20,
                ),
            ):
                out = advance_replay_floor(
                    session, run_id="run_1", new_floor=5, delete_online=False
                )
            assert window.replay_floor_sequence == 5
            assert out.replay_floor_sequence == 5


def test_archive_failure_does_not_raise_watermark(tmp_path: Path) -> None:
    session = MagicMock()
    run = MagicMock(last_sequence=3)
    window = MagicMock(
        replay_floor_sequence=0,
        last_archived_sequence=0,
        retention_version=1,
    )

    def _get(model, key, **kwargs):  # noqa: ANN001, ARG001
        name = getattr(model, "__name__", "")
        if name == "Run":
            return run
        return window

    session.get.side_effect = _get
    e1 = MagicMock(
        event_id="evt_1",
        sequence=1,
        type="run.status_changed",
        payload={},
        created_at=MagicMock(isoformat=lambda: "t"),
    )
    e2 = MagicMock(
        event_id="evt_2",
        sequence=2,
        type="run.status_changed",
        payload={},
        created_at=MagicMock(isoformat=lambda: "t"),
    )
    result = MagicMock()
    result.all.return_value = [e1, e2]
    session.scalars.return_value = result

    with patch(
        "agentforge.streaming.retention.ensure_replay_window", return_value=window
    ):
        with patch("agentforge.streaming.retention.db_now", return_value=MagicMock()):
            with patch.object(Path, "read_bytes", return_value=b"corrupt"):
                with pytest.raises(RetentionError) as exc:
                    archive_event_range(
                        session,
                        run_id="run_1",
                        from_sequence_exclusive=0,
                        to_sequence_inclusive=2,
                        archive_dir=tmp_path,
                    )
                assert exc.value.code == "archive_failed"
    assert window.last_archived_sequence == 0
    assert window.replay_floor_sequence == 0


def test_get_replay_window_rejects_floor_past_latest() -> None:
    session = MagicMock()
    run = MagicMock(last_sequence=3)
    window = MagicMock(
        replay_floor_sequence=9,
        last_archived_sequence=0,
        retention_version=1,
    )
    session.get.return_value = run
    with patch(
        "agentforge.streaming.retention.ensure_replay_window", return_value=window
    ):
        with pytest.raises(RetentionError) as exc:
            get_replay_window(session, "run_1")
        assert exc.value.code == "storage_integrity"
