"""DeliveryCoordinator — claim, native send outside txn, save receipt/unknown."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from agentforge.instances.errors import InstanceError
from agentforge.instances.repository import InstanceRepository
from agentforge.messaging.repository import MessageRepository
from agentforge.storage.models import DeliveryAttemptRow


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class DeliveryAdapter(Protocol):
    def deliver_message(self, *, session_ref: str, message: dict[str, Any]) -> dict[str, Any]: ...


AdapterFactory = Callable[[str], DeliveryAdapter]


class DeliveryCoordinator:
    def __init__(
        self,
        session: Session,
        *,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self.session = session
        self.messages = MessageRepository(session)
        self.instances = InstanceRepository(session)
        self.adapter_factory = adapter_factory

    def process_claims(self, *, owner: str = "delivery_coordinator", limit: int = 10) -> list[dict[str, Any]]:
        claimed = self.messages.claim_deliveries(owner=owner, limit=limit)
        return [
            self.complete_delivery(d.id, owner=owner, token=d.claim_token or "")
            for d in claimed
        ]

    def complete_delivery(
        self, delivery_id: str, *, owner: str, token: str
    ) -> dict[str, Any]:
        delivery = self.messages.get_delivery(delivery_id)
        if delivery is None:
            raise InstanceError("delivery_not_found", delivery_id)
        if delivery.claim_owner != owner or delivery.claim_token != token:
            raise InstanceError("claim_mismatch", "Delivery claim mismatch")

        message = self.messages.get_message(delivery.message_id)
        if message is None:
            raise InstanceError("message_not_found", delivery.message_id)
        recipient = self.instances.get_instance(delivery.recipient_instance_id)
        if recipient is None:
            raise InstanceError("recipient_missing", delivery.recipient_instance_id)

        # Re-validate versions — user reassignment / revoke rejects old deliveries.
        if (
            delivery.assignment_version is not None
            and recipient.assignment_version is not None
            and delivery.assignment_version != recipient.assignment_version
        ):
            delivery.transmission_status = "failed"
            delivery.status = "failed"
            delivery.error_code = "assignment_revoked"
            delivery.claim_owner = None
            delivery.claim_token = None
            delivery.claim_expires_at = None
            delivery.updated_at = _now()
            return {"deliveryId": delivery_id, "status": "failed", "error": "assignment_revoked"}

        if recipient.lifecycle in ("closed", "failed", "stopped"):
            delivery.transmission_status = "failed"
            delivery.status = "failed"
            delivery.error_code = "instance_not_receivable"
            delivery.claim_owner = None
            delivery.claim_token = None
            delivery.claim_expires_at = None
            delivery.updated_at = _now()
            return {"deliveryId": delivery_id, "status": "failed"}

        # Safe boundary: do not force-inject when only turn-boundary / no realtime.
        if delivery.delivery_mode == "next_safe_boundary" and recipient.activity not in (
            "idle",
            "waiting_input",
        ):
            delivery.transmission_status = "pending"
            delivery.status = "pending"
            delivery.next_attempt_at = _now()
            delivery.claim_owner = None
            delivery.claim_token = None
            delivery.claim_expires_at = None
            delivery.updated_at = _now()
            return {"deliveryId": delivery_id, "status": "queued_until_boundary"}

        if delivery.delivery_mode == "manual_assisted":
            delivery.transmission_status = "pending"
            delivery.status = "pending"
            delivery.error_code = "manual_assisted"
            delivery.claim_owner = None
            delivery.claim_token = None
            delivery.claim_expires_at = None
            delivery.updated_at = _now()
            return {"deliveryId": delivery_id, "status": "manual_assisted"}

        session_ref = recipient.session_ref
        if not session_ref:
            delivery.transmission_status = "failed"
            delivery.status = "failed"
            delivery.error_code = "no_session"
            delivery.claim_owner = None
            delivery.claim_token = None
            delivery.claim_expires_at = None
            delivery.updated_at = _now()
            return {"deliveryId": delivery_id, "status": "failed", "error": "no_session"}

        op_id = _id("iop")
        attempt = DeliveryAttemptRow(
            id=_id("da"),
            delivery_id=delivery_id,
            operation_id=op_id,
            transmission_status="claimed",
            started_at=_now(),
        )
        self.messages.add_delivery_attempt(attempt)

        if self.adapter_factory is None:
            attempt.transmission_status = "unknown"
            attempt.finished_at = _now()
            attempt.error_code = "adapter_unavailable"
            delivery.transmission_status = "unknown"
            delivery.status = "unknown"
            delivery.claim_owner = None
            delivery.claim_token = None
            delivery.claim_expires_at = None
            delivery.updated_at = _now()
            return {"deliveryId": delivery_id, "status": "unknown"}

        from agentforge.storage.models import AgentProfileRow

        profile = self.session.get(AgentProfileRow, recipient.agent_profile_id)
        adapter = self.adapter_factory(profile.adapter_id if profile else "unknown")
        envelope = {
            "messageId": message.id,
            "type": message.message_type,
            "source": message.sender_ref,
            "planVersion": message.plan_version,
            "assignmentVersion": message.assignment_version,
            "payload": message.payload,
            "untrustedContentBoundary": True,
        }
        try:
            result = adapter.deliver_message(session_ref=session_ref, message=envelope)
        except Exception as exc:  # noqa: BLE001
            attempt.transmission_status = "unknown"
            attempt.error_code = "native_timeout_or_error"
            attempt.finished_at = _now()
            delivery.transmission_status = "unknown"
            delivery.status = "unknown"
            delivery.error_code = str(exc)[:128]
            delivery.claim_owner = None
            delivery.claim_token = None
            delivery.claim_expires_at = None
            delivery.updated_at = _now()
            # Do not auto-resend to a new session on unknown.
            return {
                "deliveryId": delivery_id,
                "status": "unknown",
                "messageId": message.id,
                "retry": False,
            }

        support = result.get("support") or result.get("status")
        receipt = result.get("receiptRef") or result.get("native_receipt_ref")
        if support in ("unsupported",):
            attempt.transmission_status = "failed"
            attempt.error_code = "unsupported"
            attempt.finished_at = _now()
            delivery.transmission_status = "failed"
            delivery.status = "failed"
            delivery.error_code = "unsupported"
        elif support in ("unknown",) or (not receipt and support not in ("supported", "ok", "sent", "received")):
            # native supported≠received; timeout → unknown only.
            attempt.transmission_status = "unknown"
            attempt.finished_at = _now()
            delivery.transmission_status = "unknown"
            delivery.status = "unknown"
            delivery.error_code = "native_unknown"
        else:
            received = result.get("received") is True or support == "received"
            attempt.transmission_status = "received" if received else "sent"
            attempt.native_receipt_ref = receipt
            attempt.finished_at = _now()
            delivery.transmission_status = attempt.transmission_status
            delivery.status = "received" if received else "sent"
            delivery.native_receipt_ref = receipt
            if received:
                delivery.received_at = _now()
                if delivery.reply_status == "awaiting_reply":
                    delivery.status = "awaiting_reply"

        delivery.claim_owner = None
        delivery.claim_token = None
        delivery.claim_expires_at = None
        delivery.updated_at = _now()
        delivery.retry_count = int(delivery.retry_count or 0) + 1
        return {
            "deliveryId": delivery_id,
            "status": delivery.transmission_status,
            "messageId": message.id,
            "receiptRef": receipt,
        }
