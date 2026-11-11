"""PermissionBridge — map user decisions to native options without expanding scope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

BridgeOutcome = Literal["mapped", "mapping_unsupported", "manual_required"]


@dataclass(frozen=True)
class BridgeResult:
    outcome: BridgeOutcome
    native_decision: Literal["allow", "deny"] | None
    detail: str
    mapped_options: dict[str, Any] | None = None


def map_user_decision(
    *,
    user_decision: Literal["approve", "reject"],
    semantic: str,
    native_scope: str,
    platform_scope: str,
    native_options: list[str] | None = None,
) -> BridgeResult:
    """Map approve/reject onto product-native allow/deny.

    A once-scoped platform allow must not become session-wide native allow.
    If native options cannot be expressed precisely → mapping_unsupported.
    """
    if semantic == "session" and user_decision == "approve":
        # Explicit session grants require dedicated evidence; refuse silent expansion.
        if native_scope != platform_scope:
            return BridgeResult(
                outcome="mapping_unsupported",
                native_decision=None,
                detail="session approve requires exact native_scope == platform_scope",
            )

    options = {o.lower() for o in (native_options or ["allow", "deny", "approve", "reject"])}
    if user_decision == "approve":
        if not ({"allow", "approve"} & options):
            return BridgeResult(
                outcome="mapping_unsupported",
                native_decision=None,
                detail="native options lack allow/approve",
            )
        if native_scope and platform_scope and not _scope_fits(native_scope, platform_scope):
            return BridgeResult(
                outcome="manual_required",
                native_decision=None,
                detail="native_scope exceeds platform_scope; refuse prompt-based safety",
            )
        return BridgeResult(
            outcome="mapped",
            native_decision="allow",
            detail="mapped approve→allow",
            mapped_options={"nativeDecision": "allow", "semantic": semantic},
        )

    if not ({"deny", "reject"} & options):
        return BridgeResult(
            outcome="mapping_unsupported",
            native_decision=None,
            detail="native options lack deny/reject",
        )
    return BridgeResult(
        outcome="mapped",
        native_decision="deny",
        detail="mapped reject→deny",
        mapped_options={"nativeDecision": "deny", "semantic": semantic},
    )


def _scope_fits(native_scope: str, platform_scope: str) -> bool:
    """Native requested scope must be within platform-granted scope (string containment)."""
    if native_scope == platform_scope:
        return True
    # Prefix form: platform "repo:a/" covers native "repo:a/src"
    if platform_scope.endswith("/") and native_scope.startswith(platform_scope):
        return True
    return False
