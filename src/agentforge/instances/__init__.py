"""Role assignment and AgentInstance binding (BE-26 / D09)."""

from agentforge.instances.status import (
    LEGACY_INSTANCE_STATUS_MAP,
    InstanceStatusFacets,
    activity_may_accept_work,
    combine_instance_status_facets,
    lifecycle_implies_active_session,
    split_legacy_instance_status,
)

__all__ = [
    "LEGACY_INSTANCE_STATUS_MAP",
    "InstanceStatusFacets",
    "activity_may_accept_work",
    "combine_instance_status_facets",
    "lifecycle_implies_active_session",
    "split_legacy_instance_status",
]
