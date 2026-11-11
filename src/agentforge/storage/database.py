"""Lazy SQLAlchemy connections; startup never creates or migrates tables."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from agentforge.contracts.system import DependencyStatus
from agentforge.core.config import Settings


def create_database_engine(settings: Settings) -> Engine | None:
    if settings.database_url is None:
        return None
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=3,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=3000"},
        hide_parameters=True,
    )


def _sqlstate(exc: BaseException) -> str | None:
    orig: Any = getattr(exc, "orig", None)
    if orig is None:
        return None
    state = getattr(orig, "sqlstate", None)
    return str(state) if state is not None else None


def check_database(
    engine: Engine | None,
    *,
    expected_revision: str | None = None,
) -> DependencyStatus:
    if engine is None:
        return DependencyStatus(status="unconfigured", message="Configure AGENTFORGE_DATABASE_URL")
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SELECT 1")).scalar_one()
            if expected_revision:
                try:
                    current: object = connection.execute(
                        text("SELECT version_num FROM app.alembic_version")
                    ).scalar_one()
                except SQLAlchemyError as exc:
                    state = _sqlstate(exc)
                    if state == "42501":
                        return DependencyStatus(
                            status="unavailable",
                            message=(
                                "Database reachable but SELECT on app.alembic_version denied; "
                                "apply deploy/postgres/grants.sql"
                            ),
                        )
                    if state == "42P01":
                        return DependencyStatus(
                            status="unavailable",
                            message=(
                                "Database reachable but app.alembic_version missing; run migrations"
                            ),
                        )
                    return DependencyStatus(
                        status="unavailable",
                        message=(
                            "Database reachable but could not read app.alembic_version; "
                            "check migration state and grants"
                        ),
                    )
                if str(current) != expected_revision:
                    return DependencyStatus(
                        status="unavailable",
                        message=(
                            f"Alembic revision mismatch: expected {expected_revision}, "
                            f"found {current}; do not auto-migrate from the API"
                        ),
                    )
                # Permission signal: service role should not create tables.
                can_create: object = connection.execute(
                    text("SELECT has_schema_privilege(current_user, 'app', 'CREATE')")
                ).scalar_one()
                if bool(can_create):
                    return DependencyStatus(
                        status="unavailable",
                        message=(
                            "Connected role has CREATE on app; use agentforge_api for the service, "
                            "not migrator/admin"
                        ),
                    )
        return DependencyStatus(
            status="available",
            message=(
                "PostgreSQL connection succeeded"
                + (f"; revision {expected_revision}" if expected_revision else "")
            ),
        )
    except SQLAlchemyError:
        return DependencyStatus(status="unavailable", message="PostgreSQL connection failed")
