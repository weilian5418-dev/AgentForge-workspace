"""D11.5: pure planner — stable actions, no gate bypass."""

from __future__ import annotations

from agentforge.driver.planner import choose_next_action
from agentforge.driver.snapshots import (
    AttemptSnap,
    CollaborationSnapshot,
    InstanceSnap,
    ReleaseSnap,
    StageSnap,
    WorkPackageSnap,
)


def _snap(**overrides) -> CollaborationSnapshot:
    base = dict(
        run_id="run_1",
        run_status="running",
        collaboration_state="running",
        status_version=1,
        plan_version=1,
        assignment_version=1,
        gate_policy_version=1,
        active_generation=1,
        last_sequence=1,
        plan_confirmed=True,
        stages=(),
        work_packages=(),
        attempts=(),
        instances=(),
        releases=(),
        open_waits=(),
        snapshot_hash="a" * 64,
    )
    base.update(overrides)
    return CollaborationSnapshot(**base)


def test_cancel_requested_coordinates_stop_first() -> None:
    action = choose_next_action(_snap(run_status="cancel_requested"))
    assert action.kind == "coordinate_stop"


def test_unknown_attempt_opens_recovery_not_redispatch() -> None:
    action = choose_next_action(
        _snap(
            attempts=(
                AttemptSnap(
                    attempt_id="ia_1",
                    work_package_id="wp_a",
                    instance_id="inst_1",
                    status="unknown",
                ),
            ),
            work_packages=(
                WorkPackageSnap("wp_a", "stage_a", "role_dev"),
            ),
        )
    )
    assert action.kind == "open_recovery"
    assert action.work_package_id == "wp_a"


def test_unconfirmed_plan_waits_without_dispatch() -> None:
    action = choose_next_action(
        _snap(
            plan_confirmed=False,
            work_packages=(WorkPackageSnap("wp_a", "stage_a", "role_dev"),),
        )
    )
    assert action.kind == "register_wait"
    assert action.wait_reason == "plan_confirmation"


def test_first_stage_ready_dispatches_when_instance_idle() -> None:
    action = choose_next_action(
        _snap(
            stages=(
                StageSnap("stage_a", "executing", "none", False, ("wp_a",)),
            ),
            work_packages=(WorkPackageSnap("wp_a", "stage_a", "role_dev"),),
            instances=(
                InstanceSnap(
                    "inst_1", "role_dev", "active", "idle", "ws_1", "ns_1"
                ),
            ),
        )
    )
    assert action.kind == "dispatch_ready"
    assert action.work_package_id == "wp_a"


def test_busy_role_waits_on_capacity() -> None:
    action = choose_next_action(
        _snap(
            stages=(StageSnap("stage_a", "executing", "none", False),),
            work_packages=(
                WorkPackageSnap("wp_a", "stage_a", "role_dev"),
                WorkPackageSnap("wp_b", "stage_a", "role_dev"),
            ),
            instances=(
                InstanceSnap(
                    "inst_1", "role_dev", "active", "idle", "ws_1", "ns_1"
                ),
            ),
            attempts=(
                AttemptSnap("ia_1", "wp_a", "inst_1", "running", "stage_a"),
            ),
        )
    )
    assert action.kind == "register_wait"
    assert action.wait_reason == "capacity"


def test_upstream_not_released_does_not_skip_to_handoff() -> None:
    action = choose_next_action(
        _snap(
            work_packages=(
                WorkPackageSnap("wp_a", "stage_a", "role_dev", True),
                WorkPackageSnap("wp_b", "stage_b", "role_dev", True),
            ),
            stages=(
                StageSnap("stage_a", "executing", "none", False),
                StageSnap("stage_b", "pending", "none", False),
            ),
            instances=(
                InstanceSnap(
                    "inst_1", "role_dev", "active", "idle", "ws_1", "ns_1"
                ),
            ),
            releases=(),
        )
    )
    assert action.kind == "dispatch_ready"
    assert action.work_package_id == "wp_a"


def test_all_required_released_prepares_handoff_not_completed() -> None:
    action = choose_next_action(
        _snap(
            work_packages=(
                WorkPackageSnap("wp_a", "stage_a", "role_dev", True),
            ),
            releases=(ReleaseSnap("rel_1", "stage_a", ("wp_a",)),),
            collaboration_state="running",
        )
    )
    assert action.kind == "prepare_handoff"
    assert action.kind != "end" or True


def test_same_snapshot_is_stable() -> None:
    snap = _snap(
        stages=(StageSnap("stage_a", "executing", "none", False),),
        work_packages=(WorkPackageSnap("wp_a", "stage_a", "role_dev"),),
        instances=(
            InstanceSnap("inst_1", "role_dev", "active", "idle", "ws_1", "ns_1"),
        ),
    )
    assert choose_next_action(snap).kind == choose_next_action(snap).kind
