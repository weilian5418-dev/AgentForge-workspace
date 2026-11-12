"""D11.8: graph routing, handoff, planner submitted→wait."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentforge.contracts.collaboration import LeaseFence
from agentforge.driver.graph import ACTION_TO_NODE, build_collaboration_graph, route_action
from agentforge.driver.nodes import prepare_handoff
from agentforge.driver.planner import choose_next_action
from agentforge.driver.snapshots import AttemptSnap, CollaborationSnapshot, WorkPackageSnap
from agentforge.driver.state import CollaborationState


def test_route_action_covers_all_planner_kinds() -> None:
    for kind in ACTION_TO_NODE:
        assert route_action({"next_action": kind}) == ACTION_TO_NODE[kind]
    with pytest.raises(ValueError, match="unknown"):
        route_action({"next_action": "not_a_real_action"})


def test_submitted_attempt_waits_for_stop_not_resubmit() -> None:
    snap = CollaborationSnapshot(
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
        work_packages=(WorkPackageSnap("wp_a", "stage_a", "role_dev"),),
        attempts=(
            AttemptSnap("ia_1", "wp_a", "inst_1", "submitted", "stage_a"),
        ),
        snapshot_hash="a" * 64,
    )
    action = choose_next_action(snap)
    assert action.kind == "register_wait"
    assert action.wait_reason == "stop_verification"


def test_build_graph_compiles_with_memory_checkpointer() -> None:
    graph = build_collaboration_graph(checkpointer=InMemorySaver())
    assert graph is not None
    # Structural smoke: nodes present
    assert "refresh" in graph.nodes
    assert "decide" in graph.nodes
    assert "await_condition" in graph.nodes
    assert "prepare_handoff" in graph.nodes


def test_prepare_handoff_sets_collaboration_ready() -> None:
    session = MagicMock()
    run = MagicMock(
        id="run_1",
        plan_version=1,
        assignment_version=1,
        gate_policy_version=1,
        active_generation=1,
        collaboration_state="running",
        collaboration_ready_at=None,
    )
    session.get.return_value = run
    fence = LeaseFence(
        run_id="run_1", owner="o", token="t", lease_generation=1, thread_id="afg:1"
    )

    class _Snap:
        work_packages = (WorkPackageSnap("wp_a", "s", "r", True),)
        releases = (MagicMock(release_id="rel_1"),)

        @staticmethod
        def released_work_packages():
            return frozenset({"wp_a"})

    with (
        patch("agentforge.driver.nodes.assert_driver_fence"),
        patch("agentforge.driver.nodes.SnapshotReader") as reader_cls,
        patch("agentforge.driver.nodes.DriverRepository") as repo_cls,
        patch("agentforge.driver.nodes.append_fact"),
        patch("agentforge.driver.nodes.db_now", side_effect=Exception("no sql")),
    ):
        reader_cls.return_value.load.return_value = _Snap()
        repo_cls.return_value.find_operation.return_value = None
        result = prepare_handoff(session, fence)

    assert result["status"] == "succeeded"
    assert result["handoffRef"]
    assert run.collaboration_state == "ready_for_integration"
    assert session.add.called or repo_cls.return_value.add_operation.called


def test_action_to_node_has_no_silent_end_default() -> None:
    # Unknown kinds must raise in route_action — never fall through to END.
    state: CollaborationState = {"next_action": "dispatch_ready"}
    assert route_action(state) == "dispatch_ready"
