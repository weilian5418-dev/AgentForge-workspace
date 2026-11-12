"""Native event auth, normalize, dedupe, and cursor management."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from agentforge.instances.errors import InstanceError
from agentforge.instances.repository import InstanceRepository
from agentforge.instances.status import combine_instance_status_facets
from agentforge.storage.models import NativeEventReceiptRow


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class NativeEventIngestor:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = InstanceRepository(session)

    def ingest(
        self,
        *,
        instance_id: str,
        source: str,
        dedupe_key: str,
        event_type: str,
        payload: dict[str, Any],
        cursor: str | None = None,
        authentic: bool,
        native_session_id: str | None = None,
        service_authenticated: bool = False,
    ) -> dict[str, Any]:
        """Internal service entry — ordinary users cannot set authentic=True."""
        if authentic and not service_authenticated:
            raise InstanceError(
                "untrusted_authentic_flag",
                "authentic=true requires internal service authentication",
            )
        instance = self.repo.get_instance(instance_id)
        if instance is None:
            raise InstanceError("instance_not_found", instance_id)

        existing = self.repo.find_event_receipt(
            instance_id=instance_id, source=source, dedupe_key=dedupe_key
        )
        if existing is not None:
            return {
                "receiptId": existing.id,
                "duplicate": True,
                "trusted": bool(existing.authentic),
                "eventType": existing.event_type,
            }

        if not authentic:
            # Unauthenticated idle text cannot establish a safety boundary.
            receipt = NativeEventReceiptRow(
                id=_id("rcpt"),
                instance_id=instance_id,
                native_session_id=native_session_id or instance.native_session_id,
                source=source,
                dedupe_key=dedupe_key,
                cursor=cursor,
                event_type=event_type,
                normalized_payload={"raw": payload, "trusted": False},
                authentic=False,
                received_at=_now(),
            )
            self.repo.add_event_receipt(receipt)
            return {"receiptId": receipt.id, "duplicate": False, "trusted": False}

        normalized = {
            "type": event_type,
            "payload": payload,
            "trusted": True,
        }
        receipt = NativeEventReceiptRow(
            id=_id("rcpt"),
            instance_id=instance_id,
            native_session_id=native_session_id or instance.native_session_id,
            source=source,
            dedupe_key=dedupe_key,
            cursor=cursor,
            event_type=event_type,
            normalized_payload=normalized,
            authentic=True,
            received_at=_now(),
        )
        self.repo.add_event_receipt(receipt)

        # Fact events update observation only — never stage.release / approvals.
        if event_type == "fact.idle" and instance.lifecycle == "active":
            instance.activity = "idle"
            instance.status = combine_instance_status_facets("active", "idle")
            instance.updated_at = _now()
        elif event_type == "fact.turn_finished" and instance.lifecycle == "active":
            instance.activity = "idle"
            instance.status = combine_instance_status_facets("active", "idle")
            instance.updated_at = _now()
        elif event_type == "fact.session_terminated":
            instance.lifecycle = "stopped"
            instance.activity = "idle"
            instance.status = combine_instance_status_facets("stopped", "idle")
            instance.updated_at = _now()

        return {
            "receiptId": receipt.id,
            "duplicate": False,
            "trusted": True,
            "cursor": cursor,
        }
