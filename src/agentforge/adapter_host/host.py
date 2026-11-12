"""Adapter Host accepts only registered profile/instance/workspace/operation tickets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentforge.runtime.execution_context import (
    ExecutionContext,
    ExecutionContextError,
    assert_context_complete,
)


@dataclass
class AdapterHost:
    """Session operation boundary. Must not become an arbitrary host shell."""

    registered_profile_ids: set[str] = field(default_factory=set)
    health: str = "not_configured"

    def register_profile(self, profile_id: str) -> None:
        self.registered_profile_ids.add(profile_id)
        self.health = "ready"

    def status(self) -> dict[str, Any]:
        return {
            "component": "adapter-host",
            "health": self.health,
            "registeredProfiles": sorted(self.registered_profile_ids),
        }

    def assert_allowed(
        self,
        *,
        profile_id: str,
        instance_id: str,
        workspace_id: str,
        operation_id: str,
        context: ExecutionContext | None = None,
    ) -> None:
        if profile_id not in self.registered_profile_ids:
            raise PermissionError(f"Profile not registered with Adapter Host: {profile_id}")
        if not instance_id or not workspace_id or not operation_id:
            raise PermissionError("instanceId, workspaceId, and operationId are required")
        if context is None:
            raise PermissionError("ExecutionContext is required for managed operations")
        try:
            assert_context_complete(context)
        except ExecutionContextError as exc:
            raise PermissionError(f"{exc.code}: {exc.message}") from exc
        if (
            context.profile_id != profile_id
            or context.instance_id != instance_id
            or context.workspace_id != workspace_id
            or context.operation_id != operation_id
        ):
            raise PermissionError(
                "ExecutionContext ids do not match Adapter Host assert_allowed arguments"
            )

    def require_context(self, context: ExecutionContext, *, require_session: bool = False) -> None:
        """Strong gate: refuse when context is incomplete or profile unregistered."""
        if context.profile_id not in self.registered_profile_ids:
            raise PermissionError(f"Profile not registered with Adapter Host: {context.profile_id}")
        try:
            assert_context_complete(context, require_session=require_session)
        except ExecutionContextError as exc:
            raise PermissionError(f"{exc.code}: {exc.message}") from exc
