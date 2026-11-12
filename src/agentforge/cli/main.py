"""Typer CLI: bootstrap, lifecycle, auth, diagnostics (BE-03)."""

from __future__ import annotations

import json
import signal
import time
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
import uvicorn
from pydantic import ValidationError

from agentforge import __version__
from agentforge.api.app import create_app
from agentforge.core.config import Settings
from agentforge.core.credentials import (
    CredentialError,
    initialize_token,
    list_credential_status,
    read_token,
    revoke_credential,
    rotate_token,
)
from agentforge.core.exit_codes import (
    AUTH_FAILURE,
    CONFIG_ERROR,
    DEPENDENCY_UNAVAILABLE,
    FAILURE,
    OK,
    TIMEOUT,
)
from agentforge.observability.logging import configure_logging
from agentforge.runtime.components import component_status_snapshot
from agentforge.runtime.control import admits_new_work, read_admission, write_admission
from agentforge.runtime.doctor import run_doctor
from agentforge.runtime.drain import collect_drain_coordination
from agentforge.runtime.instance import (
    InstanceError,
    InstanceLock,
    PlatformInstance,
    clear_runtime_record,
    force_terminate_process,
    matches_managed_process,
    read_runtime_record,
    write_runtime_record,
)

app = typer.Typer(no_args_is_help=True, help="AgentForge Python backend")
auth_app = typer.Typer(help="Local credential lifecycle")
grant_app = typer.Typer(help="Local directory authorization (BE-04)")
project_app = typer.Typer(help="Project registration helpers (BE-04)")
provider_app = typer.Typer(help="Model provider / sync calls (D01)")
queue_app = typer.Typer(help="Outbox dispatcher / queue worker (D02)")
agent_app = typer.Typer(help="Agent product discovery / profiles (D03)")
approval_app = typer.Typer(help="Action authorization / native approval (D04)")
runner_app = typer.Typer(help="Controlled Runner operations (D05)")
artifact_app = typer.Typer(help="Artifact bus freeze/verify/publish (D06)")
git_app = typer.Typer(help="Git worktrees / snapshots / integrate (D07)")
app.add_typer(auth_app, name="auth")
app.add_typer(grant_app, name="grant")
app.add_typer(project_app, name="project")
app.add_typer(provider_app, name="provider")
app.add_typer(queue_app, name="queue")
app.add_typer(agent_app, name="agent")
app.add_typer(approval_app, name="approval")
app.add_typer(runner_app, name="runner")
app.add_typer(artifact_app, name="artifact")
app.add_typer(git_app, name="git")


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError:
        typer.echo(
            "Invalid configuration; check environment variables and .env (values redacted).",
            err=True,
        )
        raise typer.Exit(CONFIG_ERROR) from None


def emit(payload: Any, *, as_json: bool, human: str | None = None) -> None:
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    elif human is not None:
        typer.echo(human)
    else:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command()
def version(json_out: bool = typer.Option(False, "--json", help="Machine-readable output")) -> None:
    """Print application and protocol versions."""
    payload = {"version": __version__, "schemaVersion": "1", "protocol": "api/v1"}
    emit(payload, as_json=json_out, human=__version__)


@app.command()
def init(json_out: bool = typer.Option(False, "--json")) -> None:
    """Initialize local state directory and identity; does not reset existing credentials."""
    settings = load_settings()
    try:
        created = initialize_token(settings)
    except CredentialError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(FAILURE) from None
    payload = {
        "created": created,
        "stateDir": str(settings.state_dir),
        "message": (
            "Local credentials initialized." if created else "Local credentials already configured."
        ),
    }
    emit(payload, as_json=json_out, human=str(payload["message"]))


