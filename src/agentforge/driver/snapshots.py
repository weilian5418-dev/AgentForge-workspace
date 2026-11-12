"""Bounded collaboration snapshots — references and versions only, not authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.collaboration import ACTIVE_INSTANCE_ATTEMPT_STATUSES, LeaseFence
from agentforge.driver.binding import sha256_hex
from agentforge.driver.fencing import assert_driver_fence
from agentforge.storage.models import (
    AgentInstanceRow,
    DriverThreadRow,
    InstanceAttemptRow,
    Plan,
    PlanConfirmationRequestRow,
    ReleaseRecordRow,
    Run,
    RunWaitRow,
    StageRow,
)


@dataclass(frozen=True)
class WorkPackageSnap:
    work_package_id: str
    stage_id: str | None
    role_id: str | None
    required: bool = True


@dataclass(frozen=True)
class StageSnap:
    stage_id: str
    status: str
    waiting_reason: str
    write_access_closed: bool
    required_work_packages: tuple[str, ...] = ()


@dataclass(frozen=True)
class AttemptSnap:
    attempt_id: str
    work_package_id: str
    instance_id: str
    status: str
    stage_id: str | None = None


@dataclass(frozen=True)
class InstanceSnap:
    instance_id: str
    role_id: str
    lifecycle: str
    activity: str
    workspace_id: str | None
    native_session_id: str | None


@dataclass(frozen=True)
class ReleaseSnap:
    release_id: str
    stage_id: str
    work_package_ids: tuple[str, ...]


@dataclass(frozen=True)
class WaitSnap:
    wait_id: str
    wait_key: str
    reason: str
    status: str


@dataclass(frozen=True)
class CollaborationSnapshot:
    """Immutable fact view for choose_next_action — never grant permission by itself."""

    run_id: str
    run_status: str
    collaboration_state: str
    status_version: int
    plan_version: int | None
    assignment_version: int | None
    gate_policy_version: int | None
    active_generation: int
    last_sequence: int
    plan_confirmed: bool
    stages: tuple[StageSnap, ...] = ()
    work_packages: tuple[WorkPackageSnap, ...] = ()
    attempts: tuple[AttemptSnap, ...] = ()
    instances: tuple[InstanceSnap, ...] = ()
    releases: tuple[ReleaseSnap, ...] = ()
    open_waits: tuple[WaitSnap, ...] = ()
    active_thread_id: str | None = None
    snapshot_hash: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def active_attempt_for_wp(self, work_package_id: str) -> AttemptSnap | None:
        for a in self.attempts:
            if a.work_package_id == work_package_id and a.status in ACTIVE_INSTANCE_ATTEMPT_STATUSES:
                return a
        return None

    def released_work_packages(self) -> frozenset[str]:
        ids: set[str] = set()
        for r in self.releases:
            ids.update(r.work_package_ids)
        return frozenset(ids)

    def instance_busy(self, instance_id: str) -> bool:
        for a in self.attempts:
            if a.instance_id == instance_id and a.status in ACTIVE_INSTANCE_ATTEMPT_STATUSES:
                return True
        return False


def _hash_snapshot(parts: dict[str, Any]) -> str:
    return sha256_hex(parts)


class SnapshotReader:
    def __init__(self, session: Session) -> None:
        self.session = session

    def load(
        self,
        run_id: str,
        fence: LeaseFence | None = None,
        *,
        require_fence: bool = False,
    ) -> CollaborationSnapshot:
        if fence is not None:
            assert_driver_fence(
                self.session, fence, require_active_thread=fence.thread_id is not None
            )
        elif require_fence:
            raise ValueError("fence required")

        # Prefer a short consistent read; callers wrap with UOW / isolation as needed.
        run = self.session.get(Run, run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")

        plan_confirmed = False
        if run.plan_version is not None:
            plan = self.session.scalar(
                select(Plan).where(Plan.run_id == run_id, Plan.version == run.plan_version)
            )
            pending = self.session.scalar(
                select(PlanConfirmationRequestRow).where(
                    PlanConfirmationRequestRow.run_id == run_id,
                    PlanConfirmationRequestRow.plan_version == run.plan_version,
                    PlanConfirmationRequestRow.status == "pending",
                )
            )
            plan_confirmed = plan is not None and plan.status == "approved" and pending is None

        stages = tuple(
            StageSnap(
                stage_id=s.id,
                status=s.status,
                waiting_reason=s.waiting_reason or "none",
                write_access_closed=bool(s.write_access_closed),
                required_work_packages=tuple(s.required_work_packages or []),
            )
            for s in self.session.scalars(select(StageRow).where(StageRow.run_id == run_id)).all()
        )

        work_packages: list[WorkPackageSnap] = []
        if run.plan_version is not None:
            plan = self.session.scalar(
                select(Plan).where(Plan.run_id == run_id, Plan.version == run.plan_version)
            )
            spec = (plan.spec_json or {}) if plan is not None else {}
            for wp in spec.get("workPackages") or spec.get("work_packages") or []:
                if not isinstance(wp, dict):
                    continue
                work_packages.append(
                    WorkPackageSnap(
                        work_package_id=str(
                            wp.get("workPackageId") or wp.get("id") or wp.get("work_package_id")
                        ),
                        stage_id=(
                            str(wp["stageId"])
                            if wp.get("stageId")
                            else str(wp["stage_id"])
                            if wp.get("stage_id")
                            else None
                        ),
                        role_id=(
                            str(wp["roleId"])
                            if wp.get("roleId")
                            else str(wp["role_id"])
                            if wp.get("role_id")
                            else None
                        ),
                        required=bool(wp.get("required", True)),
                    )
                )

        attempts = tuple(
            AttemptSnap(
                attempt_id=a.id,
                work_package_id=a.work_package_id,
                instance_id=a.instance_id,
                status=a.status,
                stage_id=a.stage_id,
            )
            for a in self.session.scalars(
                select(InstanceAttemptRow).where(InstanceAttemptRow.run_id == run_id)
            ).all()
        )

        instances = tuple(
            InstanceSnap(
                instance_id=i.id,
                role_id=i.role_id,
                lifecycle=i.lifecycle,
                activity=i.activity,
                workspace_id=i.workspace_id,
                native_session_id=i.native_session_id,
            )
            for i in self.session.scalars(
                select(AgentInstanceRow).where(AgentInstanceRow.run_id == run_id)
            ).all()
        )

        releases = tuple(
            ReleaseSnap(
                release_id=r.id,
                stage_id=r.stage_id,
                work_package_ids=tuple(r.work_package_ids or []),
            )
            for r in self.session.scalars(
                select(ReleaseRecordRow).where(ReleaseRecordRow.stage_id.in_([s.stage_id for s in stages] or ["__none__"]))
            ).all()
        ) if stages else ()

        open_waits = tuple(
            WaitSnap(
                wait_id=w.id,
                wait_key=w.wait_key,
                reason=w.reason,
                status=w.status,
            )
            for w in self.session.scalars(
                select(RunWaitRow).where(
                    RunWaitRow.run_id == run_id, RunWaitRow.status == "open"
                )
            ).all()
        )

        thread = self.session.scalar(
            select(DriverThreadRow).where(
                DriverThreadRow.run_id == run_id, DriverThreadRow.status == "active"
            )
        )

        base = {
            "runId": run.id,
            "status": run.status,
            "collaborationState": run.collaboration_state,
            "statusVersion": run.status_version,
            "planVersion": run.plan_version,
            "assignmentVersion": run.assignment_version,
            "gatePolicyVersion": run.gate_policy_version,
            "activeGeneration": run.active_generation,
            "lastSequence": run.last_sequence,
            "planConfirmed": plan_confirmed,
            "stages": [s.stage_id + ":" + s.status for s in stages],
            "attempts": [a.attempt_id + ":" + a.status for a in attempts],
            "releases": [r.release_id for r in releases],
            "waits": [w.wait_id for w in open_waits],
        }
        return CollaborationSnapshot(
            run_id=run.id,
            run_status=run.status,
            collaboration_state=run.collaboration_state or "idle",
            status_version=int(run.status_version),
            plan_version=run.plan_version,
            assignment_version=run.assignment_version,
            gate_policy_version=run.gate_policy_version,
            active_generation=int(run.active_generation),
            last_sequence=int(run.last_sequence or 0),
            plan_confirmed=plan_confirmed,
            stages=stages,
            work_packages=tuple(work_packages),
            attempts=attempts,
            instances=instances,
            releases=releases,
            open_waits=open_waits,
            active_thread_id=thread.thread_id if thread else None,
            snapshot_hash=_hash_snapshot(base),
        )
