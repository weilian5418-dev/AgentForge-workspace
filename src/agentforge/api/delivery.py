"""Delivery pointer, immutable manifests, and user acceptance API (D12.13)."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, Response
from starlette.exceptions import HTTPException

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.delivery import (
    AcceptanceDecisionListResponse,
    AcceptanceDecisionRequest,
    AcceptanceResult,
    DeliveryManifestView,
    DeliveryPointerView,
)
from agentforge.core.config import Settings
from agentforge.delivery.acceptance import (
    AcceptanceError,
    decide_acceptance,
    get_delivery_pointer,
    list_acceptance_decisions,
    load_manifest_payload,
    resolve_operation_status,
)
from agentforge.delivery.manifest import ManifestError, build_and_publish_manifest
from agentforge.queries.authorization import AuthenticatedPrincipal, api_principal
from agentforge.storage.database import create_database_engine
from agentforge.storage.uow import unit_of_work_connection

router = APIRouter(prefix="/api/v1", tags=["delivery"])


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _require_engine(request: Request, settings: Settings) -> Any:
    engine = getattr(request.app.state, "database", None) or create_database_engine(settings)
    if engine is None:
        raise HTTPException(503, "database_unavailable: Database is not configured")
    return engine


def _principal(request: Request) -> AuthenticatedPrincipal:
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, AuthenticatedPrincipal):
        return principal
    return api_principal()


def _store(settings: Settings) -> ArtifactStore:
    return ArtifactStore(settings.state_dir / "artifact_store")


def _map_acceptance(exc: AcceptanceError) -> HTTPException:
    status = {
        "run_not_found": 404,
        "operation_not_found": 404,
        "unauthorized": 401,
        "forbidden": 403,
        "manifest_stale": 409,
        "delivery_revision_conflict": 409,
        "idempotency_conflict": 409,
        "acceptance_scope_invalid": 400,
        "required_check_not_passed": 409,
        "content_integrity_failed": 409,
        "invalid_request": 400,
    }.get(exc.code, 400)
    return HTTPException(status, f"{exc.code}: {exc.message}")


def _parse_delivery_if_match(value: str | None) -> int:
    if not value:
        raise HTTPException(428, "delivery_revision_conflict: If-Match required")
    raw = value.strip().strip('"')
    if not raw.startswith("delivery:"):
        raise HTTPException(400, "delivery_revision_conflict: If-Match must be delivery:<n>")
    try:
        return int(raw.split(":", 1)[1])
    except ValueError as exc:
        raise HTTPException(400, "delivery_revision_conflict: invalid delivery revision") from exc


@router.get("/runs/{run_id}/delivery", response_model=DeliveryPointerView)
def get_delivery(
    request: Request,
    run_id: str,
    settings: Annotated[Settings, Depends(_settings)],
    response: Response,
) -> DeliveryPointerView:
    engine = _require_engine(request, settings)
    with unit_of_work_connection(engine) as uow:
        view = get_delivery_pointer(uow.session, run_id=run_id)
    response.headers["ETag"] = f'"{view.etag}"'
    return view


@router.get(
    "/runs/{run_id}/delivery-manifests/{manifest_hash}",
    response_model=DeliveryManifestView,
)
def get_manifest(
    request: Request,
    run_id: str,
    manifest_hash: str,
    settings: Annotated[Settings, Depends(_settings)],
    response: Response,
) -> DeliveryManifestView:
    engine = _require_engine(request, settings)
    store = _store(settings)
    try:
        with unit_of_work_connection(engine) as uow:
            row, payload = load_manifest_payload(
                uow.session, store, manifest_hash=manifest_hash
            )
            if row.run_id != run_id:
                raise AcceptanceError("manifest_stale", "manifest not in this run")
            view = DeliveryManifestView.model_validate(
                {
                    "manifestId": row.id,
                    "runId": row.run_id,
                    "versionId": row.version_id,
                    "cycleId": row.cycle_id,
                    "revision": row.revision,
                    "manifestHash": row.manifest_hash,
                    "payload": payload.model_dump(mode="json", by_alias=True),
                    "technicalStatus": row.technical_status,
                    "createdAt": row.created_at,
                    "etag": row.manifest_hash,
                }
            )
    except AcceptanceError as exc:
        raise _map_acceptance(exc) from exc
    response.headers["ETag"] = f'"{view.etag}"'
    return view


@router.post(
    "/runs/{run_id}/delivery-manifests",
    response_model=DeliveryManifestView,
    status_code=201,
)
def publish_delivery_manifest(
    request: Request,
    run_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> DeliveryManifestView:
    """Publish immutable manifest for current version+cycle query params."""
    version_id = request.query_params.get("versionId") or request.query_params.get("version_id")
    cycle_id = request.query_params.get("cycleId") or request.query_params.get("cycle_id")
    if not version_id or not cycle_id:
        raise HTTPException(400, "invalid_request: versionId and cycleId required")
    engine = _require_engine(request, settings)
    store = _store(settings)
    try:
        return build_and_publish_manifest(
            engine, store, version_id=version_id, cycle_id=cycle_id
        )
    except ManifestError as exc:
        status = {
            "version_not_found": 404,
            "cycle_not_found": 404,
            "run_not_found": 404,
            "manifest_stale": 409,
            "content_integrity_failed": 409,
        }.get(exc.code, 400)
        raise HTTPException(status, f"{exc.code}: {exc.message}") from exc


@router.post("/runs/{run_id}/acceptance", response_model=AcceptanceResult, status_code=201)
def post_acceptance(
    request: Request,
    run_id: str,
    body: AcceptanceDecisionRequest,
    settings: Annotated[Settings, Depends(_settings)],
) -> AcceptanceResult:
    """User acceptance — ordinary bearer only; integration-review tickets cannot call this."""
    ticket = request.headers.get("X-Agent-Ticket") or ""
    if ticket:
        raise HTTPException(
            403, "forbidden: internal reviewer tickets cannot call user acceptance"
        )
    idem = request.headers.get("Idempotency-Key") or ""
    if not idem:
        raise HTTPException(400, "invalid_request: Idempotency-Key required")
    revision = _parse_delivery_if_match(request.headers.get("If-Match"))
    engine = _require_engine(request, settings)
    store = _store(settings)
    try:
        return decide_acceptance(
            engine,
            store,
            principal=_principal(request),
            run_id=run_id,
            expected_delivery_revision=revision,
            idempotency_key=idem,
            body=body,
        )
    except AcceptanceError as exc:
        raise _map_acceptance(exc) from exc


@router.get(
    "/runs/{run_id}/acceptance-decisions",
    response_model=AcceptanceDecisionListResponse,
)
def get_acceptance_history(
    request: Request,
    run_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> AcceptanceDecisionListResponse:
    engine = _require_engine(request, settings)
    with unit_of_work_connection(engine) as uow:
        items = list_acceptance_decisions(uow.session, run_id=run_id)
    return AcceptanceDecisionListResponse(items=items)


@router.get("/operations/{operation_id}")
def get_operation(
    request: Request,
    operation_id: str,
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, Any]:
    engine = _require_engine(request, settings)
    try:
        with unit_of_work_connection(engine) as uow:
            return resolve_operation_status(uow.session, operation_id=operation_id)
    except AcceptanceError as exc:
        raise _map_acceptance(exc) from exc