@app.command()
def start(
    json_out: bool = typer.Option(False, "--json"),
    mode: str | None = typer.Option(None, "--mode", help="dev|local|hosted"),
) -> None:
    """Acquire instance lock and start the API in the foreground."""
    settings = load_settings()
    if mode is not None:
        if mode not in {"dev", "local", "hosted"}:
            typer.echo("Invalid --mode; use dev, local, or hosted", err=True)
            raise typer.Exit(CONFIG_ERROR)
        object.__setattr__(settings, "run_mode", mode)
    if settings.run_mode == "hosted":
        typer.echo("hosted mode is managed by OPS-01; use deployment tooling", err=True)
        raise typer.Exit(CONFIG_ERROR)
    if settings.run_mode == "local":
        missing = [
            row["id"]
            for row in component_status_snapshot("local")
            if row["state"] == "not_implemented"
            and row["id"] in {"graph-driver", "queue-worker", "runner"}
        ]
        typer.echo(
            "local mode components not implemented yet: "
            + ", ".join(missing)
            + "; starting control API only is not full-stack readiness",
            err=True,
        )
    try:
        initialize_token(settings)
        read_token(settings)
    except CredentialError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(AUTH_FAILURE) from None

    lock = InstanceLock(settings)
    try:
        lock.acquire()
    except InstanceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from None

    write_admission(settings, "open")
    record = PlatformInstance(
        instance_id=lock.instance_id,
        pid=__import__("os").getpid(),
        started_at=lock.started_at,
        host=settings.host,
        port=settings.port,
        run_mode=settings.run_mode,
        stage="starting",
        version=__version__,
        components=component_status_snapshot(settings.run_mode),
        process_key=lock.process_key,
    )
    write_runtime_record(settings, record)
    configure_logging(settings.log_level)
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    if json_out:
        emit(
            {
                "instanceId": record.instance_id,
                "baseUrl": settings.base_url,
                "runMode": settings.run_mode,
                "stage": "starting",
            },
            as_json=True,
        )
    else:
        typer.echo(f"AgentForge API: {settings.base_url}/api/v1/health", err=True)

    api = create_app(settings)
    server = uvicorn.Server(
        uvicorn.Config(
            api,
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
            access_log=False,
            proxy_headers=False,
        )
    )

    def _request_shutdown() -> None:
        write_admission(settings, "drain")
        record.stage = "draining"
        write_runtime_record(settings, record)
        server.should_exit = True

    api.state.request_shutdown = _request_shutdown

    def _on_signal(signum: int, frame: Any) -> None:  # noqa: ARG001
        _request_shutdown()

    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    record.stage = "running"
    write_runtime_record(settings, record)
    try:
        server.run()
    finally:
        record.stage = "stopped"
        write_runtime_record(settings, record)
        lock.release()
        clear_runtime_record(settings)
        write_admission(settings, "open")


@app.command()
def status(json_out: bool = typer.Option(False, "--json")) -> None:
    """Show instance and component status without starting services."""
    settings = load_settings()
    record = read_runtime_record(settings)
    admission = read_admission(settings)
    if record is None or not matches_managed_process(record):
        payload = {
            "state": "stopped",
            "admission": admission,
            "runMode": settings.run_mode,
            "components": component_status_snapshot(settings.run_mode),
            "message": "No live managed API instance",
        }
        emit(payload, as_json=json_out)
        raise typer.Exit(OK)

    try:
        token = read_token(settings)
        with httpx.Client(base_url=settings.base_url, timeout=8, trust_env=False) as client:
            response = client.get(
                "/api/v1/capabilities", headers={"Authorization": f"Bearer {token}"}
            )
            response.raise_for_status()
            capabilities = response.json()
    except CredentialError:
        typer.echo("Authentication failed while querying status", err=True)
        raise typer.Exit(AUTH_FAILURE) from None
    except httpx.HTTPError:
        unknown: dict[str, Any] = {
            "state": "unknown",
            "instanceId": record.instance_id,
            "pid": record.pid,
            "admission": admission,
            "message": "Runtime record exists but API could not be queried",
        }
        emit(unknown, as_json=json_out)
        raise typer.Exit(FAILURE) from None

    running: dict[str, Any] = {
        "state": "running",
        "instanceId": record.instance_id,
        "pid": record.pid,
        "startedAt": record.started_at,
        "admission": admission,
        "acceptingWork": admits_new_work(admission),
        "runMode": settings.run_mode,
        "capabilities": capabilities,
    }
    emit(running, as_json=json_out)


