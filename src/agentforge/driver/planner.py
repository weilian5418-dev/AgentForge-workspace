"""Pure next-action policy — no I/O, adapters, or gate bypass."""

from __future__ import annotations

from agentforge.contracts.collaboration import ACTIVE_INSTANCE_ATTEMPT_STATUSES, NextAction
from agentforge.driver.snapshots import CollaborationSnapshot


def choose_next_action(snapshot: CollaborationSnapshot) -> NextAction:
    """Deterministic scheduler. Same snapshot → same action."""

    # 1) Control / safety isolation first.
    if snapshot.run_status in ("cancel_requested", "pause_requested"):
        return NextAction(
            kind="coordinate_stop",
            detail=f"run_status={snapshot.run_status}",
        )
    if snapshot.run_status in ("canceled", "paused"):
        return NextAction(
            kind="register_wait",
            wait_reason="recovery_evidence",
            wait_key=f"control:{snapshot.run_status}:{snapshot.status_version}",
            detail="await control transition",
        )
    if snapshot.run_status == "blocked":
        return NextAction(
            kind="open_recovery",
            recovery_reason="run_blocked",
            detail="run is blocked",
        )

    # 2) Unknown attempts occupy write slots — never re-dispatch the same WP.
    for attempt in snapshot.attempts:
        if attempt.status == "unknown":
            return NextAction(
                kind="open_recovery",
                work_package_id=attempt.work_package_id,
                recovery_reason="unknown_attempt",
                detail=f"attempt {attempt.attempt_id} unknown",
            )
        if attempt.status == "awaiting_stop_verification":
            return NextAction(
                kind="register_wait",
                stage_id=attempt.stage_id,
                work_package_id=attempt.work_package_id,
                wait_reason="stop_verification",
                wait_key=f"stop:{attempt.attempt_id}",
                detail="await stop proof",
            )
        if attempt.status == "submitted":
            return NextAction(
                kind="accept_submissions",
                stage_id=attempt.stage_id,
                work_package_id=attempt.work_package_id,
                detail=f"submission pending for {attempt.attempt_id}",
            )

    # 3) Stage review / rework in progress.
    for stage in snapshot.stages:
        if stage.status == "rework":
            return NextAction(
                kind="prepare_rework",
                stage_id=stage.stage_id,
                detail="stage rework accepted path",
            )
        if stage.status in ("reviewing", "frozen", "stopping", "submission_requested"):
            reason = {
                "reviewing": "stage_review",
                "frozen": "stage_review",
                "stopping": "stop_verification",
                "submission_requested": "stop_verification",
            }[stage.status]
            return NextAction(
                kind="register_wait",
                stage_id=stage.stage_id,
                wait_reason=reason,  # type: ignore[arg-type]
                wait_key=f"stage:{stage.stage_id}:{stage.status}",
                detail=stage.waiting_reason,
            )

    # 4) Plan not confirmed — wait only; graph must not approve.
    if not snapshot.plan_confirmed:
        return NextAction(
            kind="register_wait",
            wait_reason="plan_confirmation",
            wait_key="plan_confirmation",
            detail="plan not confirmed",
        )

    # 5) Integration handoff when all required packages released.
    required_ids = [
        wp.work_package_id for wp in snapshot.work_packages if wp.required
    ]
    released = snapshot.released_work_packages()
    if required_ids and all(wp_id in released for wp_id in required_ids):
        if snapshot.collaboration_state != "ready_for_integration":
            return NextAction(kind="prepare_handoff", detail="all required released")
        return NextAction(kind="end", detail="ready_for_integration")

    # 6) Missing instances / workspaces before dispatch.
    if snapshot.work_packages and not snapshot.instances:
        return NextAction(kind="ensure_instances", detail="no instances yet")
    for inst in snapshot.instances:
        if not inst.workspace_id:
            return NextAction(
                kind="ensure_workspaces",
                detail=f"instance {inst.instance_id} missing workspace",
            )
        if not inst.native_session_id and inst.lifecycle in ("created", "starting"):
            return NextAction(
                kind="register_wait",
                wait_reason="session_ready",
                wait_key=f"session:{inst.instance_id}",
                detail="await session ready",
            )

    # 7) Dispatch first ready work package without active/unknown slot.
    for wp in snapshot.work_packages:
        if wp.work_package_id in released:
            continue
        if snapshot.active_attempt_for_wp(wp.work_package_id) is not None:
            continue
        # Upstream gate: if stage listed and not ready/executing/released, wait.
        if wp.stage_id:
            stage = next((s for s in snapshot.stages if s.stage_id == wp.stage_id), None)
            if stage is not None and stage.status in ("pending", "blocked"):
                return NextAction(
                    kind="register_wait",
                    stage_id=wp.stage_id,
                    work_package_id=wp.work_package_id,
                    wait_reason="dependency",
                    wait_key=f"dep:{wp.stage_id}",
                    detail=f"stage {stage.status}",
                )
            if stage is not None and stage.write_access_closed and stage.status == "executing":
                # Write closed without submission path — wait rather than force.
                continue
        # Capacity: find idle instance for role if specified.
        if wp.role_id:
            candidates = [
                i
                for i in snapshot.instances
                if i.role_id == wp.role_id and i.lifecycle == "active"
            ]
            if not candidates:
                return NextAction(
                    kind="ensure_instances",
                    work_package_id=wp.work_package_id,
                    detail=f"no active instance for role {wp.role_id}",
                )
            if all(snapshot.instance_busy(i.instance_id) for i in candidates):
                return NextAction(
                    kind="register_wait",
                    work_package_id=wp.work_package_id,
                    wait_reason="capacity",
                    wait_key=f"capacity:{wp.role_id}",
                    detail="role busy",
                )
        return NextAction(
            kind="dispatch_ready",
            stage_id=wp.stage_id,
            work_package_id=wp.work_package_id,
            operation_key=f"assign:{snapshot.run_id}:{wp.work_package_id}",
            detail="ready to reserve attempt",
        )

    # 8) Nothing actionable — wait / yield.
    if snapshot.open_waits:
        w = snapshot.open_waits[0]
        return NextAction(
            kind="register_wait",
            wait_reason=w.reason,  # type: ignore[arg-type]
            wait_key=w.wait_key,
            detail="existing open wait",
        )
    return NextAction(
        kind="register_wait",
        wait_reason="driver_yield",
        wait_key=f"yield:{snapshot.last_sequence}",
        detail="no legal action",
    )


def assert_no_gate_bypass(action: NextAction) -> None:
    """Planner never invents release / stop_verified."""
    if action.kind in ("prepare_handoff", "end"):
        return
    if action.detail and "force" in action.detail.lower():
        raise AssertionError("planner must not force-pass gates")
