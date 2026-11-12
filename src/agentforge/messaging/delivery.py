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

    def process_claims(
        self, *, owner: str = "delivery_coordinator", limit: int = 10
    ) -> list[dict[str, Any]]:
        raise InstanceError(
            "engine_coordinator_required",
            "Use instances.worker with an Engine; native I/O inside caller transactions is prohibited",
        )

    def complete_delivery(self, delivery_id: str, *, owner: str, token: str) -> dict[str, Any]:
        raise InstanceError(
            "engine_coordinator_required",
            "Use instances.worker with an Engine; native I/O inside caller transactions is prohibited",
        )
