"""drive_once — one bounded collaboration fragment (D11.8)."""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from agentforge.contracts.collaboration import (
    GRAPH_VERSION,
    STATE_SCHEMA_VERSION,
    DriverResult,
    LeaseFence,
)
from agentforge.core.config import Settings
from agentforge.driver.binding import binding_hash, build_thread_id, default_binding
from agentforge.driver.checkpointer import (
    FencedPostgresSaver,
    open_graph_connection,
    release_graph_advisory_lock,
    try_acquire_graph_advisory_lock,
)
from agentforge.driver.fencing import DriverFenceLost
from agentforge.driver.graph import build_collaboration_graph
from agentforge.driver.repository import DriverRepository
from agentforge.driver.signals import claim_pending_signals, complete_observed_signals
from agentforge.driver.state import DriverContext
from agentforge.driver.waits import WaitService
from agentforge.storage.models import DriverThreadRow, Run
from agentforge.storage.exceptions import LeaseConflictError
from agentforge.storage.repositories.leases import LeaseRepository
from agentforge.storage.uow import db_now, unit_of_work_connection

logger = logging.getLogger(__name__)

DEFAULT_FRAGMENT_STEPS = 8
DEFAULT_LEASE_TTL = 90
HEARTBEAT_INTERVAL_SEC = 25


def _now(session: Session) -> datetime:
    value = db_now(session)
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=UTC)  # type: ignore[union-attr]
    return value  # type: ignore[return-value]


def sqlalchemy_url_to_psycopg_dsn(url: str) -> str:
    parsed = make_url(url)
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


def _ensure_driver_thread(session: Session, run: Run) -> DriverThreadRow:
    if (
        run.plan_version is None
        or run.assignment_version is None
        or run.gate_policy_version is None
    ):
        raise ValueError("run missing plan/assignment/gate versions for driver thread")
    binding = default_binding(
        run_id=run.id,
        plan_version=int(run.plan_version),
        assignment_version=int(run.assignment_version),
        gate_policy_version=int(run.gate_policy_version),
        execution_generation=int(run.active_generation),
    )
    thread_id = build_thread_id(binding)
    bhash = binding_hash(binding)
    repo = DriverRepository(session)
    existing = repo.get_thread_by_id(thread_id)
    if existing is not None:
        if existing.status != "active":
            raise ValueError("driver thread exists but is not active")
        return existing
    active = repo.get_active_thread(run.id)
    if active is not None and active.thread_id != thread_id:
        # Binding changed — supersede prior active thread (wake must not mint casually).
        active.status = "superseded"
        active.updated_at = _now(session)
    row = DriverThreadRow(
        id=f"dth_{uuid.uuid4().hex[:12]}",
        run_id=run.id,
        thread_id=thread_id,
        plan_version=binding.plan_version,
        assignment_version=binding.assignment_version,
        gate_policy_version=binding.gate_policy_version,
        execution_generation=binding.execution_generation,
        graph_version=GRAPH_VERSION,
        state_schema_version=STATE_SCHEMA_VERSION,
        checkpoint_epoch=binding.checkpoint_epoch,
        binding_hash=bhash,
        status="active",
        created_at=_now(session),
        updated_at=_now(session),
    )
    repo.add_thread(row)
    session.flush()
    return row


class _Heartbeat(threading.Thread):
    def __init__(
        self,
        engine: Engine,
        *,
        run_id: str,
        owner: str,
        token: str,
        generation: int,
        ttl: int,
        stop: threading.Event,
    ) -> None:
        super().__init__(daemon=True, name=f"driver-hb-{run_id[:8]}")
        self.engine = engine
        self.run_id = run_id
        self.owner = owner
        self.token = token
        self.generation = generation
        self.ttl = ttl
        self.stop = stop

    def run(self) -> None:
        while not self.stop.wait(HEARTBEAT_INTERVAL_SEC):
            try:
                with unit_of_work_connection(self.engine) as uow:
                    LeaseRepository(uow).renew_run_lease(
                        run_id=self.run_id,
                        owner=self.owner,
                        token=self.token,
                        generation=self.generation,
                        ttl_seconds=self.ttl,
                    )
            except Exception:
                logger.warning("driver heartbeat renew failed for %s", self.run_id, exc_info=True)
                return


