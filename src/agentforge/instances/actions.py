"""Stop / close / resume handshakes — handshake ≠ factual verification."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from agentforge.contracts.agents import AgentInstanceActionRequest
from agentforge.contracts.runs import OperationAccepted
from agentforge.instances.errors import InstanceError
from agentforge.instances.repository import InstanceRepository
from agentforge.instances.status import combine_instance_status_facets
from agentforge.storage.models import InstanceOperationRow, ProtocolRequestRow


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class StopAdapter(Protocol):
    def request_stop(self, *, session_ref: str, operation_id: str) -> dict[str, Any]: ...

    def inspect_execution(self, *, session_ref: str) -> dict[str, Any]: ...

    def close_session(self, *, session_ref: str) -> dict[str, Any]: ...

    def resume_or_handoff(self, *, session_ref: str, context: dict[str, Any]) -> dict[str, Any]: ...


AdapterFactory = Callable[[str], StopAdapter]


class InstanceActionService:
    def __init__(
        self,
        session: Session,
        *,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self.session = session
        self.repo = InstanceRepository(session)
        self.adapter_factory = adapter_factory

    def request_action(
        self,
        instance_id: str,
        body: AgentInstanceActionRequest,
        *,
        actor: str = "api",
    ) -> OperationAccepted:
        instance = self.repo.get_instance(instance_id)
        if instance is None:
            raise InstanceError("instance_not_found", instance_id)

        kind_map = {
            "request_stop": "request_stop",
            "resume": "resume",
            "close": "close",
        }
        kind = kind_map[body.action]
        op_id = _id("iop")
        self.repo.add_operation(
            InstanceOperationRow(
                id=op_id,
                instance_id=instance_id,
                kind=kind,
                status="prepared",
                idempotency_key=f"{instance_id}:{kind}:{body.reason or 'none'}",
                payload_json={"reason": body.reason, "actor": actor},
                prepared_at=_now(),
            )
        )

        if body.action == "request_stop":
            instance.lifecycle = "stop_requested"
            instance.status = combine_instance_status_facets(
                "stop_requested", instance.activity or "busy"
            )
            instance.updated_at = _now()
            self.repo.add_protocol_request(
                ProtocolRequestRow(
                    id=_id("preq"),
                    request_type="control.stop_request",
                    allowed_response_types=[
                        "control.stop_response",
                        "control.shutdown_approved",
                    ],
                    instance_id=instance_id,
                    native_session_id=instance.native_session_id,
                    attempt_id=instance.current_attempt_id,
                    generation=instance.generation,
                    plan_version=instance.plan_version,
                    assignment_version=instance.assignment_version,
                    stage_id=instance.stage_id,
                    status="pending",
                    initiated_by=actor,
                    legal_responder_refs=[instance_id],
                    created_at=_now(),
                )
            )

        return OperationAccepted(operation_id=op_id, status="accepted", run_id=instance.run_id)

    def execute_stop_handshake(self, operation_id: str) -> dict[str, Any]:
        """Native stop request + inspect evidence. shutdown_approved ≠ stopped."""
        op = self.repo.get_operation(operation_id)
        if op is None or op.kind != "request_stop":
            raise InstanceError("operation_not_found", operation_id)
        instance = self.repo.get_instance(op.instance_id)
        if instance is None:
            raise InstanceError("instance_not_found", op.instance_id)
        session_ref = instance.session_ref
        if not session_ref:
            op.status = "failed"
            op.error_code = "no_session"
            op.completed_at = _now()
            return {"status": "failed", "error": "no_session"}

        if self.adapter_factory is None:
            op.status = "unknown"
            op.error_code = "adapter_unavailable"
            op.completed_at = _now()
            return {"status": "unknown"}

        from agentforge.storage.models import AgentProfileRow

        profile = self.session.get(AgentProfileRow, instance.agent_profile_id)
        adapter = self.adapter_factory(profile.adapter_id if profile else "unknown")
        stop_result = adapter.request_stop(session_ref=session_ref, operation_id=operation_id)
        accepted = stop_result.get("support") in ("supported", "ok") or stop_result.get(
            "accepted"
        )
        # Always inspect — handshake acceptance is not stop evidence.
        inspection = adapter.inspect_execution(session_ref=session_ref)
        running = inspection.get("running")
        if running is True:
            op.status = "unknown"
            op.error_code = "stop_accepted_but_still_running"
            op.native_ref = stop_result.get("nativeRequestId")
            op.payload_json = {
                **dict(op.payload_json or {}),
                "stop": stop_result,
                "inspect": inspection,
            }
            op.completed_at = _now()
            # Do not freeze/close conflicting resources while unknown.
            return {"status": "unknown", "stopAccepted": bool(accepted), "running": True}

        if running is False:
            instance.lifecycle = "stopped"
            instance.activity = "idle"
            instance.status = combine_instance_status_facets("stopped", "idle")
            instance.updated_at = _now()
            op.status = "succeeded"
            op.completed_at = _now()
            op.payload_json = {
                **dict(op.payload_json or {}),
                "stop": stop_result,
                "inspect": inspection,
            }
            return {"status": "succeeded", "stopped": True}

        op.status = "unknown"
        op.error_code = "inspect_inconclusive"
        op.completed_at = _now()
        return {"status": "unknown", "inspect": inspection}

    def execute_close(self, operation_id: str) -> dict[str, Any]:
        op = self.repo.get_operation(operation_id)
        if op is None or op.kind != "close":
            raise InstanceError("operation_not_found", operation_id)
        instance = self.repo.get_instance(op.instance_id)
        if instance is None:
            raise InstanceError("instance_not_found", op.instance_id)
        if instance.lifecycle not in ("stopped", "failed", "closed"):
            raise InstanceError(
                "not_stopped",
                "Close requires confirmed stop; shutdown_approved alone is insufficient",
            )
        session_ref = instance.session_ref
        if session_ref and self.adapter_factory is not None:
            from agentforge.storage.models import AgentProfileRow

            profile = self.session.get(AgentProfileRow, instance.agent_profile_id)
            adapter = self.adapter_factory(profile.adapter_id if profile else "unknown")
            adapter.close_session(session_ref=session_ref)

        native = (
            self.repo.get_native_session(instance.native_session_id)
            if instance.native_session_id
            else None
        )
        if native is not None:
            native.binding_status = "closed"
            native.closed_at = _now()
        # Closing session must not delete workspace or candidates.
        instance.lifecycle = "closed"
        instance.activity = "idle"
        instance.status = combine_instance_status_facets("closed", "idle")
        instance.updated_at = _now()
        op.status = "succeeded"
        op.completed_at = _now()
        return {"status": "succeeded", "closed": True}
