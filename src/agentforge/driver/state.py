"""LangGraph collaboration state and runtime context (not domain authority)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from sqlalchemy.engine import Engine

from agentforge.contracts.collaboration import LeaseFence, NextAction
from agentforge.core.config import Settings
from agentforge.driver.snapshots import CollaborationSnapshot


class CollaborationState(TypedDict, total=False):
    """Checkpointed coordination hints — never stores lease tokens or full payloads."""

    run_id: str
    thread_id: str | None
    snapshot_hash: str | None
    observed_sequence: int
    collaboration_state: str | None
    next_action: str | None
    action_stage_id: str | None
    action_work_package_id: str | None
    action_wait_reason: str | None
    action_wait_key: str | None
    action_operation_key: str | None
    action_recovery_reason: str | None
    action_detail: str | None
    wait_ref: str | None
    operation_ref: str | None
    handoff_ref: str | None
    recovery_case_ref: str | None
    steps_remaining: int
    last_error: str | None
    fragment_outcome: str | None


@dataclass
class DriverContext:
    """Runtime-only fencing and I/O handles — never written into checkpoints."""

    engine: Engine
    settings: Settings
    fence: LeaseFence
    state_dir: Path
    latest_snapshot: CollaborationSnapshot | None = None
    last_action: NextAction | None = None
    claimed_wake_ids: list[str] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)


def action_from_state(state: CollaborationState) -> NextAction:
    kind = state.get("next_action") or "refresh"
    return NextAction.model_validate(
        {
            "kind": kind,
            "stageId": state.get("action_stage_id"),
            "workPackageId": state.get("action_work_package_id"),
            "waitReason": state.get("action_wait_reason"),
            "waitKey": state.get("action_wait_key"),
            "operationKey": state.get("action_operation_key"),
            "recoveryReason": state.get("action_recovery_reason"),
            "detail": state.get("action_detail"),
        }
    )


def state_from_action(action: NextAction) -> dict[str, Any]:
    return {
        "next_action": action.kind,
        "action_stage_id": action.stage_id,
        "action_work_package_id": action.work_package_id,
        "action_wait_reason": action.wait_reason,
        "action_wait_key": action.wait_key,
        "action_operation_key": action.operation_key,
        "action_recovery_reason": action.recovery_reason,
        "action_detail": action.detail,
    }
