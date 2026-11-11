"""D02: async_operations table + Procrastinate queue schema.

Revision ID: 20261111_0009
Revises: 20261111_0008
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from procrastinate import App, SyncPsycopgConnector

revision: str = "20261111_0009"
down_revision: str | None = "20261111_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _psycopg_conninfo(bind: sa.Connection) -> str:
    # Must not use str(engine.url) — SQLAlchemy redacts the password.
    raw = bind.engine.url.render_as_string(hide_password=False)
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if raw.startswith(prefix):
            return "postgresql://" + raw[len(prefix) :]
    return raw


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.async_operations (
                id text PRIMARY KEY,
                operation_type text NOT NULL,
                status text NOT NULL DEFAULT 'accepted',
                run_id text NULL,
                provider_id text NULL,
                grant_id text NULL,
                provider_config_version integer NULL,
                result_ref text NULL,
                outbox_id text NULL,
                uncertain_side_effects boolean NOT NULL DEFAULT false,
                message text NULL,
                payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                replay_count integer NOT NULL DEFAULT 0,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_async_operations_type_status
            ON app.async_operations (operation_type, status)
            """
        )
    )
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS queue"))

    bind = op.get_bind()
    exists = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'queue' AND table_name = 'procrastinate_jobs'"
        )
    ).scalar()
    if exists:
        return

    connector = SyncPsycopgConnector(
        conninfo=_psycopg_conninfo(bind),
        kwargs={"options": "-c search_path=queue,public"},
    )
    app = App(connector=connector)
    app.open()
    try:
        app.schema_manager.apply_schema()
    finally:
        app.close()


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS app.async_operations"))
    raise NotImplementedError(
        "20261111_0009 downgrade refuses to drop queue schema; restore from backup"
    )
