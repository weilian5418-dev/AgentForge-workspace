from __future__ import annotations

from sqlalchemy import select, update

from agentforge.storage.exceptions import VersionConflictError
from agentforge.storage.models import Run
from agentforge.storage.uow import UnitOfWork


class RunRepository:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def get(self, run_id: str, *, for_update: bool = False) -> Run | None:
        stmt = select(Run).where(Run.id == run_id)
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        row = self._uow.session.execute(stmt).scalar_one_or_none()
        if for_update and row is not None:
            self._uow.session.refresh(row)
        return row

    def add(self, run: Run) -> Run:
        self._uow.session.add(run)
        return run

    def update_status(
        self,
        *,
        run_id: str,
        expected_status_version: int,
        new_status: str,
        extra: dict[str, object] | None = None,
    ) -> Run:
        values: dict[str, object] = {
            "status": new_status,
            "status_version": expected_status_version + 1,
        }
        if extra:
            values.update(extra)
        result = self._uow.session.execute(
            update(Run)
            .where(Run.id == run_id, Run.status_version == expected_status_version)
            .values(**values)
            .returning(Run)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise VersionConflictError(
                f"run {run_id} status_version {expected_status_version} conflict"
            )
        return row

    def bump_active_generation(self, run_id: str, expected_generation: int) -> int:
        """Invalidate prior attempts; lock Run then CAS on DB generation (not cached ORM)."""
        session = self._uow.session
        # Lock for ordering with attempt completion; do not trust cached attributes.
        locked_id = session.execute(
            select(Run.id).where(Run.id == run_id).with_for_update()
        ).scalar_one_or_none()
        if locked_id is None:
            raise VersionConflictError(f"run {run_id} active_generation conflict")
        result = session.execute(
            update(Run)
            .where(Run.id == run_id, Run.active_generation == expected_generation)
            .values(active_generation=expected_generation + 1)
            .returning(Run.active_generation)
        )
        value = result.scalar_one_or_none()
        if value is None:
            raise VersionConflictError(f"run {run_id} active_generation conflict")
        cached = session.get(Run, run_id)
        if cached is not None:
            session.refresh(cached)
        return int(value)
