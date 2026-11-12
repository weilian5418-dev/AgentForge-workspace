"""MessageService — create structured messages; does not create team members."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from agentforge.contracts.messages import (
    CollaborationMessage,
    MessageCreateRequest,
    MessageDelivery,
)
from agentforge.contracts.runs import OperationAccepted
from agentforge.instances.eligibility import EligibilityService
from agentforge.instances.errors import InstanceError
from agentforge.instances.repository import InstanceRepository
from agentforge.messaging.protocol import ProtocolRouter
from agentforge.messaging.repository import MessageRepository
from agentforge.storage.models import (
    CollaborationMessageRow,
    MessageDeliveryRow,
    Outbox,
    Run,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class MessageService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.messages = MessageRepository(session)
        self.instances = InstanceRepository(session)
        self.protocol = ProtocolRouter(session)
        self.eligibility = EligibilityService(session)

    def create(
        self,
        run_id: str,
        body: MessageCreateRequest,
        *,
        sender_ref: str,
        project_id: str | None = None,
        plan_version: int | None = None,
        assignment_version: int | None = None,
    ) -> CollaborationMessage:
        run = self.session.get(Run, run_id)
        if run is None:
            raise InstanceError("run_not_found", run_id)

        route = self.protocol.route_inbound(body.type, body.payload.model_dump(by_alias=True))
        if body.type == "task.assignment":
            # Must go through eligibility — assignment approval ≠ stage dispatch.
            payload_obj = body.payload
            stage_id = body.stage_id or getattr(payload_obj, "stage_id", None)
            wp = body.work_package_id or getattr(payload_obj, "work_package_id", None)
            if stage_id and wp:
                self.eligibility.assert_may_dispatch(
                    run_id=run_id, stage_id=stage_id, work_package_id=wp
                )

        # Recipients must be concrete instances (or deterministically resolved roles).
        recipient_instance_ids: list[str] = []
        for ref in body.recipient_refs:
            inst = self.instances.get_instance(ref)
            if inst is None:
                # Role id → unique instance on this run
                matches = [
                    i
                    for i in self.instances.list_instances_for_run(run_id)
                    if i.role_id == ref
                ]
                if len(matches) != 1:
                    raise InstanceError(
                        "ambiguous_recipient",
                        f"Recipient {ref} is not a concrete instance",
                    )
                recipient_instance_ids.append(matches[0].id)
            else:
                if inst.run_id != run_id:
                    raise InstanceError("recipient_wrong_run", ref)
                recipient_instance_ids.append(inst.id)

        msg_id = _id("msg")
        payload = body.payload.model_dump(by_alias=True)
        row = CollaborationMessageRow(
            id=msg_id,
            run_id=run_id,
            project_id=project_id or run.project_id,
            stage_id=body.stage_id,
            work_package_id=body.work_package_id,
            message_type=body.type,
            sender_ref=sender_ref,
            recipient_refs=list(body.recipient_refs),
            payload=payload,
            artifact_refs=[],
            plan_version=plan_version or run.plan_version,
            assignment_version=assignment_version,
            reply_required=body.reply_required,
            reply_to=body.reply_to,
            request_id=body.request_id,
            created_at=_now(),
        )
        self.messages.add_message(row)

        for recipient_id in recipient_instance_ids:
            recipient = self.instances.get_instance(recipient_id)
            if recipient is None:
                raise InstanceError("recipient_missing", recipient_id)
            # Re-check versions at create time; delivery claim re-checks again.
            if (
                assignment_version is not None
                and recipient.assignment_version is not None
                and recipient.assignment_version != assignment_version
            ):
                raise InstanceError(
                    "assignment_revoked",
                    "Recipient assignment version no longer matches",
                )
            delivery_id = _id("del")
            attempt_id = _id("datt")
            reply_status = "awaiting_reply" if body.reply_required else "not_required"
            self.messages.add_delivery(
                MessageDeliveryRow(
                    id=delivery_id,
                    message_id=msg_id,
                    recipient_instance_id=recipient_id,
                    attempt_id=attempt_id,
                    status="pending",
                    transmission_status="pending",
                    reply_status=reply_status if not body.reply_required else "awaiting_reply",
                    delivery_mode=self._choose_mode(recipient),
                    assignment_version=assignment_version or recipient.assignment_version,
                    plan_version=plan_version or run.plan_version,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            self.session.add(
                Outbox(
                    id=_id("obx"),
                    run_id=run_id,
                    event_id=None,
                    intent_type="message.deliver",
                    target="delivery_coordinator",
                    payload={"deliveryId": delivery_id, "messageId": msg_id},
                    idempotency_key=f"deliver:{delivery_id}",
                    delivery_status="pending",
                    attempt_count=0,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )

        if not route.get("deliverToModel") and body.type.startswith("control."):
            # Control still recorded; domain processors consume separately.
            pass

        return self._to_message(row)

    def list_messages(self, run_id: str) -> list[CollaborationMessage]:
        return [self._to_message(r) for r in self.messages.list_messages(run_id)]

    def list_deliveries(self, message_id: str) -> list[MessageDelivery]:
        return [self._to_delivery(r) for r in self.messages.list_deliveries(message_id)]

    def get_operation_status(self, operation_id: str) -> OperationAccepted:
        op = self.instances.get_operation(operation_id)
        if op is None:
            raise InstanceError("operation_not_found", operation_id)
        status_map = {
            "prepared": "accepted",
            "claimed": "running",
            "native_invoked": "running",
            "succeeded": "succeeded",
            "failed": "failed",
            "unknown": "needs_reconciliation",
            "needs_reconciliation": "needs_reconciliation",
        }
        inst = self.instances.get_instance(op.instance_id)
        return OperationAccepted(
            operation_id=operation_id,
            status=status_map.get(op.status, "accepted"),  # type: ignore[arg-type]
            run_id=inst.run_id if inst else None,
        )

    @staticmethod
    def _choose_mode(recipient: Any) -> str:
        # Default conservative boundary; realtime only when capability evidenced later.
        return "next_safe_boundary"

    @staticmethod
    def _to_message(row: CollaborationMessageRow) -> CollaborationMessage:
        return CollaborationMessage.model_validate(
            {
                "schemaVersion": "1",
                "messageId": row.id,
                "projectId": row.project_id or "unknown",
                "runId": row.run_id,
                "stageId": row.stage_id,
                "workPackageId": row.work_package_id,
                "senderRef": row.sender_ref,
                "recipientRefs": list(row.recipient_refs or ["pending"]),
                "payload": row.payload,
                "artifactRefs": list(row.artifact_refs or []),
                "planVersion": row.plan_version,
                "assignmentVersion": row.assignment_version,
                "replyRequired": row.reply_required,
                "replyTo": row.reply_to,
                "requestId": row.request_id,
                "correlationId": row.correlation_id,
                "createdAt": row.created_at,
                "expiresAt": row.expires_at,
                "derivedFromGateDecisionId": row.derived_from_gate_decision_id,
            }
        )

    @staticmethod
    def _to_delivery(row: MessageDeliveryRow) -> MessageDelivery:
        return MessageDelivery.model_validate(
            {
                "deliveryId": row.id,
                "messageId": row.message_id,
                "recipientInstanceId": row.recipient_instance_id,
                "attemptId": row.attempt_id,
                "transmissionStatus": row.transmission_status,
                "replyStatus": row.reply_status,
                "status": row.status,
                "nativeReceiptRef": row.native_receipt_ref,
                "receivedAt": row.received_at,
                "respondedAt": row.responded_at,
                "retryCount": row.retry_count,
                "nextAttemptAt": row.next_attempt_at,
                "errorCode": row.error_code,
                "deliveryMode": row.delivery_mode,
            }
        )
