"""Native approval mapping and PermissionBridge (BE-06 / D04)."""

from agentforge.permissions.bridge import BridgeResult, map_user_decision, scope_fits
from agentforge.permissions.digest import compute_action_digest
from agentforge.permissions.service import (
    ApprovalError,
    assert_action_executable,
    confirm_native,
    decide,
    get_approval,
    grant_record_looks_valid,
    is_effective_grant,
    list_approvals,
    mark_forward_reconciled,
    record_native_request,
    retry_forward,
    revoke_grant,
    try_consume_once,
)

__all__ = [
    "ApprovalError",
    "BridgeResult",
    "assert_action_executable",
    "compute_action_digest",
    "confirm_native",
    "decide",
    "get_approval",
    "grant_record_looks_valid",
    "is_effective_grant",
    "list_approvals",
    "map_user_decision",
    "mark_forward_reconciled",
    "record_native_request",
    "retry_forward",
    "revoke_grant",
    "scope_fits",
    "try_consume_once",
]
