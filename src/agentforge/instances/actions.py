"""Stop / close / resume handshakes — handshake ≠ factual verification."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from sqlalchemy.orm import Session

from agentforge.contracts.agents import AgentInstanceActionRequest
from agentforge.contracts.runs import OperationAccepted
from agentforge.instances.errors import InstanceError
from agentforge.instances.repository import InstanceRepository
from agentforge.instances.status import combine_instance_status_facets
from agentforge.storage.models import InstanceOperationRow, Outbox, ProtocolRequestRow


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
        existing = self.repo.find_operation_by_idempotency(
            instance_id=instance_id,
            kind=kind,
            idempotency_key=f"{instance_id}:{kind}:{instance.current_attempt_id}:{body.reason or 'none'}",
        )
        if existing:
            return OperationAccepted(
                operation_id=existing.id, status="accepted", run_id=instance.run_id
            )
        op_id = _id("iop")
        self.repo.add_operation(
            InstanceOperationRow(
                id=op_id,
                instance_id=instance_id,
                kind=kind,
                status="prepared",
                idempotency_key=f"{instance_id}:{kind}:{instance.current_attempt_id}:{body.reason or 'none'}",
                attempt_id=instance.current_attempt_id,
                payload_json={
                    "reason": body.reason,
                    "actor": actor,
                    "sessionRef": instance.session_ref,
                    "nativeSessionId": instance.native_session_id,
                    "generation": instance.generation,
                },
                prepared_at=_now(),
            )
        )

        if body.action == "request_stop":
            instance.lifecycle = "stop_requested"
            instance.status = combine_instance_status_facets(
                "stop_requested", cast(Any, instance.activity or "busy")
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

        self.session.add(
            Outbox(
                id=_id("obx"),
                run_id=instance.run_id,
                intent_type=f"instance.{kind}",
                target="instance",
                payload={"operationId": op_id, "instanceId": instance.id},
                idempotency_key=f"instance-op:{op_id}",
                delivery_status="pending",
                attempt_count=0,
            )
        )
        return OperationAccepted(operation_id=op_id, status="accepted", run_id=instance.run_id)

    def execute_stop_handshake(self, operation_id: str) -> dict[str, Any]:
        raise InstanceError(
            "engine_coordinator_required",
            "Use instances.worker with an Engine; native I/O inside caller transactions is prohibited",
        )

    def execute_close(self, operation_id: str) -> dict[str, Any]:
        raise InstanceError(
            "engine_coordinator_required",
            "Use instances.worker with an Engine; native I/O inside caller transactions is prohibited",
        )
