"""Platform admission — DB is worker authority; control file is local mirror."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from agentforge.core.config import Settings
from agentforge.storage.models import PlatformAdmissionRow
from agentforge.storage.uow import db_now

Admission = Literal["open", "drain", "blocked"]
DbAdmissionMode = Literal["open", "draining", "stopped"]

DEFAULT_ENVIRONMENT_ID = "local"

_FILE_TO_DB: dict[Admission, DbAdmissionMode] = {
    "open": "open",
    "drain": "draining",
    "blocked": "stopped",
}
_DB_TO_FILE: dict[str, Admission] = {
    "open": "open",
    "draining": "drain",
    "stopped": "blocked",
}


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _now(session: Session | None = None) -> datetime:
    if session is not None:
        try:
            value = db_now(session)
            if getattr(value, "tzinfo", None) is None:
                return value.replace(tzinfo=UTC)  # type: ignore[union-attr]
            return value  # type: ignore[return-value]
        except Exception:
            pass
    return datetime.now(UTC)


def read_admission(settings: Settings) -> Admission:
    """Read local control file mirror (fail-closed on corrupt content)."""
    path = settings.control_path
    if not path.exists():
        return "open"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError, ValueError):
        return "blocked"
    if not isinstance(data, dict):
        return "blocked"
    value = data.get("admission")
    if value == "drain":
        return "drain"
    if value == "blocked":
        return "blocked"
    if value == "open":
        return "open"
    return "blocked"


def write_admission(settings: Settings, admission: Admission) -> dict[str, Any]:
    """Write local mirror only — prefer set_admission for DB+file authority."""
    if admission not in {"open", "drain", "blocked"}:
        raise ValueError(f"Unsupported admission value: {admission}")
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    payload = {"admission": admission, "updatedAt": _iso_now()}
    path = settings.control_path
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return payload


def admits_new_work(admission: Admission) -> bool:
    return admission == "open"


def read_admission_db(
    session: Session, *, environment_id: str = DEFAULT_ENVIRONMENT_ID
) -> Admission:
    """Authoritative admission for workers sharing the database."""
    row = session.get(PlatformAdmissionRow, environment_id)
    if row is None:
        return "open"
    return _DB_TO_FILE.get(row.mode, "blocked")


def set_admission(
    session: Session,
    settings: Settings,
    admission: Admission,
    *,
    environment_id: str = DEFAULT_ENVIRONMENT_ID,
    reason: str | None = None,
) -> dict[str, Any]:
    """Persist DB admission and mirror to the local control file."""
    if admission not in {"open", "drain", "blocked"}:
        raise ValueError(f"Unsupported admission value: {admission}")
    mode = _FILE_TO_DB[admission]
    row = session.get(PlatformAdmissionRow, environment_id)
    if row is None:
        row = PlatformAdmissionRow(
            environment_id=environment_id,
            mode=mode,
            version=1,
            reason=reason,
            updated_at=_now(session),
        )
        session.add(row)
    else:
        row.mode = mode
        row.version = int(row.version) + 1
        row.reason = reason
        row.updated_at = _now(session)
    mirror = write_admission(settings, admission)
    return {
        "environmentId": environment_id,
        "mode": mode,
        "admission": admission,
        "version": row.version,
        "reason": reason,
        "file": mirror,
    }


def resolve_admission(
    settings: Settings,
    session: Session | None = None,
    *,
    environment_id: str = DEFAULT_ENVIRONMENT_ID,
) -> Admission:
    """Prefer DB when a session is available; otherwise file mirror."""
    if session is not None:
        try:
            return read_admission_db(session, environment_id=environment_id)
        except Exception:
            return "blocked"
    return read_admission(settings)


def apply_admission(
    settings: Settings,
    admission: Admission,
    *,
    environment_id: str = DEFAULT_ENVIRONMENT_ID,
    reason: str | None = None,
) -> dict[str, Any]:
    """Set DB admission when configured; always update the local file mirror."""
    if settings.database_url is None:
        return {
            "admission": admission,
            **write_admission(settings, admission),
            "authority": "file",
        }
    from sqlalchemy import create_engine

    from agentforge.storage.uow import unit_of_work_connection

    engine = create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    try:
        with unit_of_work_connection(engine) as uow:
            result = set_admission(
                uow.session,
                settings,
                admission,
                environment_id=environment_id,
                reason=reason,
            )
            result["authority"] = "database"
            return result
    except Exception:
        # Fail closed on DB write errors: still mirror file, but mark blocked semantics
        # only when the requested mode was blocked; drain keeps file drain for CLI UX.
        mirror = write_admission(settings, admission)
        return {
            "admission": admission,
            **mirror,
            "authority": "file_fallback",
            "reason": reason,
        }
    finally:
        engine.dispose()
