"""Application factory and authenticated control API (BE-03 lifecycle aware)."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint

from agentforge import __version__
from agentforge.contracts.errors import ErrorCode, ErrorResponse, build_error
from agentforge.contracts.system import (
    CapabilityResponse,
    DependencyStatus,
    HealthResponse,
    ReadinessResponse,
    ShutdownRequest,
    ShutdownResponse,
)
from agentforge.api.approvals import router as approvals_router
from agentforge.api.artifacts import router as artifacts_router
from agentforge.api.discovery import router as discovery_router
from agentforge.api.git import router as git_router
from agentforge.api.projects import router as projects_router
from agentforge.api.providers import router as providers_router
from agentforge.api.runner import router as runner_router
from agentforge.api.instances import router as instances_router
from agentforge.api.runs import router as runs_router
from agentforge.core.config import Settings
from agentforge.core.credentials import CredentialError, CredentialVerifier
from agentforge.runtime.components import component_status_snapshot
from agentforge.runtime.control import admits_new_work, read_admission, write_admission
from agentforge.runtime.drain import collect_drain_coordination
from agentforge.runtime.instance import read_runtime_record, write_runtime_record
from agentforge.storage.database import check_database, create_database_engine

bearer = HTTPBearer(auto_error=False)


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    envelope = build_error(code=code, message=message, request_id=request.state.request_id)
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(envelope.model_dump(mode="json"), status_code=status, headers=headers)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.settings = config
        application.state.verifier = CredentialVerifier(config)
        application.state.database = create_database_engine(config)
        try:
            yield
        finally:
            if application.state.database is not None:
                application.state.database.dispose()

    app = FastAPI(
        title="AgentForge",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 405, 422, 500)},
    )

    @app.middleware("http")
    async def request_boundary(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = uuid.uuid4().hex
        response: Response
        host = request.url.hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            response = error_response(request, 403, ErrorCode.HOST_REJECTED, "Host is not allowed")
        elif request.headers.get("origin") is not None:
            response = error_response(
                request, 403, ErrorCode.ORIGIN_REJECTED, "Origin is not allowed"
            )
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    def authenticate(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        if (
            credentials is None
            or len(credentials.credentials) > 512
            or not credentials.credentials.isascii()
        ):
            raise HTTPException(401, "Authentication required")
        try:
            app.state.verifier.verify(credentials.credentials)
        except CredentialError:
            raise HTTPException(401, "Authentication required") from None

    def database_status() -> DependencyStatus:
        return check_database(
            app.state.database, expected_revision=config.expected_alembic_revision
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = {
            401: ErrorCode.UNAUTHORIZED,
            403: ErrorCode.FORBIDDEN,
            404: ErrorCode.NOT_FOUND,
            405: ErrorCode.METHOD_NOT_ALLOWED,
        }.get(exc.status_code, ErrorCode.HTTP_ERROR)
        return error_response(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        return error_response(request, 500, ErrorCode.INTERNAL_ERROR, "Internal server error")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(request, 422, ErrorCode.INVALID_REQUEST, "Request validation failed")

    @app.get("/api/v1/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get(
        "/api/v1/ready",
        response_model=ReadinessResponse,
        dependencies=[Depends(authenticate)],
        responses={503: {"model": ReadinessResponse}},
    )
    def ready(response: Response) -> ReadinessResponse:
        admission = read_admission(config)
        database = database_status()
        reasons: list[str] = []
        accepting = admits_new_work(admission)
        if admission == "drain":
            reasons.append("admission=drain; status queries remain available")
        elif admission == "blocked":
            reasons.append(
                "admission=blocked; control file unreadable or corrupt — new work forbidden"
            )
        if database.status != "available":
            accepting = False
            reasons.append(database.message)
        if config.run_mode in {"local", "hosted"}:
            missing = [
                row["id"]
                for row in component_status_snapshot(config.run_mode)
                if row["state"] == "not_implemented"
                and row["id"] in {"graph-driver", "queue-worker", "runner"}
            ]
            if missing:
                accepting = False
                reasons.append(
                    "required components not implemented: "
                    + ", ".join(missing)
                    + "; API up is not full stack readiness"
                )
        # health proves process liveness; ready means mode can accept corresponding work.
        ready_status = accepting
        if not ready_status:
            response.status_code = 503
        return ReadinessResponse(
            status="ready" if ready_status else "not_ready",
            run_mode=config.run_mode,
            admission=admission,
            accepting_work=accepting,
            database=database,
            reason="; ".join(reasons) if reasons else None,
        )

    @app.get(
        "/api/v1/capabilities",
        response_model=CapabilityResponse,
        dependencies=[Depends(authenticate)],
    )
    def capabilities() -> CapabilityResponse:
        def executable(name: str) -> DependencyStatus:
            found = shutil.which(name) is not None
            return DependencyStatus(
                status="available" if found else "unavailable",
                message=f"{name} executable {'found' if found else 'not found'}; runtime not probed",
            )

        return CapabilityResponse(
            version=__version__,
            run_mode=config.run_mode,
            admission=read_admission(config),
            git=executable("git"),
            docker=executable("docker"),
            database=database_status(),
            components=component_status_snapshot(config.run_mode),
        )

    @app.post(
        "/api/v1/control/shutdown",
        response_model=ShutdownResponse,
        dependencies=[Depends(authenticate)],
    )
    def shutdown(body: ShutdownRequest) -> ShutdownResponse | JSONResponse:
        """Drain admission, verify agent stop coordination, then exit the API process.

        Incomplete/unknown agent coordination returns 409 and does **not** invoke the
        exit callback unless ``force`` is true. ``platformOnly`` allows API-only stop
        when the database is unconfigured (pure dev); it cannot skip pending agents.
        """
        record = read_runtime_record(config)
        if record is None or record.instance_id != body.instance_id:
            raise HTTPException(409, "Instance identity mismatch")
        write_admission(config, "drain")
        record.stage = "draining"
        write_runtime_record(config, record)

        coordination = collect_drain_coordination(config)
        verification = str(coordination["verification"])
        drain_complete = bool(coordination["drainComplete"])
        if body.platform_only and config.database_url is None and verification == "unknown":
            drain_complete = True
            verification = "platform_only"
            message = "Platform-only stop: database unconfigured; agent coordination skipped"
        else:
            message = str(coordination["message"])

        if not drain_complete and not body.force:
            return JSONResponse(
                status_code=409,
                content=ShutdownResponse(
                    status="drain_incomplete",
                    instance_id=body.instance_id,
                    drain_complete=False,
                    verification=verification,  # type: ignore[arg-type]
                    pending_agent_instances=list(coordination["pendingAgentInstances"]),
                    unconfirmed_side_effects=list(coordination["unconfirmedSideEffects"]),
                    message=message,
                ).model_dump(mode="json"),
            )

        request_shutdown = getattr(app.state, "request_shutdown", None)
        if callable(request_shutdown):
            request_shutdown()
        return ShutdownResponse(
            instance_id=body.instance_id,
            status="stopping",
            drain_complete=drain_complete,
            verification=verification,  # type: ignore[arg-type]
            pending_agent_instances=list(coordination["pendingAgentInstances"]),
            unconfirmed_side_effects=list(coordination["unconfirmedSideEffects"]),
            message=message if drain_complete else f"Forced stop with incomplete drain: {message}",
        )

    @app.get("/api/v1/openapi.json", include_in_schema=False, dependencies=[Depends(authenticate)])
    def openapi() -> dict[str, Any]:
        return app.openapi()

    app.include_router(projects_router, dependencies=[Depends(authenticate)])
    app.include_router(runs_router, dependencies=[Depends(authenticate)])
    app.include_router(instances_router, dependencies=[Depends(authenticate)])
    app.include_router(providers_router, dependencies=[Depends(authenticate)])
    app.include_router(discovery_router, dependencies=[Depends(authenticate)])
    app.include_router(approvals_router, dependencies=[Depends(authenticate)])
    app.include_router(runner_router, dependencies=[Depends(authenticate)])
    app.include_router(artifacts_router, dependencies=[Depends(authenticate)])
    app.include_router(git_router, dependencies=[Depends(authenticate)])
    return app
