"""Bounded doctor checks with per-item timeouts (BE-03.6)."""

from __future__ import annotations

import multiprocessing
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Literal

from agentforge import __version__
from agentforge.core.config import Settings
from agentforge.core.credentials import CredentialError, list_credential_status, read_token
from agentforge.runtime.components import component_status_snapshot
from agentforge.runtime.instance import read_runtime_record
from agentforge.storage.database import check_database, create_database_engine

CheckStatus = Literal["pass", "fail", "warn", "skip"]
CheckOutcome = tuple[CheckStatus, str, str]


@dataclass
class CheckResult:
    check_id: str
    status: CheckStatus
    required: bool
    duration_ms: int
    message: str
    remediation: str
    checked_at: str

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {
            "checkId": data["check_id"],
            "status": data["status"],
            "required": data["required"],
            "durationMs": data["duration_ms"],
            "message": data["message"],
            "remediation": data["remediation"],
            "checkedAt": data["checked_at"],
        }


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _settings_payload(settings: Settings) -> dict[str, Any]:
    database_url = (
        None if settings.database_url is None else settings.database_url.get_secret_value()
    )
    return {
        "host": settings.host,
        "port": settings.port,
        "state_dir": str(settings.state_dir),
        "run_mode": settings.run_mode,
        "database_url": database_url,
        "expected_alembic_revision": settings.expected_alembic_revision,
        "api_token": None if settings.api_token is None else settings.api_token.get_secret_value(),
        "require_database": False,
    }


def _settings_from_payload(payload: dict[str, Any]) -> Settings:
    return Settings.model_validate(
        {
            "host": payload["host"],
            "port": int(payload["port"]),
            "state_dir": Path(payload["state_dir"]),
            "run_mode": payload["run_mode"],
            "database_url": payload.get("database_url"),
            "expected_alembic_revision": payload["expected_alembic_revision"],
            "api_token": payload.get("api_token"),
        }
    )


def _outcome_to_result(
    check_id: str,
    required: bool,
    outcome: CheckOutcome,
    *,
    started: float,
    checked_at: str,
) -> CheckResult:
    status, message, remediation = outcome
    return CheckResult(
        check_id=check_id,
        status=status,
        required=required,
        duration_ms=int((time.perf_counter() - started) * 1000),
        message=message,
        remediation=remediation,
        checked_at=checked_at,
    )


def _run_check_inline(
    check_id: str,
    required: bool,
    fn: Any,
) -> CheckResult:
    started = time.perf_counter()
    checked_at = _now()
    try:
        outcome = fn()
    except Exception as exc:  # noqa: BLE001
        outcome = (
            "fail",
            f"Check error: {type(exc).__name__}",
            "See logs; secrets are never included in doctor output",
        )
    return _outcome_to_result(check_id, required, outcome, started=started, checked_at=checked_at)


def _execute_named_check(kind: str, payload: dict[str, Any]) -> CheckOutcome:
    """Top-level worker body — must stay picklable for spawn."""
    if kind == "inject_block":
        time.sleep(float(os.environ.get("AGENTFORGE_DOCTOR_BLOCK_SECONDS", "1.5")))
        return "pass", "inject block finished", ""

    settings = _settings_from_payload(payload)
    if kind == "database":
        require_database = bool(payload.get("require_database"))
        engine = create_database_engine(settings)
        try:
            result = check_database(engine, expected_revision=settings.expected_alembic_revision)
        finally:
            if engine is not None:
                engine.dispose()
        if result.status == "available":
            return "pass", result.message, ""
        if result.status == "unconfigured":
            check_status: CheckStatus = "fail" if require_database else "warn"
            return (
                check_status,
                f"{result.status}: {result.message}",
                "Set AGENTFORGE_DATABASE_URL with agentforge_api role",
            )
        return (
            "fail",
            f"{result.status}: {result.message}",
            "Verify PostgreSQL, revision, and role permissions (no auto-migrate)",
        )
    return "fail", f"unknown check {kind}", ""


def _worker_main(conn: Connection, kind: str, payload: dict[str, Any]) -> None:
    try:
        conn.send(("ok", _execute_named_check(kind, payload)))
    except Exception as exc:  # noqa: BLE001
        conn.send(("err", type(exc).__name__))
    finally:
        conn.close()


