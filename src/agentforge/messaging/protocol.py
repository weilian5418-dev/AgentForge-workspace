"""ProtocolRouter: control protocols before model text delivery."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from agentforge.instances.errors import InstanceError
from agentforge.instances.repository import InstanceRepository


def _now() -> datetime:
    return datetime.now(UTC)


CONTROL_PREFIXES = ("control.",)
FACT_PREFIXES = ("fact.",)


class ProtocolRouter:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = InstanceRepository(session)

    def classify(self, message_type: str) -> str:
        if message_type.startswith(CONTROL_PREFIXES) or message_type in {
            "control.stop_request",
            "control.stop_response",
            "control.shutdown_approved",
            "control.permission_response",
            "control.contract_change_response",
        }:
            return "control"
        if message_type.startswith(FACT_PREFIXES) or message_type.startswith("fact."):
            return "fact"
        if message_type == "stage.release":
            return "platform_derived"
        return "business"

    def match_response(
        self,
        *,
        request_id: str,
        response_type: str,
        responder_ref: str,
        instance_id: str,
        native_session_id: str | None = None,
        attempt_id: str | None = None,
        generation: int | None = None,
        plan_version: int | None = None,
        assignment_version: int | None = None,
        stage_id: str | None = None,
        response_ref: str,
        content: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        req = self.repo.get_protocol_request(request_id)
        if req is None:
            raise InstanceError("protocol_request_not_found", request_id)
        if response_type not in (req.allowed_response_types or []):
            raise InstanceError(
                "illegal_response_type",
                f"{response_type} not in allowedResponseTypes",
            )
        if responder_ref not in (req.legal_responder_refs or []):
            raise InstanceError("illegal_responder", "Responder not authorized for request")
        if req.instance_id != instance_id:
            raise InstanceError("instance_mismatch", "Response instance mismatch")
        if req.native_session_id and req.native_session_id != native_session_id:
            raise InstanceError("session_mismatch", "Response session mismatch")
        if req.attempt_id and req.attempt_id != attempt_id:
            raise InstanceError("attempt_mismatch", "Response attempt mismatch")
        if req.generation is not None and req.generation != generation:
            raise InstanceError("generation_mismatch", "Response generation mismatch")
        if req.plan_version is not None and req.plan_version != plan_version:
            raise InstanceError("plan_version_mismatch", "Response plan version mismatch")
        if req.assignment_version is not None and req.assignment_version != assignment_version:
            raise InstanceError("assignment_version_mismatch", "Response assignment mismatch")
        if req.stage_id and req.stage_id != stage_id:
            raise InstanceError("stage_mismatch", "Response stage mismatch")
        digest = hashlib.sha256(
            json.dumps(
                {
                    "requestId": request_id,
                    "type": response_type,
                    "sender": responder_ref,
                    "session": native_session_id,
                    "attempt": attempt_id,
                    "generation": generation,
                    "plan": plan_version,
                    "assignment": assignment_version,
                    "stage": stage_id,
                    "content": content or {},
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        if req.status == "pending" and req.deadline_at is not None and req.deadline_at < _now():
            req.status = "timed_out"
            raise InstanceError("protocol_request_expired", "Request deadline passed")

        if req.status != "pending":
            if req.matched_response_ref == response_ref and req.matched_response_digest == digest:
                return {"status": "duplicate_ignored", "requestId": request_id}
            # Opposite/different content → conflict, do not overwrite history.
            conflicts = list(req.conflict_refs or [])
            if response_ref not in conflicts:
                conflicts.append(response_ref)
            req.conflict_refs = conflicts
            req.status = "conflict"
            return {"status": "conflict", "requestId": request_id}

        req.status = "matched"
        req.matched_response_ref = response_ref
        req.matched_response_digest = digest
        # shutdown_approved only means peer accepted close request — not stopped.
        return {
            "status": "matched",
            "requestId": request_id,
            "responseType": response_type,
            "impliesStopped": False,
            "content": content or {},
        }

    def route_inbound(self, message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Control first — do not let the model decide stop/auth from prose."""
        kind = self.classify(message_type)
        if kind == "control":
            return {"route": "domain_processor", "kind": kind, "deliverToModel": False}
        if kind == "fact":
            return {"route": "observation", "kind": kind, "deliverToModel": False}
        if kind == "platform_derived":
            return {"route": "gate_domain", "kind": kind, "deliverToModel": False}
        return {"route": "instance_mailbox", "kind": kind, "deliverToModel": True}
