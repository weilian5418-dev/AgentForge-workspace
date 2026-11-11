"""System health and capability contracts."""

from typing import Any, Literal

from pydantic import Field

from agentforge.contracts.base import ApiInputModel, ContractModel
from agentforge.contracts.common import OpaqueId
from agentforge.contracts.errors import ErrorDetail, ErrorResponse

__all__ = [
    "CapabilityResponse",
    "DependencyStatus",
    "ErrorDetail",
    "ErrorResponse",
    "HealthResponse",
    "ReadinessResponse",
    "ShutdownRequest",
    "ShutdownResponse",
]


class HealthResponse(ContractModel):
    status: Literal["ok"] = "ok"


class DependencyStatus(ContractModel):
    status: Literal["available", "unavailable", "unconfigured", "not_checked"]
    message: str


class ReadinessResponse(ContractModel):
    status: Literal["ready", "not_ready"]
    run_mode: Literal["dev", "local", "hosted"] = "dev"
    admission: Literal["open", "drain", "blocked"] = "open"
    accepting_work: bool = True
    database: DependencyStatus
    reason: str | None = None


class CapabilityResponse(ContractModel):
    schema_version: Literal["1"] = "1"
    version: str
    run_mode: Literal["dev", "local", "hosted"] = "dev"
    admission: Literal["open", "drain", "blocked"] = "open"
    git: DependencyStatus
    docker: DependencyStatus
    database: DependencyStatus
    agent_execution: Literal[False] = False
    components: list[dict[str, Any]] = Field(default_factory=list)


class ShutdownRequest(ApiInputModel):
    instance_id: OpaqueId
    force: bool = Field(
        default=False,
        strict=True,
        description=(
            "When true, stop the platform API even if agent drain coordination is incomplete. "
            "Default false: incomplete/unknown coordination returns 409 and does not exit."
        ),
    )
    platform_only: bool = Field(
        default=False,
        strict=True,
        description=(
            "Dev/API-only stop: skip agent coordination when no collaboration DB is configured. "
            "Ignored when collaboration tables exist and report pending work unless force=true."
        ),
    )


class ShutdownResponse(ContractModel):
    status: Literal["stopping", "drain_incomplete"] = "stopping"
    instance_id: OpaqueId
    drain_complete: bool = Field(default=True, strict=True)
    verification: Literal["verified_empty", "pending", "unknown", "platform_only"] = (
        "verified_empty"
    )
    pending_agent_instances: list[dict[str, Any]] = Field(default_factory=list)
    unconfirmed_side_effects: list[dict[str, Any]] = Field(default_factory=list)
    message: str | None = None