def drive_once(
    settings: Settings,
    *,
    run_id: str,
    wake_signal_id: str | None = None,
    owner: str | None = None,
    engine: Engine | None = None,
    fragment_steps: int = DEFAULT_FRAGMENT_STEPS,
) -> DriverResult:
    """Coordinate one bounded fragment: lease → claim → graph → complete signals."""
    from agentforge.storage.database import create_database_engine

    owns_engine = engine is None
    eng = engine or create_database_engine(settings)
    if eng is None:
        raise ValueError("Database is not configured")
    if settings.database_url is None:
        raise ValueError("Database URL required for graph checkpointer")

    owner_id = owner or f"driver:{uuid.uuid4().hex[:10]}"
    dsn = sqlalchemy_url_to_psycopg_dsn(settings.database_url.get_secret_value())
    graph_conn = open_graph_connection(dsn)
    locked = False
    fence: LeaseFence | None = None
    stop_hb = threading.Event()
    hb: _Heartbeat | None = None
    claimed_ids: list[str] = []

    try:
        locked = try_acquire_graph_advisory_lock(graph_conn, run_id)
        if not locked:
            return DriverResult(
                run_id=run_id,
                outcome="busy",
                error_code="driver_busy",
                message="another driver holds the graph advisory lock",
            )

        # 1) Acquire lease + ensure thread + claim signals (app UOW).
        with unit_of_work_connection(eng) as uow:
            run = uow.session.get(Run, run_id, with_for_update=True, populate_existing=True)
            if run is None:
                return DriverResult(run_id=run_id, outcome="blocked", message="run not found")
            try:
                lease = LeaseRepository(uow).acquire_run_lease(
                    run_id=run_id, owner=owner_id, ttl_seconds=DEFAULT_LEASE_TTL
                )
            except LeaseConflictError as exc:
                return DriverResult(
                    run_id=run_id,
                    outcome="busy",
                    error_code="driver_busy",
                    message=str(exc),
                )
            thread = _ensure_driver_thread(uow.session, run)
            fence = LeaseFence(
                run_id=run_id,
                owner=lease.owner,
                token=lease.token,
                lease_generation=int(lease.generation),
                thread_id=thread.thread_id,
                binding_hash=thread.binding_hash,
            )
            claimed = claim_pending_signals(
                uow.session, run_id=run_id, owner=owner_id, limit=16
            )
            claimed_ids = [s.wake_signal_id for s in claimed]
            if wake_signal_id and wake_signal_id not in claimed_ids:
                claimed_ids.append(wake_signal_id)

        assert fence is not None
        hb = _Heartbeat(
            eng,
            run_id=run_id,
            owner=fence.owner,
            token=fence.token,
            generation=fence.lease_generation,
            ttl=DEFAULT_LEASE_TTL,
            stop=stop_hb,
        )
        hb.start()

        # 2) Pending interrupt resume only when wait is satisfied/canceled.
        saver = FencedPostgresSaver(graph_conn, fence)
        graph = build_collaboration_graph(checkpointer=saver)
        config = {
            "configurable": {"thread_id": fence.thread_id},
            "recursion_limit": max(24, fragment_steps * 4),
        }
        ctx = DriverContext(
            engine=eng,
            settings=settings,
            fence=fence,
            state_dir=settings.state_dir,
            claimed_wake_ids=list(claimed_ids),
        )

        resume_cmd = None
        try:
            snapshot = graph.get_state(config)
            if snapshot and snapshot.tasks:
                for task in snapshot.tasks:
                    for intr in task.interrupts or ():
                        wait_id = None
                        if isinstance(intr.value, dict):
                            wait_id = intr.value.get("waitId") or intr.value.get("wait_id")
                        if not wait_id:
                            continue
                        with unit_of_work_connection(eng) as uow:
                            outcome = WaitService(uow.session).recheck_open_wait(str(wait_id))
                        if outcome.status == "satisfied":
                            from langgraph.types import Command

                            resume_cmd = Command(
                                resume={
                                    "waitId": wait_id,
                                    "wakeSignalId": wake_signal_id,
                                }
                            )
                        else:
                            # Keep waiting — complete only wakes that do not require resume.
                            with unit_of_work_connection(eng) as uow:
                                complete_observed_signals(uow.session, claimed_ids)
                            return DriverResult(
                                run_id=run_id,
                                thread_id=fence.thread_id,
                                outcome="interrupted",
                                wait_id=str(wait_id),
                                wait_reason=outcome.wait.reason if outcome.wait else None,
                                observed_sequence=None,
                                message="wait still open",
                            )
        except Exception:
            logger.debug("get_state/resume probe failed; starting fresh invoke", exc_info=True)

        initial: dict[str, Any] = {
            "run_id": run_id,
            "thread_id": fence.thread_id,
            "steps_remaining": fragment_steps,
            "wait_ref": None,
            "fragment_outcome": None,
        }
        try:
            if resume_cmd is not None:
                result_state = graph.invoke(resume_cmd, config=config, context=ctx)
            else:
                result_state = graph.invoke(initial, config=config, context=ctx)
        except DriverFenceLost as exc:
            return DriverResult(
                run_id=run_id,
                thread_id=fence.thread_id,
                outcome="fence_lost",
                error_code="driver_fence_lost",
                message=exc.message,
            )

        # 3) Interpret outcome from interrupts / final state.
        outcome = "yielded"
        wait_id = None
        wait_reason = None
        if isinstance(result_state, dict):
            if result_state.get("__interrupt__"):
                outcome = "interrupted"
                intrs = result_state["__interrupt__"]
                if intrs:
                    val = getattr(intrs[0], "value", None) or {}
                    if isinstance(val, dict):
                        wait_id = val.get("waitId")
                        wait_reason = val.get("reason")
            else:
                fo = result_state.get("fragment_outcome")
                if fo in {
                    "ready_for_integration",
                    "blocked",
                    "yielded",
                    "interrupted",
                    "fence_lost",
                }:
                    outcome = fo
                elif result_state.get("collaboration_state") == "ready_for_integration":
                    outcome = "ready_for_integration"
                elif result_state.get("next_action") == "end":
                    outcome = result_state.get("fragment_outcome") or "yielded"

            # Persist thread cursor.
            with unit_of_work_connection(eng) as uow:
                thread_row = DriverRepository(uow.session).get_thread_by_id(fence.thread_id or "")
                if thread_row is not None:
                    thread_row.last_observed_sequence = int(
                        result_state.get("observed_sequence") or 0
                    )
                    thread_row.updated_at = _now(uow.session)
                complete_observed_signals(uow.session, claimed_ids)

            return DriverResult(
                run_id=run_id,
                thread_id=fence.thread_id,
                outcome=outcome,  # type: ignore[arg-type]
                next_action=result_state.get("next_action"),  # type: ignore[arg-type]
                wait_id=wait_id,
                wait_reason=wait_reason,  # type: ignore[arg-type]
                operation_ref=result_state.get("operation_ref"),
                recovery_case_ref=result_state.get("recovery_case_ref"),
                handoff_ref=result_state.get("handoff_ref"),
                observed_sequence=result_state.get("observed_sequence"),
                error_code=None,
                message=result_state.get("last_error") or result_state.get("action_detail"),
            )

        with unit_of_work_connection(eng) as uow:
            complete_observed_signals(uow.session, claimed_ids)
        return DriverResult(run_id=run_id, thread_id=fence.thread_id, outcome="yielded")

    finally:
        stop_hb.set()
        if hb is not None:
            hb.join(timeout=2)
        if fence is not None:
            try:
                with unit_of_work_connection(eng) as uow:
                    LeaseRepository(uow).release_run_lease(
                        run_id=fence.run_id,
                        owner=fence.owner,
                        token=fence.token,
                        generation=fence.lease_generation,
                    )
            except Exception:
                logger.debug("lease release skipped", exc_info=True)
        if locked:
            try:
                release_graph_advisory_lock(graph_conn, run_id)
            except Exception:
                logger.debug("advisory unlock skipped", exc_info=True)
        try:
            graph_conn.close()
        except Exception:
            pass
        if owns_engine and eng is not None:
            eng.dispose()
