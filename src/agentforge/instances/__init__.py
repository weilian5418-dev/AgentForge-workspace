"""Role assignment and AgentInstance binding (BE-26 / D09)."""

from agentforge.instances.actions import InstanceActionService
from agentforge.instances.context_loader import ExecutionContextLoader
from agentforge.instances.eligibility import EligibilityService
from agentforge.instances.errors import InstanceError
from agentforge.instances.event_ingestor import NativeEventIngestor
from agentforge.instances.preparation import ExecutionPreparationService
from agentforge.instances.repository import InstanceRepository
from agentforge.instances.session_coordinator import SessionCoordinator
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
    "EligibilityService",
    "ExecutionContextLoader",
    "ExecutionPreparationService",
    "InstanceActionService",
    "InstanceError",
    "InstanceRepository",
    "InstanceStatusFacets",
    "NativeEventIngestor",
    "SessionCoordinator",
    "activity_may_accept_work",
    "combine_instance_status_facets",
    "lifecycle_implies_active_session",
    "split_legacy_instance_status",
]
