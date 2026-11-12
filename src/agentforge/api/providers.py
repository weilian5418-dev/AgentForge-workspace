"""Provider config, grants, budgets, and sync model-call API (D01)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, Response
from starlette.exceptions import HTTPException

from agentforge.contracts.providers import (
    BudgetAccount,
    BudgetAccountCreateRequest,
    ModelAuthorizationGrant,
    ModelAuthorizationGrantCreateRequest,
    ModelCall,
    ModelCallCreateRequest,
    ProviderCreateRequest,
    ProviderCredentialRefResponse,
    ProviderCredentialRegisterRequest,
    ProviderProbeRequest,
    ProviderProbeResult,
    ProviderProfile,
    ProviderUpdateRequest,
)
from agentforge.contracts.runs import OperationAccepted, OperationStatusResponse
from agentforge.core.config import Settings
from agentforge.core.credentials import CredentialError
from agentforge.models.budget import BudgetError, BudgetService
from agentforge.models.credentials import ModelCredentialStore
from agentforge.models.gateway import ModelGateway, ModelGatewayError
from agentforge.queue.operations import (
    OperationError,
    accept_model_probe,
    get_operation,
    request_manual_replay,
)
from agentforge.runtime.control import admits_new_work, read_admission
from agentforge.storage.uow import unit_of_work_connection

router = APIRouter(prefix="/api/v1", tags=["providers"])


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _engine(request: Request, settings: Settings) -> Any:
    if settings.database_url is None or request.app.state.database is None:
        raise HTTPException(503, "Database is not configured")
    return request.app.state.database


def _map(exc: ModelGatewayError | BudgetError | CredentialError | OperationError) -> HTTPException:
    code = getattr(exc, "code", "INVALID_REQUEST")
    status = {
        "provider_not_found": 404,
        "operation_not_found": 404,
        "replay_blocked": 409,
        "grant_not_found": 404,
        "call_not_found": 404,
        "account_not_found": 404,
        "MODEL_AUTHORIZATION_DENIED": 403,
        "CONTEXT_MANIFEST_MISMATCH": 409,
        "IDEMPOTENCY_CONFLICT": 409,
        "BUDGET_EXHAUSTED": 409,
        "BUDGET_EXCEEDED": 409,
        "PROVIDER_AUTH_FAILED": 502,
        "PROVIDER_UNAVAILABLE": 503,
        "credential_missing": 400,
        "credential_required": 400,
    }.get(code, 400)
    message = getattr(exc, "message", str(exc))
    return HTTPException(status, f"{code}: {message}")


@router.post("/providers/credentials", response_model=ProviderCredentialRefResponse)
def register_credential(
    request: Request,
    body: ProviderCredentialRegisterRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> ProviderCredentialRefResponse:
    try:
        store = ModelCredentialStore(settings)
        meta = store.register(body.secret, label=body.label)
    except CredentialError as exc:
        raise _map(exc) from exc
    return ProviderCredentialRefResponse(
        credential_ref=meta["credentialRef"],
        status="configured",
        label=body.label,
        created_at=datetime.now(UTC),
    )


@router.post("/providers", response_model=ProviderProfile)
def create_provider(
    request: Request,
    body: ProviderCreateRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> ProviderProfile:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ModelGateway(uow.session, settings).create_provider(body)
    except ModelGatewayError as exc:
        raise _map(exc) from exc


@router.get("/providers", response_model=list[ProviderProfile])
def list_providers(
    request: Request,
    settings: Annotated[Settings, Depends(_settings)],
) -> list[ProviderProfile]:
    engine = _engine(request, settings)
    with unit_of_work_connection(engine) as uow:
        return ModelGateway(uow.session, settings).list_providers()


@router.get("/providers/{provider_id}", response_model=ProviderProfile)
def get_provider(
    request: Request,
    provider_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> ProviderProfile:
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ModelGateway(uow.session, settings).get_provider(provider_id)
    except ModelGatewayError as exc:
        raise _map(exc) from exc


@router.patch("/providers/{provider_id}", response_model=ProviderProfile)
def update_provider(
    request: Request,
    provider_id: str,
    body: ProviderUpdateRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> ProviderProfile:
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ModelGateway(uow.session, settings).update_provider(provider_id, body)
    except ModelGatewayError as exc:
        raise _map(exc) from exc


@router.post(
    "/providers/{provider_id}/probe",
    response_model=None,
    responses={200: {"model": ProviderProbeResult}, 202: {"model": OperationAccepted}},
)
def probe_provider(
    request: Request,
    provider_id: str,
    body: ProviderProbeRequest,
    settings: Annotated[Settings, Depends(_settings)],
    response: Response,
) -> Any:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    if not body.authorize_network:
        raise HTTPException(400, "authorizeNetwork must be true to probe")
    engine = _engine(request, settings)
    try:
        if body.async_dispatch:
            with unit_of_work_connection(engine) as uow:
                accepted = accept_model_probe(
                    uow.session,
                    provider_id=provider_id,
                    grant_id=body.grant_id,
                    probe_tools=body.probe_tools,
                )
            response.status_code = 202
            return accepted
        with unit_of_work_connection(engine) as uow:
            return ModelGateway(uow.session, settings).probe(provider_id, body)
    except (ModelGatewayError, OperationError) as exc:
        raise _map(exc) from exc


@router.get("/operations/{operation_id}", response_model=OperationStatusResponse)
def get_operation_status(
    request: Request,
    operation_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> OperationStatusResponse:
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return get_operation(uow.session, operation_id)
    except OperationError as exc:
        raise _map(exc) from exc


@router.post("/operations/{operation_id}/replay", response_model=OperationStatusResponse)
def replay_operation(
    request: Request,
    operation_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> OperationStatusResponse:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            request_manual_replay(uow.session, operation_id)
            return get_operation(uow.session, operation_id)
    except OperationError as exc:
        raise _map(exc) from exc


@router.post("/model-grants", response_model=ModelAuthorizationGrant)
def create_grant(
    request: Request,
    body: ModelAuthorizationGrantCreateRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> ModelAuthorizationGrant:
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ModelGateway(uow.session, settings).create_grant(body)
    except ModelGatewayError as exc:
        raise _map(exc) from exc


@router.post("/model-grants/{grant_id}/revoke", response_model=ModelAuthorizationGrant)
def revoke_grant(
    request: Request,
    grant_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> ModelAuthorizationGrant:
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ModelGateway(uow.session, settings).revoke_grant(grant_id)
    except ModelGatewayError as exc:
        raise _map(exc) from exc


@router.post("/budget-accounts", response_model=BudgetAccount)
def create_budget_account(
    request: Request,
    body: BudgetAccountCreateRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> BudgetAccount:
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return BudgetService(uow.session).create_account(body)
    except BudgetError as exc:
        raise _map(exc) from exc


@router.post("/model-calls", response_model=ModelCall)
def create_model_call(
    request: Request,
    body: ModelCallCreateRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> ModelCall:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ModelGateway(uow.session, settings).invoke(body)
    except (ModelGatewayError, BudgetError) as exc:
        raise _map(exc) from exc


@router.get("/model-calls/{call_id}", response_model=ModelCall)
def get_model_call(
    request: Request,
    call_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> ModelCall:
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return ModelGateway(uow.session, settings).get_call(call_id)
    except ModelGatewayError as exc:
        raise _map(exc) from exc
