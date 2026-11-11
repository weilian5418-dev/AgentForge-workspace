"""BE-04: directory grants, baselines, inputs, environment path bindings.

Revision ID: 20261111_0007
Revises: 20261110_0006
Create Date: 2026-11-11

Adds grant/environment/baseline/input tables and project columns needed for
secure project registration. Does not rewrite historical project rows beyond
nullable column defaults.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261111_0007"
down_revision: str | None = "20261110_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.execution_environments (
                id text PRIMARY KEY,
                type text NOT NULL,
                node_id text NOT NULL,
                wsl_distro text NULL,
                connection_status text NOT NULL DEFAULT 'unverified',
                last_checked_at timestamptz NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.directory_grants (
                id text PRIMARY KEY,
                authorized_user text NOT NULL,
                environment_id text NOT NULL
                    REFERENCES app.execution_environments (id),
                normalized_root text NOT NULL,
                directory_identity text NOT NULL,
                allowed_operations jsonb NOT NULL DEFAULT '[]'::jsonb,
                grant_version integer NOT NULL DEFAULT 1
                    CHECK (grant_version >= 1),
                status text NOT NULL DEFAULT 'active',
                created_at timestamptz NOT NULL DEFAULT now(),
                revoked_at timestamptz NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_directory_grants_user_status
            ON app.directory_grants (authorized_user, status)
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.project_baselines (
                id text PRIMARY KEY,
                project_id text NOT NULL REFERENCES app.projects (id),
                environment_id text NOT NULL,
                dirty_policy text NOT NULL,
                status text NOT NULL,
                is_git boolean NOT NULL DEFAULT false,
                git_commit text NULL,
                git_dirty_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
                manifest_hash text NULL,
                scope_strategy text NOT NULL,
                notes jsonb NOT NULL DEFAULT '[]'::jsonb,
                checked_at timestamptz NOT NULL DEFAULT now(),
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_project_baselines_project
            ON app.project_baselines (project_id)
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.project_inputs (
                id text PRIMARY KEY,
                project_id text NOT NULL REFERENCES app.projects (id),
                input_version integer NOT NULL CHECK (input_version >= 1),
                original_name text NOT NULL,
                size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
                sha256 text NOT NULL,
                storage_ref text NOT NULL,
                content_type text NULL,
                status text NOT NULL DEFAULT 'staging',
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT uq_project_inputs_name_version
                    UNIQUE (project_id, original_name, input_version)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_project_inputs_project_status
            ON app.project_inputs (project_id, status)
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.environment_path_bindings (
                id text PRIMARY KEY,
                source_environment_id text NOT NULL,
                target_environment_id text NOT NULL,
                source_path text NOT NULL,
                target_path text NOT NULL,
                correspondence_evidence text NOT NULL,
                binding_version integer NOT NULL DEFAULT 1
                    CHECK (binding_version >= 1),
                status text NOT NULL,
                checked_at timestamptz NOT NULL DEFAULT now(),
                expires_at timestamptz NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )

    op.add_column(
        "projects",
        sa.Column("environment_id", sa.Text(), nullable=True),
        schema="app",
    )
    op.add_column(
        "projects",
        sa.Column("grant_version", sa.Integer(), nullable=True),
        schema="app",
    )
    op.add_column(
        "projects",
        sa.Column("current_baseline_id", sa.Text(), nullable=True),
        schema="app",
    )
    op.add_column(
        "projects",
        sa.Column(
            "grant_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("projects", "grant_status", schema="app")
    op.drop_column("projects", "current_baseline_id", schema="app")
    op.drop_column("projects", "grant_version", schema="app")
    op.drop_column("projects", "environment_id", schema="app")
    op.execute(sa.text("DROP TABLE IF EXISTS app.environment_path_bindings"))
    op.execute(sa.text("DROP TABLE IF EXISTS app.project_inputs"))
    op.execute(sa.text("DROP TABLE IF EXISTS app.project_baselines"))
    op.execute(sa.text("DROP TABLE IF EXISTS app.directory_grants"))
    op.execute(sa.text("DROP TABLE IF EXISTS app.execution_environments"))