def _run_check_process(
    check_id: str,
    required: bool,
    kind: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> CheckResult:
    """Run a potentially blocking check in a killable child process."""
    started = time.perf_counter()
    checked_at = _now()
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_worker_main, args=(child, kind, payload))
    proc.start()
    child.close()
    proc.join(timeout_seconds)
    if proc.is_alive():
        proc.terminate()
        proc.join(0.5)
        if proc.is_alive():
            proc.kill()
            proc.join(0.5)
        parent.close()
        return CheckResult(
            check_id=check_id,
            status="fail",
            required=required,
            duration_ms=int((time.perf_counter() - started) * 1000),
            message=f"Check timed out after {timeout_seconds:.1f}s",
            remediation="Inspect the dependency independently; doctor will not hang on it",
            checked_at=checked_at,
        )
    try:
        if parent.poll(0.2):
            tag, body = parent.recv()
            if tag == "ok":
                return _outcome_to_result(
                    check_id, required, body, started=started, checked_at=checked_at
                )
            message = f"Check error: {body}"
        else:
            message = "Check worker exited without a result"
    except (EOFError, OSError):
        message = "Check worker communication failed"
    finally:
        parent.close()
    return CheckResult(
        check_id=check_id,
        status="fail",
        required=required,
        duration_ms=int((time.perf_counter() - started) * 1000),
        message=message,
        remediation="Re-run doctor; if it persists inspect local process limits",
        checked_at=checked_at,
    )


def run_doctor(
    settings: Settings,
    *,
    require_database: bool = False,
    level: Literal["default", "local"] = "default",
) -> dict[str, Any]:
    """Default doctor never calls models, pulls images, or starts services."""
    payload = _settings_payload(settings)
    payload["require_database"] = require_database

    def python_check() -> CheckOutcome:
        return "pass", f"Python {sys.version.split()[0]}", ""

    def config_check() -> CheckOutcome:
        return (
            "pass",
            f"mode={settings.run_mode} stateDir={settings.state_dir} "
            f"listen={settings.host}:{settings.port}",
            "",
        )

    def credentials_check() -> CheckOutcome:
        try:
            read_token(settings)
            cred_status = list_credential_status(settings)
            return "pass", f"credentials configured source={cred_status['source']}", ""
        except CredentialError as exc:
            return "fail", str(exc), "Run agentforge init"

    def dirs_check() -> CheckOutcome:
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        return "pass", "state directory writable", ""

    def git_check() -> CheckOutcome:
        if shutil.which("git") is not None:
            return "pass", "git executable found", ""
        return "warn", "git not on PATH", "Install Git if code tasks are required"

    def docker_check() -> CheckOutcome:
        if shutil.which("docker") is not None:
            return "pass", "docker CLI found (daemon not probed in default doctor)", ""
        return "warn", "docker CLI not on PATH", "Required for local/container execution later"

    def runtime_check() -> CheckOutcome:
        record = read_runtime_record(settings)
        if record is None:
            return "pass", "no active runtime record", ""
        return "pass", f"runtime record instanceId={record.instance_id} stage={record.stage}", ""

    def reconciliation_check() -> CheckOutcome:
        from agentforge.storage.reconciliation import pending_reconciliation_summary

        summary = pending_reconciliation_summary(settings)
        if summary["status"] in {"unconfigured", "missing_tables"}:
            return "warn", summary["message"], "Apply migrations or configure database"
        if summary["pendingCount"]:
            return (
                "warn",
                summary["message"],
                "Resolve app.run_status_reconciliations before advancing those runs",
            )
        return "pass", summary["message"], ""

    def database_check_inline() -> CheckOutcome:
        return _execute_named_check("database", payload)

    # Unconfigured DB cannot hang on I/O — keep it in-process. Configured DB runs
    # in a killable child so statement/connect stalls cannot pin the CLI.
    database_check = (
        _run_check_process("database", require_database, "database", payload, 5.0)
        if settings.database_url is not None
        else _run_check_inline("database", require_database, database_check_inline)
    )

    checks = [
        _run_check_inline("python", True, python_check),
        _run_check_inline("config", True, config_check),
        _run_check_inline("credentials", True, credentials_check),
        _run_check_inline("state_dir", True, dirs_check),
        _run_check_inline("git", False, git_check),
        _run_check_inline("docker_cli", False, docker_check),
        database_check,
        _run_check_inline("runtime_record", False, runtime_check),
        _run_check_inline("wait_status_reconciliation", False, reconciliation_check),
    ]

    if os.environ.get("AGENTFORGE_DOCTOR_BLOCK_SECONDS"):
        checks.append(_run_check_process("inject_block", False, "inject_block", payload, 0.05))

    if level == "local":

        def components_check() -> CheckOutcome:
            rows = component_status_snapshot(settings.run_mode)
            missing = [r["id"] for r in rows if r["state"] == "not_implemented"]
            if settings.run_mode == "local" and missing:
                return (
                    "warn",
                    f"local mode components not implemented: {', '.join(missing)}",
                    "Wait for BE-14/20/21; do not pretend the stack is ready",
                )
            return "pass", "component catalog inspected", ""

        checks.append(_run_check_inline("components", False, components_check))

    required_failed = any(c.status == "fail" and c.required for c in checks)
    return {
        "version": __version__,
        "runMode": settings.run_mode,
        "level": level,
        "checks": [c.as_dict() for c in checks],
        "requiredFailed": required_failed,
        "note": "Default doctor does not call models, download images, or start services",
    }
