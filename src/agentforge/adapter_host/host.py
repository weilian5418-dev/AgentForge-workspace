"""Adapter Host accepts only registered profile/instance/workspace/operation tickets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    ) -> None:
        if profile_id not in self.registered_profile_ids:
            raise PermissionError(f"Profile not registered with Adapter Host: {profile_id}")
        if not instance_id or not workspace_id or not operation_id:
            raise PermissionError("instanceId, workspaceId, and operationId are required")