@app.command()
def doctor(
    require_database: bool = typer.Option(False, "--require-database"),
    level: str = typer.Option("default", "--level", help="default|local"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Diagnose configuration; never auto-repair, install, call models, or start services."""
    if level not in {"default", "local"}:
        typer.echo("Invalid --level", err=True)
        raise typer.Exit(CONFIG_ERROR)
    settings = load_settings()
    report = run_doctor(settings, require_database=require_database, level=level)  # type: ignore[arg-type]
    emit(report, as_json=True if json_out else True)
    if report["requiredFailed"]:
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)


@app.command()
def drain(
    json_out: bool = typer.Option(False, "--json"),
    timeout_seconds: float = typer.Option(15.0, "--timeout"),
) -> None:
    """Stop accepting new platform work and report agent drain coordination.

    Writing the admission flag alone is **not** proof that all agent work stopped.
    """
    settings = load_settings()
    record = read_runtime_record(settings)
    if record is None or not matches_managed_process(record):
        typer.echo("No running managed instance to drain", err=True)
        raise typer.Exit(FAILURE)
    payload = write_admission(settings, "drain")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if read_admission(settings) == "drain":
            coordination = collect_drain_coordination(settings)
            emit(
                {
                    "admission": "drain",
                    "updatedAt": payload["updatedAt"],
                    "acceptingWork": False,
                    "platformAdmissionSet": True,
                    "drainComplete": coordination["drainComplete"],
                    "verification": coordination["verification"],
                    "pendingAgentInstances": coordination["pendingAgentInstances"],
                    "unconfirmedSideEffects": coordination["unconfirmedSideEffects"],
                    "message": coordination["message"],
                },
                as_json=json_out,
            )
            # Admission flag set is not proof agents stopped.
            raise typer.Exit(OK if coordination["drainComplete"] else FAILURE)
        time.sleep(0.05)
    typer.echo("Drain acknowledgment timed out", err=True)
    raise typer.Exit(TIMEOUT)


@app.command()
def stop(
    force: bool = typer.Option(False, "--force"),
    timeout_seconds: float = typer.Option(20.0, "--timeout"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Drain then stop the managed API process via local control API."""
    settings = load_settings()
    record = read_runtime_record(settings)
    if record is None:
        emit({"state": "stopped", "message": "No runtime record"}, as_json=json_out)
        raise typer.Exit(OK)
    if not matches_managed_process(record):
        clear_runtime_record(settings)
        emit(
            {"state": "stopped", "message": "Stale runtime record cleared; no process stopped"},
            as_json=json_out,
        )
        raise typer.Exit(OK)

    write_admission(settings, "drain")
    try:
        token = read_token(settings)
        with httpx.Client(base_url=settings.base_url, timeout=5.0, trust_env=False) as client:
            response = client.post(
                "/api/v1/control/shutdown",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "instanceId": record.instance_id,
                    "force": force,
                    "platformOnly": settings.database_url is None,
                },
            )
        if response.status_code == 409:
            body = response.json()
            emit(
                {
                    "state": "drain_incomplete",
                    "instanceId": record.instance_id,
                    "shutdown": body,
                    "message": body.get("message")
                    or "Agent drain coordination incomplete; not stopping API",
                },
                as_json=json_out,
            )
            if not force:
                raise typer.Exit(FAILURE)
        elif response.status_code != 200:
            typer.echo(
                f"Graceful shutdown request failed: HTTP {response.status_code}",
                err=True,
            )
            if not force:
                raise typer.Exit(FAILURE)
    except CredentialError:
        typer.echo("Authentication failed while requesting shutdown", err=True)
        if not force:
            raise typer.Exit(AUTH_FAILURE) from None
    except httpx.HTTPError as exc:
        typer.echo(f"Could not reach managed API for shutdown: {type(exc).__name__}", err=True)
        if not force:
            raise typer.Exit(FAILURE) from None

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = read_runtime_record(settings)
        if current is None or not matches_managed_process(current):
            # Process gone or record stale — always clear leftover runtime files.
            clear_runtime_record(settings)
            emit({"state": "stopped", "instanceId": record.instance_id}, as_json=json_out)
            raise typer.Exit(OK)
        time.sleep(0.1)

    if force:
        force_terminate_process(record)
        time.sleep(0.5)
        if matches_managed_process(record):
            typer.echo(
                json.dumps(
                    {
                        "state": "force_stop_incomplete",
                        "instanceId": record.instance_id,
                        "unconfirmed": ["process_still_alive"],
                    }
                ),
                err=True,
            )
            raise typer.Exit(TIMEOUT)
        clear_runtime_record(settings)
        emit(
            {"state": "stopped", "forced": True, "instanceId": record.instance_id},
            as_json=json_out,
        )
        raise typer.Exit(OK)

    typer.echo(
        json.dumps(
            {
                "state": "stop_timeout",
                "instanceId": record.instance_id,
                "unconfirmed": ["process_exit"],
            }
        ),
        err=True,
    )
    raise typer.Exit(TIMEOUT)


@app.command()
def logs(
    tail: int = typer.Option(100, "--tail", min=1, max=5000),
    follow: bool = typer.Option(False, "--follow"),
) -> None:
    """Read current instance log file if present."""
    settings = load_settings()
    path = settings.log_path
    if not path.exists():
        typer.echo("No log file yet", err=True)
        raise typer.Exit(FAILURE)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-tail:]:
        typer.echo(line)
    if follow:
        typer.echo("--follow is best-effort; reopen logs after rotation", err=True)


@app.command("export-contracts")
def export_contracts(output: Path = Path("schemas")) -> None:
    """Export OpenAPI and contract JSON Schemas without reading credentials."""
    from agentforge.contracts.export import export_contract_documents

    count = export_contract_documents(output)
    typer.echo(f"Exported {count} contracts.")


@auth_app.command("status")
def auth_status(json_out: bool = typer.Option(False, "--json")) -> None:
    """Show credential ids and expiry; never print the token."""
    settings = load_settings()
    try:
        payload = list_credential_status(settings)
    except CredentialError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(AUTH_FAILURE) from None
    emit(payload, as_json=json_out)


@auth_app.command("rotate")
def auth_rotate(json_out: bool = typer.Option(False, "--json")) -> None:
    """Rotate local credentials; running API reloads verification records."""
    settings = load_settings()
    try:
        result = rotate_token(settings)
    except CredentialError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(AUTH_FAILURE) from None
    emit(
        {
            "rotated": True,
            "credentialId": result["credentialId"],
            "version": result["version"],
            "revokedCredentialId": result["revokedCredentialId"],
        },
        as_json=json_out,
        human="Credentials rotated; token value not printed.",
    )


