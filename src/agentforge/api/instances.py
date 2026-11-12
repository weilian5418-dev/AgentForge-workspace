"""Instance preparation, messages, and actions API (D09)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from starlette.exceptions import HTTPException

from agentforge.contracts.agents import (
    AgentInstanceActionRequest,
    AgentInstanceListResponse,
    ExecutionRequest,
)
from agentforge.contracts.messages import (
    MessageCreateRequest,
    MessageDeliveryListResponse,
    MessageListResponse,
)
from agentforge.contracts.runs import OperationAccepted
from agentforge.core.config import Settings
from agentforge.instances.actions import InstanceActionService
from agentforge.instances.errors import InstanceError
from agentforge.instances.event_ingestor import NativeEventIngestor
from agentforge.instances.preparation import ExecutionPreparationService
from agentforge.messaging.service import MessageService
from agentforge.runtime.control import admits_new_work, read_admission
from agentforge.storage.database import create_database_engine
from agentforge.storage.uow import unit_of_work_connection

router = APIRouter(prefix="/api/v1", tags=["instances"])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _require_engine(request: Request, settings: Settings):
    engine = getattr(request.app.state, "database", None) or create_database_engine(settings)
    if engine is None:
        raise HTTPException(503, "database_unavailable: Database is not configured")
    return engine


def _map_error(exc: InstanceError) -> HTTPException:
    status = {
        "run_not_found": 404,
        "instance_not_found": 404,
        "plan_not_found": 404,
        "message_not_found": 404,
        "delivery_not_found": 404,
        "operation_not_found": 404,
        "protocol_request_not_found": 404,
        "workspace_grant_revoked": 403,
        "auth_invalid": 403,
        "untrusted_authentic_flag": 403,
        "plan_not_approved": 409,
        "plan_hash_mismatch": 409,
        "plan_version_stale": 409,
        "gate_policy_mismatch": 409,
        "profile_config_changed": 409,
        "execution_blocked": 409,
        "assignment_revoked": 409,
        "active_session_exists": 409,
        "not_stopped": 409,
        "illegal_response_type": 409,
        "illegal_responder": 409,
    }.get(exc.code, 400)
    return HTTPException(status, f"{exc.code}: {exc.message}")


@router.post("/runs/{run_id}/execution-requests", response_model=OperationAccepted, status_code=202)
def create_execution_request(
    request: Request,
    run_id: str,
    body: ExecutionRequest,
    settings: Annotated[Settings, Depends(_settings)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> OperationAccepted:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "admission_blocked: Platform is not admitting new work")
    key = idempotency_key or (
        f"{body.plan_hash}:{body.plan_version}:{body.assignment_version}:"
        f"{body.gate_policy_version}"
    )
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ExecutionPreparationService(uow.session).prepare(
                run_id, body, actor="api", idempotency_key=key
            )
    except InstanceError as exc:
        raise _map_error(exc) from exc


@router.get("/runs/{run_id}/instances", response_model=AgentInstanceListResponse)
def list_instances(
    request: Request,
    run_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> AgentInstanceListResponse:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            items = ExecutionPreparationService(uow.session).list_instances(run_id)
            return AgentInstanceListResponse(items=items)
    except InstanceError as exc:
        raise _map_error(exc) from exc


@router.get("/agent-instances/{instance_id}")
def get_instance(
    request: Request,
    instance_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, Any]:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            view = ExecutionPreparationService(uow.session).get_instance(instance_id)
            inst = view["instance"]
            return {
                "instance": inst.model_dump(mode="json", by_alias=True),
                "session": view["session"],
                "currentExecution": view["currentExecution"],
            }
    except InstanceError as exc:
        raise _map_error(exc) from exc


@router.post(
    "/agent-instances/{instance_id}/actions",
    response_model=OperationAccepted,
    status_code=202,
)
def instance_action(
    request: Request,
    instance_id: str,
    body: AgentInstanceActionRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> OperationAccepted:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return InstanceActionService(uow.session).request_action(
                instance_id, body, actor="api"
            )
    except InstanceError as exc:
        raise _map_error(exc) from exc


@router.post("/runs/{run_id}/messages", response_model=dict)
def create_message(
    request: Request,
    run_id: str,
    body: MessageCreateRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, Any]:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            msg = MessageService(uow.session).create(
                run_id, body, sender_ref="user:api"
            )
            return msg.model_dump(mode="json", by_alias=True)
    except InstanceError as exc:
        raise _map_error(exc) from exc


@router.get("/runs/{run_id}/messages", response_model=MessageListResponse)
def list_messages(
    request: Request,
    run_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> MessageListResponse:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            items = MessageService(uow.session).list_messages(run_id)
            return MessageListResponse(items=items)
    except InstanceError as exc:
        raise _map_error(exc) from exc


@router.get("/messages/{message_id}/deliveries", response_model=MessageDeliveryListResponse)
def list_deliveries(
    request: Request,
    message_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> MessageDeliveryListResponse:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            items = MessageService(uow.session).list_deliveries(message_id)
            return MessageDeliveryListResponse(items=items)
    except InstanceError as exc:
        raise _map_error(exc) from exc


@router.post("/internal/native-events")
def ingest_native_event(
    request: Request,
    body: dict[str, Any],
    settings: Annotated[Settings, Depends(_settings)],
    x_internal_service: Annotated[str | None, Header(alias="X-Internal-Service")] = None,
) -> dict[str, Any]:
    """Separate internal auth entry — users cannot self-set authentic/stopped."""
    if not x_internal_service:
        raise HTTPException(401, "internal_auth_required: Missing X-Internal-Service")
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return NativeEventIngestor(uow.session).ingest(
                instance_id=body["instanceId"],
                source=body["source"],
                dedupe_key=body["dedupeKey"],
                event_type=body["eventType"],
                payload=body.get("payload") or {},
                cursor=body.get("cursor"),
                authentic=bool(body.get("authentic")),
                native_session_id=body.get("nativeSessionId"),
                service_authenticated=True,
            )
    except InstanceError as exc:
        raise _map_error(exc) from exc
    except KeyError as exc:
        raise HTTPException(400, f"missing_field: {exc}") from exc
