from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from agentforge.storage.exceptions import (
    AttemptSlotBusyError,
    LeaseConflictError,
    LeaseExpiredError,
)
from agentforge.storage.models import Run, RunLease, TaskAttempt
from agentforge.storage.uow import UnitOfWork, db_now

ACTIVE_ATTEMPT_STATUSES = ("leased", "running", "awaiting_stop_verification")


class LeaseRepository:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def _lock_run(self, run_id: str) -> Run:
        """Lock order protocol: always acquire the Run row before attempts."""
        session = self._uow.session
        run = session.execute(
            select(Run)
            .where(Run.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if run is None:
            raise LeaseExpiredError(f"run {run_id} missing")
        session.refresh(run)
        return run

    def _lock_attempt(self, attempt_id: str) -> TaskAttempt | None:
        session = self._uow.session
        attempt = session.execute(
            select(TaskAttempt)
            .where(TaskAttempt.id == attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if attempt is not None:
            session.refresh(attempt)
        return attempt

    def acquire_run_lease(self, *, run_id: str, owner: str, ttl_seconds: int = 60) -> RunLease:
        """Acquire Driver control. `generation` increments on each re-acquire."""
        session = self._uow.session
        now = db_now(session)
        expires = now + timedelta(seconds=ttl_seconds)
        token = secrets.token_urlsafe(24)

        # Lock the stable run row first so first-time inserts cannot race on PK.
        run = session.execute(
            select(Run)
            .where(Run.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if run is None:
            raise LeaseConflictError(f"run {run_id} does not exist")
        session.refresh(run)

        existing = session.execute(
            select(RunLease)
            .where(RunLease.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if existing is not None:
            session.refresh(existing)

        if existing is None:
            lease = RunLease(
                run_id=run_id,
                owner=owner,
                generation=1,
                token=token,
                expires_at=expires,
            )
            try:
                with session.begin_nested():
                    session.add(lease)
                    session.flush()
            except IntegrityError as exc:
                raise LeaseConflictError(f"run {run_id} lease contested") from exc
            return lease

        if existing.expires_at > now and existing.owner != owner:
            raise LeaseConflictError(f"run {run_id} held by {existing.owner}")

        existing.owner = owner
        existing.generation = int(existing.generation) + 1
        existing.token = token
        existing.expires_at = expires
        existing.updated_at = now
        session.flush()
        return existing

    def renew_run_lease(
        self,
        *,
        run_id: str,
        owner: str,
        token: str,
        generation: int,
        ttl_seconds: int = 60,
    ) -> RunLease:
        session = self._uow.session
        # Lock Run first; never treat acquire as renew — generation stays fixed.
        self._lock_run(run_id)
        lease = session.execute(
            select(RunLease)
            .where(RunLease.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        now = db_now(session)
        if (
            lease is None
            or lease.owner != owner
            or lease.token != token
            or int(lease.generation) != int(generation)
            or lease.expires_at <= now
        ):
            raise LeaseExpiredError(f"cannot renew run lease {run_id}")
        expires = now + timedelta(seconds=ttl_seconds)
        lease.expires_at = expires
        lease.updated_at = now
        session.flush()
        return lease

    def release_run_lease(self, *, run_id: str, owner: str, token: str, generation: int) -> None:
        session = self._uow.session
        self._lock_run(run_id)
        now = db_now(session)
        released = session.execute(
            update(RunLease)
            .where(
                RunLease.run_id == run_id,
                RunLease.owner == owner,
                RunLease.token == token,
                RunLease.generation == generation,
            )
            .values(expires_at=now, updated_at=now)
            .returning(RunLease.run_id)
        ).scalar_one_or_none()
        if released is None:
            raise LeaseConflictError(f"cannot release run lease {run_id}")

    def verify_run_lease(self, *, run_id: str, owner: str, token: str, generation: int) -> RunLease:
        """Lock Run then lease; compare expiry with post-lock ``clock_timestamp()``."""
        session = self._uow.session
        self._lock_run(run_id)
        lease = session.execute(
            select(RunLease)
            .where(RunLease.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if lease is not None:
            session.refresh(lease)
        now = db_now(session)
        if (
            lease is None
            or lease.owner != owner
            or lease.token != token
            or int(lease.generation) != int(generation)
            or lease.expires_at <= now
        ):
            raise LeaseExpiredError(f"run lease {run_id} is not valid")
        return lease

    def create_attempt(
        self,
        *,
        attempt_id: str,
        work_package_id: str,
        run_id: str,
        run_generation: int,
        ttl_seconds: int = 60,
        worker_ref: str | None = None,
    ) -> TaskAttempt:
        session = self._uow.session
        now = db_now(session)
        active = session.execute(
            select(TaskAttempt.id).where(
                TaskAttempt.work_package_id == work_package_id,
                TaskAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES),
            )
        ).first()
        if active is not None:
            raise AttemptSlotBusyError(
                f"work package {work_package_id} still has an active attempt"
            )
        attempt = TaskAttempt(
            id=attempt_id,
            work_package_id=work_package_id,
            run_id=run_id,
            run_generation=run_generation,
            lease_token=secrets.token_urlsafe(24),
            lease_until=now + timedelta(seconds=ttl_seconds),
            worker_ref=worker_ref,
            status="leased",
        )
        session.add(attempt)
        session.flush()
        return attempt

    def renew_attempt(
        self, *, attempt_id: str, lease_token: str, ttl_seconds: int = 60
    ) -> TaskAttempt:
        session = self._uow.session
        now = db_now(session)
        meta = session.execute(
            select(TaskAttempt.run_id, TaskAttempt.run_generation).where(
                TaskAttempt.id == attempt_id
            )
        ).one_or_none()
        if meta is None:
            raise LeaseExpiredError(f"cannot renew attempt {attempt_id}")
        run_id, attempt_generation = str(meta[0]), int(meta[1])
        run = self._lock_run(run_id)
        if int(run.active_generation) != attempt_generation:
            raise LeaseExpiredError(f"cannot renew attempt {attempt_id}")
        attempt = self._lock_attempt(attempt_id)
        session.refresh(run)
        if (
            attempt is None
            or attempt.lease_token != lease_token
            or attempt.status not in ("leased", "running")
            or attempt.lease_until <= now
            or int(attempt.run_generation) != attempt_generation
            or int(run.active_generation) != attempt_generation
        ):
            raise LeaseExpiredError(f"cannot renew attempt {attempt_id}")
        # Defensive CAS write so a stale ORM status cannot overwrite stop-verification.
        result = session.execute(
            update(TaskAttempt)
            .where(
                TaskAttempt.id == attempt_id,
                TaskAttempt.lease_token == lease_token,
                TaskAttempt.run_generation == attempt_generation,
                TaskAttempt.status.in_(("leased", "running")),
                TaskAttempt.lease_until > now,
            )
            .values(lease_until=now + timedelta(seconds=ttl_seconds), updated_at=now)
            .returning(TaskAttempt)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise LeaseExpiredError(f"cannot renew attempt {attempt_id}")
        session.refresh(row)
        return row

    def complete_attempt(
        self,
        *,
        attempt_id: str,
        lease_token: str,
        run_generation: int,
        status: str,
    ) -> TaskAttempt:
        session = self._uow.session
        now = db_now(session)
        meta = session.execute(
            select(TaskAttempt.run_id).where(TaskAttempt.id == attempt_id)
        ).one_or_none()
        if meta is None:
            raise LeaseExpiredError(f"attempt {attempt_id} result rejected")
        run_id = str(meta[0])
        # Lock Run first so bump_active_generation cannot commit under a waiting completion.
        run = self._lock_run(run_id)
        if int(run.active_generation) != run_generation:
            raise LeaseExpiredError(f"attempt {attempt_id} result rejected")
        attempt = self._lock_attempt(attempt_id)
        session.refresh(run)
        if (
            attempt is None
            or attempt.lease_token != lease_token
            or int(attempt.run_generation) != run_generation
            or attempt.status not in ("leased", "running")
            or attempt.lease_until <= now
            or int(run.active_generation) != run_generation
        ):
            raise LeaseExpiredError(f"attempt {attempt_id} result rejected")
        # CAS on status so preloaded ORM rows cannot overwrite awaiting_stop_verification.
        result = session.execute(
            update(TaskAttempt)
            .where(
                TaskAttempt.id == attempt_id,
                TaskAttempt.lease_token == lease_token,
                TaskAttempt.run_generation == run_generation,
                TaskAttempt.status.in_(("leased", "running")),
                TaskAttempt.lease_until > now,
            )
            .values(status=status, updated_at=now)
            .returning(TaskAttempt)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise LeaseExpiredError(f"attempt {attempt_id} result rejected")
        session.refresh(row)
        return row

    def mark_awaiting_stop(self, *, attempt_id: str, lease_token: str) -> TaskAttempt:
        session = self._uow.session
        result = session.execute(
            update(TaskAttempt)
            .where(
                TaskAttempt.id == attempt_id,
                TaskAttempt.lease_token == lease_token,
                TaskAttempt.status.in_(("leased", "running")),
            )
            .values(status="awaiting_stop_verification", updated_at=text("now()"))
            .returning(TaskAttempt)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise LeaseConflictError(f"cannot mark attempt {attempt_id} awaiting stop")
        return row
