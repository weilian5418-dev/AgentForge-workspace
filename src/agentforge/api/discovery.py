"""Agent product discovery / registration / validation API (D03)."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException

from agentforge.contracts.agents import (
    DiscoveredInstallationView,
    DiscoveryRunRequest,
    DiscoveryRunResponse,
    ManualInstallationRegisterRequest,
    ProfileCreateRequest,
    ProfileValidateResponse,
)
from agentforge.core.config import Settings
from agentforge.discovery.registry import (
    DiscoveryRegistryError,
    create_profile,
    register_installation,
)
from agentforge.discovery.service import AgentDiscoveryService, DiscoveredInstallation
from agentforge.runtime.control import admits_new_work, read_admission
from agentforge.storage.uow import unit_of_work_connection

router = APIRouter(prefix="/api/v1", tags=["discovery"])


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _engine(request: Request, settings: Settings) -> Any:
    if settings.database_url is None or request.app.state.database is None:
        raise HTTPException(503, "Database is not configured")
    return request.app.state.database


def _map(exc: DiscoveryRegistryError) -> HTTPException:
    status = {
        "installation_not_found": 404,
        "adapter_not_found": 404,
        "inspect_failed": 400,
    }.get(exc.code, 400)
    return HTTPException(status, f"{exc.code}: {exc.message}")


@router.post("/agent-products/discover", response_model=DiscoveryRunResponse)
def discover_products(
    request: Request,
    body: DiscoveryRunRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> DiscoveryRunResponse:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    service = AgentDiscoveryService()
    found = service.discover(
        environments=list(body.environments),
        adapter_ids=list(body.adapter_ids) if body.adapter_ids else None,
    )
    views: list[DiscoveredInstallationView] = []
    if body.persist:
        engine = _engine(request, settings)
        with unit_of_work_connection(engine) as uow:
            for item in found:
                inst = register_installation(uow.session, item)
                views.append(
                    DiscoveredInstallationView(
                        product_id=item.product_id,
                        product_version=item.product_version,
                        environment_id=item.environment_id,
                        location_ref=item.location_ref,
                        auth_status=item.auth_status,  # type: ignore[arg-type]
                        adapter_id=item.adapter_id,
                        notes=item.notes,
                        installation_id=inst.installation_id,
                    )
                )
    else:
        for item in found:
            views.append(
                DiscoveredInstallationView(
                    product_id=item.product_id,
                    product_version=item.product_version,
                    environment_id=item.environment_id,
                    location_ref=item.location_ref,
                    auth_status=item.auth_status,  # type: ignore[arg-type]
                    adapter_id=item.adapter_id,
                    notes=item.notes,
                )
            )
    return DiscoveryRunResponse(items=views)


@router.post("/agent-installations", response_model=DiscoveredInstallationView)
def register_manual_installation(
    request: Request,
    body: ManualInstallationRegisterRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> DiscoveredInstallationView:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _engine(request, settings)
    item = DiscoveredInstallation(
        product_id=body.product_id,
        product_version=body.product_version,
        environment_id=body.environment_id,
        location_ref=body.location_ref,
        auth_status=body.auth_status,
        adapter_id=body.adapter_id,
        notes=["manual_registration"],
    )
    with unit_of_work_connection(engine) as uow:
        inst = register_installation(uow.session, item)
    return DiscoveredInstallationView(
        product_id=item.product_id,
        product_version=item.product_version,
        environment_id=item.environment_id,
        location_ref=item.location_ref,
        auth_status=item.auth_status,  # type: ignore[arg-type]
        adapter_id=item.adapter_id,
        notes=item.notes,
        installation_id=inst.installation_id,
    )


@router.post("/agent-profiles", response_model=ProfileValidateResponse)
def create_and_validate_profile(
    request: Request,
    body: ProfileCreateRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> ProfileValidateResponse:
    if not admits_new_work(read_admission(settings)):
        raise HTTPException(503, "Admission does not accept new work")
    engine = _engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            profile, check = create_profile(
                uow.session,
                installation_id=body.installation_id,
                adapter_id=body.adapter_id,
            )
            return ProfileValidateResponse(
                profile=profile,
                capability_check=check,
                access_level=profile.access_level,
            )
    except DiscoveryRegistryError as exc:
        raise _map(exc) from exc
