"""D07 git worktrees, dirty snapshots, integrations.

Revision ID: 20261111_0012
Revises: 20261111_0011
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261111_0012"
down_revision: str | None = "20261111_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.git_dirty_snapshots (
                id text PRIMARY KEY,
                project_id text NOT NULL,
                baseline_id text NOT NULL,
                content_hash text NOT NULL,
                manifest_hash text NOT NULL,
                path_count integer NOT NULL,
                selected_paths jsonb NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.git_workspaces (
                id text PRIMARY KEY,
                project_id text NOT NULL,
                baseline_id text NOT NULL,
                strategy text NOT NULL,
                status text NOT NULL,
                worktree_path text NOT NULL,
                source_root text NOT NULL,
                head_commit text NULL,
                dirty_included boolean NOT NULL DEFAULT false,
                dirty_snapshot_id text NULL,
                attempt_id text NULL,
                role_id text NULL,
                restrictions jsonb NOT NULL DEFAULT '[]'::jsonb,
                message text NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_git_workspaces_project "
            "ON app.git_workspaces (project_id)"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.git_workspace_snapshots (
                id text PRIMARY KEY,
                workspace_id text NOT NULL,
                content_hash text NOT NULL,
                manifest_hash text NOT NULL,
                head_commit text NULL,
                stop_operation_id text NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_git_workspace_snapshots_workspace "
            "ON app.git_workspace_snapshots (workspace_id)"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.git_integrations (
                id text PRIMARY KEY,
                project_id text NOT NULL,
                baseline_id text NOT NULL,
                status text NOT NULL,
                worktree_path text NULL,
                input_workspace_ids jsonb NOT NULL,
                conflict_paths jsonb NOT NULL DEFAULT '[]'::jsonb,
                content_hash text NULL,
                manifest_hash text NULL,
                stage_released boolean NOT NULL DEFAULT false,
                message text NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "20261111_0012 downgrade refused: dropping git workspace tables loses "
        "recovery references; restore from backup"
    )
