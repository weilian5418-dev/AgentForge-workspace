"""D11.4: wake dedupe, wait register/observe order, allowlist."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentforge.contracts.collaboration import LeaseFence
from agentforge.driver.binding import build_wait_key, sha256_hex
from agentforge.driver.signals import WAKE_SOURCE_ALLOWLIST, record_wake_signal
from agentforge.driver.waits import WaitOutcome, WaitService, evaluate_wait_predicate
from agentforge.storage.models import Outbox, Run, RunWaitRow, RunWakeSignalRow


NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


class _MemSession:
    def __init__(self) -> None:
        self.store: dict[tuple[type, Any], Any] = {}
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)
        key = getattr(row, "id", None)
        if key is not None:
            self.store[(type(row), key)] = row
        if isinstance(row, Outbox):
            self.store[(Outbox, row.idempotency_key)] = row

    def flush(self) -> None:
        return None

    def get(self, model: type, key: Any, **_kwargs: Any) -> Any | None:
        return self.store.get((model, key))

    def scalar(self, stmt: Any) -> Any | None:
        text = str(stmt).lower()
        if "run_wake_signals" in text:
            for r in self.added:
                if isinstance(r, RunWakeSignalRow):
                    return r
            return None
        if "outbox" in text:
            for r in self.added:
                if isinstance(r, Outbox):
                    return r
            return None
        if "run_waits" in text:
            for r in self.added:
                if isinstance(r, RunWaitRow):
                    return r
            return None
        if "from app.runs" in text or "runs" in text:
            return self.store.get((Run, "run_1"))
        return None

    def execute(self, stmt: Any) -> Any:
        return MagicMock(scalar_one_or_none=lambda: None, scalar_one=lambda: NOW)


def test_wake_allowlist_rejects_heartbeat() -> None:
    session = _MemSession()
    with pytest.raises(ValueError, match="allowlisted"):
        record_wake_signal(
            session,  # type: ignore[arg-type]
            run_id="run_1",
            source_kind="heartbeat",
            source_ref="h1",
        )


def test_wake_dedupes_same_source_ref() -> None:
    session = _MemSession()
    with patch("agentforge.driver.signals.append_fact"), patch(
        "agentforge.driver.signals.db_now", return_value=NOW
    ):
        a = record_wake_signal(
            session,  # type: ignore[arg-type]
            run_id="run_1",
            source_kind="control",
            source_ref="op:pause:1",
        )
        b = record_wake_signal(
            session,  # type: ignore[arg-type]
            run_id="run_1",
            source_kind="control",
            source_ref="op:pause:1",
        )
    assert a.wake_signal_id == b.wake_signal_id
    wakes = [r for r in session.added if isinstance(r, RunWakeSignalRow)]
    assert len(wakes) == 1
    outboxes = [r for r in session.added if isinstance(r, Outbox)]
    assert len(outboxes) == 1
    assert outboxes[0].intent_type == "driver.wake"


def test_wait_key_stable_for_scope() -> None:
    scope = {"instanceId": "inst_1"}
    assert build_wait_key("session_ready", scope) == build_wait_key(
        "session_ready", {"instanceId": "inst_1"}
    )
    assert sha256_hex(scope) == sha256_hex({"instanceId": "inst_1"})


def test_register_or_observe_satisfied_skips_insert() -> None:
    session = _MemSession()
    run = Run(
        id="run_1",
        project_id="p1",
        goal="g",
        mode="team",
        status="running",
        status_version=1,
        active_generation=1,
        last_sequence=3,
        execution_profile="host_authorized",
        failure_policy="dependency-aware",
        budget={},
        outputs=["code"],
        created_at=NOW,
        updated_at=NOW,
    )
    session.store[(Run, "run_1")] = run
    fence = LeaseFence(
        run_id="run_1", owner="o", token="t", lease_generation=1
    )
    with patch("agentforge.driver.waits.assert_driver_fence", return_value=None), patch(
        "agentforge.driver.waits.evaluate_wait_predicate", return_value=True
    ), patch("agentforge.driver.waits.db_now", return_value=NOW):
        outcome = WaitService(session).register_or_observe(  # type: ignore[arg-type]
            fence,
            wait_key="plan_confirmation:abc",
            reason="plan_confirmation",
            scope={},
        )
    assert outcome.status == "satisfied"
    assert outcome.wait is None
    assert not any(isinstance(r, RunWaitRow) for r in session.added)


def test_register_or_observe_creates_open_wait() -> None:
    session = _MemSession()
    run = Run(
        id="run_1",
        project_id="p1",
        goal="g",
        mode="team",
        status="running",
        status_version=1,
        active_generation=1,
        last_sequence=7,
        execution_profile="host_authorized",
        failure_policy="dependency-aware",
        budget={},
        outputs=["code"],
        created_at=NOW,
        updated_at=NOW,
    )
    session.store[(Run, "run_1")] = run
    fence = LeaseFence(
        run_id="run_1", owner="o", token="t", lease_generation=1
    )
    with patch("agentforge.driver.waits.assert_driver_fence", return_value=None), patch(
        "agentforge.driver.waits.evaluate_wait_predicate", return_value=False
    ), patch("agentforge.driver.waits.append_fact"), patch(
        "agentforge.driver.waits.db_now", return_value=NOW
    ):
        outcome = WaitService(session).register_or_observe(  # type: ignore[arg-type]
            fence,
            wait_key="capacity:xyz",
            reason="capacity",
            scope={"instanceId": "inst_1"},
            deadline_at=NOW + timedelta(minutes=5),
        )
    assert isinstance(outcome, WaitOutcome)
    assert outcome.status == "waiting"
    assert outcome.wait is not None
    assert outcome.wait.after_sequence == 7
    assert outcome.wait.status == "open"


def test_driver_yield_predicate_not_auto_true() -> None:
    assert evaluate_wait_predicate(
        MagicMock(), run_id="run_1", reason="driver_yield", scope={}
    ) is False


def test_wake_allowlist_covers_d11_sources() -> None:
    for kind in (
        "plan_confirmation",
        "instance_operation",
        "delivery",
        "stage_gate",
        "control",
        "timer",
        "sweep",
    ):
        assert kind in WAKE_SOURCE_ALLOWLIST