@auth_app.command("revoke")
def auth_revoke(
    credential_id: str = typer.Argument(..., help="Credential id to revoke"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Revoke a credential id; does not mint a replacement."""
    settings = load_settings()
    try:
        revoke_credential(settings, credential_id)
    except CredentialError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(AUTH_FAILURE) from None
    emit({"revoked": credential_id}, as_json=json_out, human=f"Revoked {credential_id}")


@grant_app.command("create")
def grant_create(
    root_path: Annotated[
        Path, typer.Argument(exists=True, file_okay=False, dir_okay=True, readable=True)
    ],
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Create a directory grant from an explicit local path confirmation (not via public API)."""
    from agentforge.contracts.grants import DirectoryGrantCreateRequest
    from agentforge.projects.grants import GrantError, create_directory_grant
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    try:
        with unit_of_work_connection(engine) as uow:
            grant = create_directory_grant(
                uow.session,
                DirectoryGrantCreateRequest.model_validate({"rootPath": str(root_path.resolve())}),
            )
            payload = grant.model_dump(mode="json", by_alias=True)
    except GrantError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(payload, as_json=json_out, human=f"Grant {payload.get('grantId')} for {root_path}")


@grant_app.command("revoke")
def grant_revoke(
    grant_id: str = typer.Argument(..., help="Directory grant id"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Revoke a directory grant and mark dependent projects."""
    from agentforge.projects.grants import GrantError, revoke_directory_grant
    from agentforge.projects.service import ProjectService
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    try:
        with unit_of_work_connection(engine) as uow:
            grant = revoke_directory_grant(uow.session, grant_id)
            affected = ProjectService(uow.session, settings).on_grant_revoked(grant_id)
            payload = {
                **grant.model_dump(mode="json", by_alias=True),
                "affectedProjects": affected,
                "note": (
                    "New reads and dispatch are blocked; already-running external agents "
                    "are not claimed stopped by this update"
                ),
            }
    except GrantError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(payload, as_json=json_out, human=f"Revoked {grant_id}; projects={affected}")


@project_app.command("register")
def project_register(
    grant_id: str = typer.Option(..., "--grant-id", help="Active directory grant id"),
    display_name: str | None = typer.Option(None, "--name"),
    dirty_policy: str = typer.Option("committed_only", "--dirty-policy"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Register a project from a valid grant (same rules as POST /projects)."""
    from agentforge.contracts.projects import ProjectCreateRequest
    from agentforge.projects.service import ProjectError, ProjectService
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    try:
        with unit_of_work_connection(engine) as uow:
            body: dict[str, object] = {
                "directoryGrantId": grant_id,
                "dirtyPolicy": dirty_policy,
            }
            if display_name is not None:
                body["displayName"] = display_name
            result = ProjectService(uow.session, settings).register(
                ProjectCreateRequest.model_validate(body)
            )
            payload = result.model_dump(mode="json", by_alias=True)
    except ProjectError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(payload, as_json=json_out, human=f"Project {payload.get('id')}")


@provider_app.command("credential")
def provider_credential(
    secret: str | None = typer.Option(
        None,
        "--secret",
        help="Deprecated: leaks into shell history. Prefer prompt or --stdin.",
        hidden=True,
    ),
    from_stdin: bool = typer.Option(False, "--stdin", help="Read secret from stdin"),
    label: str | None = typer.Option(None, "--label"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Register a model credential ref; secret never printed back (F09)."""
    import sys

    from agentforge.models.credentials import ModelCredentialStore

    settings = load_settings()
    if secret:
        typer.echo(
            "Warning: --secret is deprecated and may enter shell history; use prompt or --stdin.",
            err=True,
        )
        value = secret
    elif from_stdin:
        value = sys.stdin.read().strip()
    else:
        value = typer.prompt("Model API secret", hide_input=True)
    if not value:
        typer.echo("Empty secret rejected", err=True)
        raise typer.Exit(FAILURE)
    try:
        meta = ModelCredentialStore(settings).register(value, label=label)
    except Exception as exc:  # noqa: BLE001
        typer.echo(str(exc), err=True)
        raise typer.Exit(FAILURE) from None
    emit(meta, as_json=json_out, human=f"credentialRef={meta['credentialRef']}")


@provider_app.command("create")
def provider_create(
    adapter_type: str = typer.Option(..., "--adapter-type"),
    base_url: str = typer.Option(..., "--base-url"),
    model_id: str = typer.Option(..., "--model-id"),
    auth_mode: str = typer.Option("none", "--auth-mode"),
    credential_ref: str | None = typer.Option(None, "--credential-ref"),
    label: str | None = typer.Option(None, "--label"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Create a provider profile (does not call the model)."""
    from agentforge.contracts.providers import ProviderCreateRequest
    from agentforge.models.gateway import ModelGateway, ModelGatewayError
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    body: dict[str, object] = {
        "adapterType": adapter_type,
        "baseUrl": base_url,
        "modelId": model_id,
        "authMode": auth_mode,
    }
    if credential_ref:
        body["credentialRef"] = credential_ref
    if label:
        body["label"] = label
    try:
        with unit_of_work_connection(engine) as uow:
            result = ModelGateway(uow.session, settings).create_provider(
                ProviderCreateRequest.model_validate(body)
            )
            payload = result.model_dump(mode="json", by_alias=True)
    except ModelGatewayError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(payload, as_json=json_out, human=f"Provider {payload.get('providerId')}")


@provider_app.command("grant")
def provider_grant(
    provider_id: str = typer.Option(..., "--provider-id"),
    purpose: str = typer.Option(..., "--purpose"),
    max_calls: int = typer.Option(3, "--max-calls"),
    budget_account_id: str | None = typer.Option(None, "--budget-account-id"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Issue a model authorization grant for sync calls/probes."""
    from agentforge.contracts.providers import ModelAuthorizationGrantCreateRequest
    from agentforge.models.gateway import ModelGateway, ModelGatewayError
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    body: dict[str, object] = {
        "providerId": provider_id,
        "purpose": purpose,
        "maxCalls": max_calls,
    }
    if budget_account_id:
        body["budgetAccountId"] = budget_account_id
    try:
        with unit_of_work_connection(engine) as uow:
            result = ModelGateway(uow.session, settings).create_grant(
                ModelAuthorizationGrantCreateRequest.model_validate(body)
            )
            payload = result.model_dump(mode="json", by_alias=True)
    except ModelGatewayError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(payload, as_json=json_out, human=f"Grant {payload.get('grantId')}")


@provider_app.command("call")
def provider_call(
    provider_id: str = typer.Option(..., "--provider-id"),
    grant_id: str = typer.Option(..., "--grant-id"),
    purpose: str = typer.Option(..., "--purpose"),
    message: str = typer.Option(..., "--message"),
    idempotency_key: str = typer.Option(..., "--idempotency-key"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Synchronous controlled model call (consumes grant/budget)."""
    from agentforge.contracts.providers import ModelCallCreateRequest
    from agentforge.models.gateway import ModelGateway, ModelGatewayError
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    body = {
        "providerId": provider_id,
        "grantId": grant_id,
        "purpose": purpose,
        "messages": [{"role": "user", "content": message}],
        "idempotencyKey": idempotency_key,
    }
    try:
        with unit_of_work_connection(engine) as uow:
            result = ModelGateway(uow.session, settings).invoke(
                ModelCallCreateRequest.model_validate(body)
            )
            payload = result.model_dump(mode="json", by_alias=True)
    except ModelGatewayError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(
        payload,
        as_json=json_out,
        human=f"Call {payload.get('callId')} status={payload.get('status')}",
    )


@queue_app.command("dispatch")
def queue_dispatch(
    limit: int = typer.Option(20, "--limit", min=1, max=200),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Claim due outbox rows, enqueue Procrastinate jobs, confirm tokens."""
    from agentforge.queue.dispatcher import dispatch_once

    settings = load_settings()
    try:
        settings.require_queue_database_url()
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(CONFIG_ERROR) from None
    try:
        result = dispatch_once(settings, limit=limit)
    except Exception as exc:  # noqa: BLE001
        typer.echo(str(exc), err=True)
        raise typer.Exit(FAILURE) from None
    emit(
        result,
        as_json=json_out,
        human=(
            f"claimed={result['claimed']} enqueued={result['enqueued']} failed={result['failed']}"
        ),
    )


@queue_app.command("work")
def queue_work(
    once: bool = typer.Option(False, "--once", help="Process available jobs then exit"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run the model-operation worker (uses API/driver DB credentials)."""
    from agentforge.queue.app import open_app
    from agentforge.queue.constants import DEFAULT_QUEUE_NAME

    settings = load_settings()
    try:
        settings.require_queue_database_url()
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(CONFIG_ERROR) from None
    try:
        app_q = open_app(settings)
    except Exception as exc:  # noqa: BLE001
        typer.echo(str(exc), err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE) from None
    try:
        if once:
            # Single fetch cycle — suitable for supervised ticks / tests.
            app_q.run_worker(queues=[DEFAULT_QUEUE_NAME], wait=False, install_signal_handlers=False)
        else:
            app_q.run_worker(queues=[DEFAULT_QUEUE_NAME])
    finally:
        app_q.close()
    emit({"status": "stopped"}, as_json=json_out, human="queue worker stopped")


@queue_app.command("replay")
def queue_replay(
    operation_id: str = typer.Argument(..., help="Async operation id"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Manual replay when ledger proves no uncertain side effects."""
    from agentforge.queue.operations import OperationError, get_operation, request_manual_replay
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    try:
        with unit_of_work_connection(engine) as uow:
            request_manual_replay(uow.session, operation_id)
            payload = get_operation(uow.session, operation_id).model_dump(
                mode="json", by_alias=True
            )
    except OperationError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(payload, as_json=json_out, human=f"Replay accepted for {operation_id}")


@agent_app.command("discover")
def agent_discover(
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Passive discover candidate agent installs; does not start sessions."""
    from agentforge.discovery.registry import register_installation
    from agentforge.discovery.service import AgentDiscoveryService
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    settings = load_settings()
    found = AgentDiscoveryService().discover()
    items = []
    if persist:
        engine = create_database_engine(settings)
        if engine is None:
            typer.echo("Database is not configured", err=True)
            raise typer.Exit(DEPENDENCY_UNAVAILABLE)
        with unit_of_work_connection(engine) as uow:
            for row in found:
                inst = register_installation(uow.session, row)
                payload = row.to_dict()
                payload["installationId"] = inst.installation_id
                items.append(payload)
    else:
        items = [row.to_dict() for row in found]
    emit(
        {"items": items, "count": len(items)},
        as_json=json_out,
        human=f"discovered={len(items)} (discovery≠schedulable)",
    )


@agent_app.command("profile")
def agent_profile(
    installation_id: str = typer.Option(..., "--installation-id"),
    adapter_id: str = typer.Option(..., "--adapter-id"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Create profile + capability matrix for an installation (honest unknown/unsupported)."""
    from agentforge.discovery.registry import DiscoveryRegistryError, create_profile
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    try:
        with unit_of_work_connection(engine) as uow:
            profile, check = create_profile(
                uow.session, installation_id=installation_id, adapter_id=adapter_id
            )
            payload = {
                "profile": profile.model_dump(mode="json", by_alias=True),
                "capabilityCheck": check.model_dump(mode="json", by_alias=True),
                "accessLevel": profile.access_level,
            }
    except DiscoveryRegistryError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(
        payload,
        as_json=json_out,
        human=f"profile={profile.profile_id} accessLevel={profile.access_level}",
    )


@approval_app.command("list")
def approval_list(
    status: str | None = typer.Option(None, "--status"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List action-authorization ledger rows."""
    from agentforge.permissions.service import list_approvals
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    with unit_of_work_connection(engine) as uow:
        items = list_approvals(uow.session, status=status)
        payload = {
            "items": [i.model_dump(mode="json", by_alias=True) for i in items],
            "count": len(items),
        }
    emit(payload, as_json=json_out, human=f"approvals={len(items)}")


@approval_app.command("decide")
def approval_decide(
    approval_id: str = typer.Option(..., "--id"),
    decision: str = typer.Option(..., "--decision", help="approve|reject"),
    action_digest: str = typer.Option(..., "--action-digest"),
    note: str | None = typer.Option(None, "--note"),
    forward: bool = typer.Option(True, "--forward/--no-forward"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Record user decision; optionally forward to native adapter."""
    from agentforge.permissions.service import ApprovalError, decide
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    if decision not in {"approve", "reject"}:
        typer.echo("decision must be approve or reject", err=True)
        raise typer.Exit(FAILURE)
    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    try:
        with unit_of_work_connection(engine) as uow:
            summary = decide(
                uow.session,
                approval_id=approval_id,
                decision=decision,  # type: ignore[arg-type]
                action_digest=action_digest,
                note=note,
                forward=forward,
            )
            payload = summary.model_dump(mode="json", by_alias=True)
    except ApprovalError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(
        payload,
        as_json=json_out,
        human=(
            f"approval={summary.id} status={summary.status} "
            f"nativeConfirmed={summary.native_confirmed}"
        ),
    )


@approval_app.command("get")
def approval_get(
    approval_id: str = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show one approval ledger row."""
    from agentforge.permissions.service import ApprovalError, get_approval
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    try:
        with unit_of_work_connection(engine) as uow:
            summary = get_approval(uow.session, approval_id)
            payload = summary.model_dump(mode="json", by_alias=True)
    except ApprovalError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(
        payload,
        as_json=json_out,
        human=f"approval={summary.id} status={summary.status}",
    )


@runner_app.command("profiles")
def runner_profiles(json_out: bool = typer.Option(False, "--json")) -> None:
    """List registered Runner profiles (no arbitrary shell entry)."""
    from agentforge.runner.profiles import list_profiles

    items = [
        {
            "profileId": p.profile_id,
            "hostSupported": p.host_supported,
            "dockerRequired": p.docker_required,
            "description": p.description,
        }
        for p in list_profiles()
    ]
    emit({"items": items}, as_json=json_out, human=f"profiles={len(items)}")


@runner_app.command("issue")
def runner_issue(
    profile_id: str = typer.Option(..., "--profile-id"),
    workspace_id: str = typer.Option(..., "--workspace-id"),
    workspace_root: str = typer.Option(..., "--workspace-root"),
    scope: str = typer.Option(..., "--scope"),
    params_json: str = typer.Option("{}", "--params-json"),
    expires_hours: int = typer.Option(1, "--expires-hours"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Issue a Runner ticket (does not execute)."""
    from datetime import UTC, datetime, timedelta

    from agentforge.contracts.runner import RunnerTicketIssueRequest
    from agentforge.runner.service import RunnerError, RunnerService
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    try:
        params = json.loads(params_json)
        body = RunnerTicketIssueRequest.model_validate(
            {
                "profileId": profile_id,
                "workspaceId": workspace_id,
                "workspaceRoot": workspace_root,
                "scope": scope,
                "params": params,
                "expiresAt": datetime.now(UTC) + timedelta(hours=expires_hours),
            }
        )
        with unit_of_work_connection(engine) as uow:
            ticket = RunnerService(uow.session).issue(body)
            payload = ticket.model_dump(mode="json", by_alias=True)
    except (RunnerError, json.JSONDecodeError, Exception) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(FAILURE) from None
    emit(
        payload,
        as_json=json_out,
        human=f"operation={ticket.operation_id} digest={ticket.action_digest[:12]}…",
    )


@runner_app.command("execute")
def runner_execute(
    operation_id: str = typer.Option(..., "--operation-id"),
    action_digest: str = typer.Option(..., "--action-digest"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Execute a previously issued Runner ticket."""
    from agentforge.contracts.runner import RunnerExecuteRequest
    from agentforge.runner.executor import execute_runner
    from agentforge.runner.service import RunnerError
    from agentforge.storage.database import create_database_engine

    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    try:
        ticket = execute_runner(
            engine,
            RunnerExecuteRequest.model_validate(
                {
                    "operationId": operation_id,
                    "actionDigest": action_digest,
                }
            ),
        )
        payload = ticket.model_dump(mode="json", by_alias=True)
    except RunnerError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(
        payload,
        as_json=json_out,
        human=f"operation={ticket.operation_id} status={ticket.status}",
    )


@artifact_app.command("consume-check")
def artifact_consume_check(
    artifact_id: str = typer.Option(..., "--artifact-id"),
    version: int = typer.Option(..., "--version"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Check whether a publication may be used as downstream input (D10 gate required)."""
    from pathlib import Path

    from agentforge.artifacts.service import ArtifactService
    from agentforge.artifacts.store import ArtifactStore
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    with unit_of_work_connection(engine) as uow:
        store = ArtifactStore(Path(settings.state_dir) / "artifact_store")
        result = ArtifactService(uow.session, store).can_consume(artifact_id, version)
        payload = result.model_dump(mode="json", by_alias=True)
    emit(
        payload,
        as_json=json_out,
        human=f"allowed={result.allowed} reason={result.reason}",
    )


def _git_service():
    from pathlib import Path

    from agentforge.artifacts.store import ArtifactStore
    from agentforge.storage.database import create_database_engine
    from agentforge.storage.uow import unit_of_work_connection

    settings = load_settings()
    engine = create_database_engine(settings)
    if engine is None:
        typer.echo("Database is not configured", err=True)
        raise typer.Exit(DEPENDENCY_UNAVAILABLE)
    store = ArtifactStore(Path(settings.state_dir) / "artifact_store")
    state_dir = Path(settings.state_dir)
    return settings, engine, store, state_dir, unit_of_work_connection


@git_app.command("prepare")
def git_prepare(
    project_id: str = typer.Option(..., "--project-id"),
    baseline_id: str = typer.Option(..., "--baseline-id"),
    source_root: str = typer.Option(..., "--source-root"),
    directory_identity: str = typer.Option(..., "--directory-identity"),
    strategy: str = typer.Option("commit_only", "--strategy"),
    dirty_snapshot_id: str | None = typer.Option(None, "--dirty-snapshot-id"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Prepare a platform worktree bound to a confirmed baseline."""
    from agentforge.contracts.git_workspaces import PrepareWorktreeRequest
    from agentforge.git.service import GitWorkspaceError, GitWorkspaceService

    _settings, engine, store, state_dir, uow_fn = _git_service()
    try:
        body = PrepareWorktreeRequest.model_validate(
            {
                "projectId": project_id,
                "baselineId": baseline_id,
                "strategy": strategy,
                "sourceRoot": source_root,
                "directoryIdentity": directory_identity,
                "dirtySnapshotId": dirty_snapshot_id,
            }
        )
        with uow_fn(engine) as uow:
            view = GitWorkspaceService(uow.session, store, state_dir).prepare_worktree(body)
            payload = view.model_dump(mode="json", by_alias=True)
    except GitWorkspaceError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(
        payload,
        as_json=json_out,
        human=f"workspace={view.workspace_id} status={view.status}",
    )


@git_app.command("capture-dirty")
def git_capture_dirty(
    project_id: str = typer.Option(..., "--project-id"),
    baseline_id: str = typer.Option(..., "--baseline-id"),
    source_root: str = typer.Option(..., "--source-root"),
    paths: str = typer.Option(..., "--paths", help="Comma-separated relative paths"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Explicitly capture selected dirty paths (no auto stash)."""
    from agentforge.contracts.git_workspaces import CaptureDirtySnapshotRequest
    from agentforge.git.service import GitWorkspaceError, GitWorkspaceService

    _settings, engine, store, state_dir, uow_fn = _git_service()
    try:
        body = CaptureDirtySnapshotRequest.model_validate(
            {
                "projectId": project_id,
                "baselineId": baseline_id,
                "sourceRoot": source_root,
                "selectedPaths": [p.strip() for p in paths.split(",") if p.strip()],
            }
        )
        with uow_fn(engine) as uow:
            view = GitWorkspaceService(uow.session, store, state_dir).capture_dirty(body)
            payload = view.model_dump(mode="json", by_alias=True)
    except GitWorkspaceError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(
        payload,
        as_json=json_out,
        human=f"snapshot={view.snapshot_id} paths={view.path_count}",
    )


@git_app.command("freeze-snapshot")
def git_freeze_snapshot(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    stop_evidence_json: str = typer.Option(
        ...,
        "--stop-evidence-json",
        help="GitStopEvidence JSON (operationId, attemptId, generation, evidenceSha256, ...)",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Freeze a recoverable workspace snapshot into the D06 store."""
    from agentforge.contracts.git_workspaces import FreezeWorkspaceSnapshotRequest
    from agentforge.git.service import GitWorkspaceError, GitWorkspaceService

    _settings, engine, store, state_dir, uow_fn = _git_service()
    try:
        evidence = json.loads(stop_evidence_json)
        with uow_fn(engine) as uow:
            view = GitWorkspaceService(uow.session, store, state_dir).freeze_snapshot(
                FreezeWorkspaceSnapshotRequest.model_validate(
                    {"workspaceId": workspace_id, "stopEvidence": evidence}
                )
            )
            payload = view.model_dump(mode="json", by_alias=True)
    except (GitWorkspaceError, json.JSONDecodeError) as exc:
        typer.echo(
            str(exc) if not isinstance(exc, GitWorkspaceError) else f"{exc.code}: {exc.message}",
            err=True,
        )
        raise typer.Exit(FAILURE) from None
    emit(
        payload,
        as_json=json_out,
        human=f"snapshot={view.snapshot_id} hash={view.content_hash[:12]}…",
    )


@git_app.command("integrate")
def git_integrate(
    project_id: str = typer.Option(..., "--project-id"),
    baseline_id: str = typer.Option(..., "--baseline-id"),
    source_root: str = typer.Option(..., "--source-root"),
    directory_identity: str = typer.Option(..., "--directory-identity"),
    workspace_ids: str = typer.Option(..., "--workspace-ids", help="Comma-separated ids"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Merge candidate workspace snapshots; conflicts stay explicit (not stage-released)."""
    from agentforge.contracts.git_workspaces import IntegrateRequest
    from agentforge.git.service import GitWorkspaceError, GitWorkspaceService

    _settings, engine, store, state_dir, uow_fn = _git_service()
    # Format: workspaceId:snapshotId[,...]
    snapshot_pairs = [x.strip() for x in workspace_ids.split(",") if x.strip()]
    refs = []
    for item in snapshot_pairs:
        if ":" not in item:
            typer.echo("Use --workspace-ids workspaceId:snapshotId,...", err=True)
            raise typer.Exit(CONFIG_ERROR)
        wid, sid = item.split(":", 1)
        refs.append({"workspaceId": wid, "snapshotId": sid})
    try:
        body = IntegrateRequest.model_validate(
            {
                "projectId": project_id,
                "baselineId": baseline_id,
                "sourceRoot": source_root,
                "directoryIdentity": directory_identity,
                "candidateSnapshots": refs,
            }
        )
        with uow_fn(engine) as uow:
            view = GitWorkspaceService(uow.session, store, state_dir).integrate(body)
            payload = view.model_dump(mode="json", by_alias=True)
    except GitWorkspaceError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(
        payload,
        as_json=json_out,
        human=(
            f"integration={view.integration_id} status={view.status} "
            f"stageReleased={view.stage_released}"
        ),
    )


@git_app.command("cleanup")
def git_cleanup(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    force: bool = typer.Option(False, "--force"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Remove a platform worktree; never deletes the user source root."""
    from agentforge.contracts.git_workspaces import CleanupWorktreeRequest
    from agentforge.git.service import GitWorkspaceError, GitWorkspaceService

    _settings, engine, store, state_dir, uow_fn = _git_service()
    try:
        with uow_fn(engine) as uow:
            view = GitWorkspaceService(uow.session, store, state_dir).cleanup(
                CleanupWorktreeRequest.model_validate({"workspaceId": workspace_id, "force": force})
            )
            payload = view.model_dump(mode="json", by_alias=True)
    except GitWorkspaceError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(FAILURE) from None
    emit(
        payload,
        as_json=json_out,
        human=f"workspace={view.workspace_id} status={view.status}",
    )
