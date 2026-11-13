"""D11.3: fence helpers reject stale leases without inventing native stop facts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentforge.contracts.collaboration import LeaseFence
from agentforge.driver.fencing import DriverFenceLost, assert_driver_fence


class _Scalar:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalar_one(self) -> Any:
        return self._value


def test_assert_driver_fence_rejects_expired_lease() -> None:
    now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    run = MagicMock(id="run_1", active_generation=1, plan_version=1, assignment_version=1)
    lease = MagicMock(
        owner="owner_a",
        token="tok",
        generation=1,
        expires_at=now - timedelta(seconds=1),
    )
    session = MagicMock()
    # Run lock, lease lock, then two clock reads
    session.execute.side_effect = [
        _Scalar(run),
        _Scalar(lease),
        _Scalar(now),
        _Scalar(now),
    ]
    fence = LeaseFence.model_validate(
        {
            "runId": "run_1",
            "owner": "owner_a",
            "token": "tok",
            "leaseGeneration": 1,
        }
    )
    with pytest.raises(DriverFenceLost):
        assert_driver_fence(session, fence, require_active_thread=False)


def test_assert_driver_fence_rejects_owner_mismatch() -> None:
    now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    run = MagicMock(id="run_1", active_generation=1, plan_version=1, assignment_version=1)
    lease = MagicMock(
        owner="other",
        token="tok",
        generation=1,
        expires_at=now + timedelta(seconds=30),
    )
    session = MagicMock()
    session.execute.side_effect = [
        _Scalar(run),
        _Scalar(lease),
        _Scalar(now),
        _Scalar(now),
    ]
    fence = LeaseFence(
        run_id="run_1",
        owner="owner_a",
        token="tok",
        lease_generation=1,
    )
    with pytest.raises(DriverFenceLost):
        assert_driver_fence(session, fence, require_active_thread=False)


def test_assert_driver_fence_accepts_valid_lease() -> None:
    now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    run = MagicMock(id="run_1", active_generation=1, plan_version=1, assignment_version=1)
    lease = MagicMock(
        owner="owner_a",
        token="tok",
        generation=2,
        expires_at=now + timedelta(seconds=30),
    )
    session = MagicMock()
    session.execute.side_effect = [
        _Scalar(run),
        _Scalar(lease),
        _Scalar(now),
        _Scalar(now),
    ]
    fence = LeaseFence(
        run_id="run_1",
        owner="owner_a",
        token="tok",
        lease_generation=2,
    )
    got = assert_driver_fence(session, fence, require_active_thread=False)
    assert got is lease


def test_fenced_saver_delegates_get_next_version() -> None:
    from agentforge.driver.checkpointer import FencedPostgresSaver

    conn = MagicMock()
    delegate = MagicMock()
    delegate.get_next_version.return_value = "1.0"
    fence = LeaseFence(run_id="run_1", owner="o", token="t", lease_generation=1)
    saver = FencedPostgresSaver(conn, fence, delegate=delegate)
    assert saver.get_next_version(None, None) == "1.0"
    assert saver.serde is delegate.serde
