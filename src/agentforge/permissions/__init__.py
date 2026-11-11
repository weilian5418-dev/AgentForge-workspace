"""Native approval mapping and PermissionBridge (BE-06 / D04)."""

from agentforge.permissions.bridge import BridgeResult, map_user_decision
from agentforge.permissions.digest import compute_action_digest
from agentforge.permissions.service import (
    ApprovalError,
    confirm_native,
    decide,
    get_approval,
    is_effective_grant,
    list_approvals,
    record_native_request,
    retry_forward,
)

__all__ = [
    "ApprovalError",
    "BridgeResult",
    "compute_action_digest",
    "confirm_native",
    "decide",
    "get_approval",
    "is_effective_grant",
    "list_approvals",
    "map_user_decision",
    "record_native_request",
    "retry_forward",
]
