"""Instance lifecycle vs activity — pure mapping (D09.1).

A single flat status must not answer both "does a session exist?" and
"is work in progress?". Legacy AgentInstanceStatus values remain readable
via explicit maps; they are not interchangeable with gate release.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentforge.contracts.agents import (
    AgentInstanceStatus,
    InstanceActivityStatus,
    InstanceLifecycleStatus,
)

# Flat legacy → (lifecycle, activity). Activity defaults to idle when unknown.
LEGACY_INSTANCE_STATUS_MAP: dict[str, tuple[InstanceLifecycleStatus, InstanceActivityStatus]] = {
    "created": ("created", "idle"),
    "starting": ("starting", "idle"),
    "running": ("active", "busy"),
    "waiting_input": ("active", "waiting_input"),
    "waiting_approval": ("active", "waiting_approval"),
    "stop_requested": ("stop_requested", "busy"),
    "stopped": ("stopped", "idle"),
    "unknown": ("unknown", "idle"),
    "reconciling": ("needs_reconciliation", "idle"),
    "blocked": ("active", "idle"),
    "failed": ("failed", "idle"),
    "closed": ("closed", "idle"),
}


@dataclass(frozen=True)
class InstanceStatusFacets:
    lifecycle: InstanceLifecycleStatus
    activity: InstanceActivityStatus


def split_legacy_instance_status(status: AgentInstanceStatus | str) -> InstanceStatusFacets:
    mapped = LEGACY_INSTANCE_STATUS_MAP.get(status)
    if mapped is None:
        raise ValueError(f"Unsupported legacy instance status: {status}")
    lifecycle, activity = mapped
    return InstanceStatusFacets(lifecycle=lifecycle, activity=activity)


def combine_instance_status_facets(
    lifecycle: InstanceLifecycleStatus,
    activity: InstanceActivityStatus,
) -> AgentInstanceStatus:
    """Best-effort reverse map for migration readers. Not a source of truth."""
    if lifecycle == "needs_reconciliation":
        return "reconciling"
    if lifecycle in ("created", "starting", "stop_requested", "stopped", "unknown", "failed", "closed"):
        return lifecycle  # type: ignore[return-value]
    # lifecycle == active
    if activity == "busy":
        return "running"
    if activity == "waiting_input":
        return "waiting_input"
    if activity == "waiting_approval":
        return "waiting_approval"
    return "running"


def activity_may_accept_work(activity: InstanceActivityStatus) -> bool:
    """idle means currently able to receive the next allowed work item."""
    return activity == "idle"


def lifecycle_implies_active_session(lifecycle: InstanceLifecycleStatus) -> bool:
    """Session may exist; does not imply busy or stage release."""
    return lifecycle in ("starting", "active", "stop_requested", "needs_reconciliation")
