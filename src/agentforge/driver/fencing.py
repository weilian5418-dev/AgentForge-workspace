"""Run lease fencing for Driver business writes.

Lock order: Run → RunLease → (optional) driver_thread binding checks.
Time is always read with ``clock_timestamp()`` after locks are held.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agentforge.contracts.collaboration import LeaseFence
from agentforge.storage.models import DriverThreadRow, Run, RunLease
from agentforge.storage.uow import unit_of_work_connection

T = TypeVar("T")


class DriverFenceLost(Exception):
    """Current Driver no longer holds a valid lease / binding."""

    code = "driver_fence_lost"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _clock_after_locks(session: Session):
    return session.execute(text("SELECT clock_timestamp()")).scalar_one()


def assert_driver_fence(
    session: Session,
    fence: LeaseFence,
    *,
    require_active_thread: bool = True,
) -> RunLease:
    """Verify lease under Run → RunLease locks using post-lock DB time."""
    run = session.execute(
        select(Run)
        .where(Run.id == fence.run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if run is None:
        raise DriverFenceLost(f"run {fence.run_id} missing")

    lease = session.execute(
        select(RunLease)
        .where(RunLease.run_id == fence.run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()

    now = _clock_after_locks(session)
    if (
        lease is None
        or lease.owner != fence.owner
        or lease.token != fence.token
        or int(lease.generation) != int(fence.lease_generation)
        or lease.expires_at <= now
    ):
        raise DriverFenceLost(f"run lease {fence.run_id} is not valid for this driver")

    if fence.thread_id is not None:
        thread = session.execute(
            select(DriverThreadRow)
            .where(DriverThreadRow.thread_id == fence.thread_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if thread is None:
            raise DriverFenceLost(f"driver thread {fence.thread_id} missing")
        if require_active_thread and thread.status != "active":
            raise DriverFenceLost(f"driver thread {fence.thread_id} is not active")
        if fence.binding_hash and thread.binding_hash != fence.binding_hash:
            raise DriverFenceLost("driver thread binding_hash mismatch")
        if int(thread.execution_generation) != int(run.active_generation):
            raise DriverFenceLost("execution generation drifted from active run")
        if thread.plan_version != (run.plan_version or thread.plan_version):
            raise DriverFenceLost("plan_version drifted from run")
        if thread.assignment_version != (run.assignment_version or thread.assignment_version):
            raise DriverFenceLost("assignment_version drifted from run")

    # Re-check expiry immediately before returning so long lock waits do not slip.
    now2 = _clock_after_locks(session)
    if lease.expires_at <= now2:
        raise DriverFenceLost(f"run lease {fence.run_id} expired before commit window")
    return lease


def run_fenced_transaction(
    engine: Engine,
    fence: LeaseFence,
    apply: Callable[[Session], T],
    *,
    require_active_thread: bool = True,
) -> T:
    """Short bounded UOW: assert fence, apply domain intent, re-check expiry."""

    with unit_of_work_connection(engine) as uow:
        assert_driver_fence(
            uow.session, fence, require_active_thread=require_active_thread
        )
        result = apply(uow.session)
        assert_driver_fence(
            uow.session, fence, require_active_thread=require_active_thread
        )
        return result
