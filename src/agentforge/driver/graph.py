"""Compile the single-run collaboration StateGraph (D11.8)."""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from agentforge.driver import nodes as N
from agentforge.driver.state import CollaborationState, DriverContext

ACTION_TO_NODE: dict[str, str] = {
    "register_wait": "register_wait",
    "ensure_workspaces": "ensure_workspaces",
    "ensure_instances": "ensure_instances",
    "dispatch_ready": "dispatch_ready",
    "accept_submissions": "accept_submissions",
    "prepare_rework": "prepare_rework",
    "coordinate_stop": "coordinate_stop",
    "open_recovery": "open_recovery",
    "prepare_handoff": "prepare_handoff",
    "refresh": "refresh",
    "end": "end",
}


def route_action(state: CollaborationState) -> str:
    kind = state.get("next_action")
    if not kind:
        raise ValueError("decide_next_action did not set next_action")
    node = ACTION_TO_NODE.get(str(kind))
    if node is None:
        raise ValueError(f"unknown next_action kind: {kind}")
    return node


def build_collaboration_graph(*, checkpointer: Any = None) -> Any:
    """Build and optionally compile the collaboration graph."""
    builder: StateGraph[
        CollaborationState, DriverContext, CollaborationState, CollaborationState
    ] = StateGraph(CollaborationState, context_schema=DriverContext)
    builder.add_node("refresh", N.refresh_snapshot)
    builder.add_node("decide", N.decide_next_action)
    builder.add_node("ensure_workspaces", N.node_ensure_workspaces)
    builder.add_node("ensure_instances", N.node_ensure_instances)
    builder.add_node("dispatch_ready", N.node_dispatch_ready)
    builder.add_node("accept_submissions", N.node_accept_submissions)
    builder.add_node("register_wait", N.node_register_wait)
    builder.add_node("await_condition", N.await_condition)
    builder.add_node("prepare_handoff", N.node_prepare_handoff)
    builder.add_node("open_recovery", N.node_open_recovery)
    builder.add_node("prepare_rework", N.node_prepare_rework)
    builder.add_node("coordinate_stop", N.node_coordinate_stop)
    builder.add_node("end", N.node_end)

    builder.add_edge(START, "refresh")
    builder.add_edge("refresh", "decide")
    builder.add_conditional_edges("decide", route_action, list(ACTION_TO_NODE.values()))

    # Short domain intents re-read facts; waits interrupt; terminal nodes end.
    for name in (
        "ensure_workspaces",
        "ensure_instances",
        "dispatch_ready",
        "accept_submissions",
    ):
        builder.add_edge(name, "refresh")

    def _to_await_or_refresh(state: CollaborationState) -> str:
        return "await_condition" if state.get("wait_ref") else "refresh"

    for name in ("register_wait", "prepare_rework", "coordinate_stop", "open_recovery"):
        builder.add_conditional_edges(
            name,
            _to_await_or_refresh,
            {"await_condition": "await_condition", "refresh": "refresh"},
        )

    builder.add_edge("await_condition", "refresh")
    builder.add_edge("prepare_handoff", "end")
    builder.add_edge("end", END)

    if checkpointer is None:
        return builder.compile()
    return builder.compile(checkpointer=checkpointer)


def route_action_name(
    kind: str,
) -> Literal[
    "register_wait",
    "ensure_workspaces",
    "ensure_instances",
    "dispatch_ready",
    "accept_submissions",
    "prepare_rework",
    "coordinate_stop",
    "open_recovery",
    "prepare_handoff",
    "refresh",
    "end",
]:
    node = ACTION_TO_NODE.get(kind)
    if node is None:
        raise ValueError(f"unknown next_action kind: {kind}")
    return node  # type: ignore[return-value]
