"""Unified AgentAdapter protocol — product-specific adapters implement this.

Method names are platform abstractions; products need not expose same-named APIs.
Results must use supported/unsupported/unknown/failed — never a single available bool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

CapabilitySupport = Literal["supported", "unsupported", "unknown", "failed"]


@dataclass
class AdapterCapabilityMatrix:
    product_id: str
    product_version: str
    adapter_id: str
    adapter_version: str
    environment: str
    checked_at: str
    capabilities: dict[str, CapabilitySupport] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)

    def access_level(self) -> Literal["controlled_auto", "limited_auto", "manual_assisted"]:
        required = {
            "createSession",
            "workspaceBinding",
            "stopConfirmation",
        }
        missing = [key for key in required if self.capabilities.get(key) not in {"supported"}]
        if not missing and self.capabilities.get("approvalRoundtrip") == "supported":
            return "controlled_auto"
        if any(self.capabilities.get(key) == "supported" for key in required):
            return "limited_auto"
        return "manual_assisted"


class AgentAdapter(Protocol):
    adapter_id: str
    adapter_version: str

    def discover(self, environment: str) -> list[dict[str, Any]]: ...

    def inspect(self, installation_ref: str) -> dict[str, Any]: ...

    def validate_capabilities(self, profile_id: str) -> AdapterCapabilityMatrix: ...

    def create_session(
        self, *, profile_id: str, workspace_id: str, operation_id: str
    ) -> dict[str, Any]: ...

    def submit_task(self, *, session_ref: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def poll_events(
        self, *, session_ref: str, cursor: str | None = None
    ) -> list[dict[str, Any]]: ...

    def deliver_message(self, *, session_ref: str, message: dict[str, Any]) -> dict[str, Any]: ...

    def respond_approval(
        self, *, session_ref: str, native_request_id: str, decision: str
    ) -> dict[str, Any]: ...

    def request_stop(self, *, session_ref: str, operation_id: str) -> dict[str, Any]: ...

    def inspect_execution(self, *, session_ref: str) -> dict[str, Any]: ...

    def resume_or_handoff(self, *, session_ref: str, context: dict[str, Any]) -> dict[str, Any]: ...

    def close_session(self, *, session_ref: str) -> dict[str, Any]: ...
