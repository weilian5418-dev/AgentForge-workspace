"""Session creation with crash-window handling (succeeded but not recorded)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from agentforge.instances.context_loader import ExecutionContextLoader
from agentforge.instances.errors import InstanceError
from agentforge.instances.repository import InstanceRepository


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class SessionAdapter(Protocol):
    def create_session(
        self, *, profile_id: str, workspace_id: str, operation_id: str
    ) -> dict[str, Any]: ...

    def inspect_execution(self, *, session_ref: str) -> dict[str, Any]: ...


AdapterFactory = Callable[[str], SessionAdapter]


class SessionCoordinator:
    def __init__(
        self,
        session: Session,
        *,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self.session = session
        self.repo = InstanceRepository(session)
        self.loader = ExecutionContextLoader(session)
        self.adapter_factory = adapter_factory

    def process_create_claims(
        self, *, owner: str = "session_coordinator", limit: int = 10
    ) -> list[dict[str, Any]]:
        raise InstanceError(
            "engine_coordinator_required",
            "Use instances.worker with an Engine; native I/O inside caller transactions is prohibited",
        )

    def complete_create_session_after_commit(
        self, operation_id: str, *, owner: str, token: str
    ) -> dict[str, Any]:
        raise InstanceError(
            "engine_coordinator_required",
            "Use instances.worker with an Engine; native I/O inside caller transactions is prohibited",
        )

    def _complete_create_session(
        self, operation_id: str, *, owner: str, token: str
    ) -> dict[str, Any]:
        raise InstanceError(
            "engine_coordinator_required",
            "Use instances.worker with an Engine; native I/O inside caller transactions is prohibited",
        )
