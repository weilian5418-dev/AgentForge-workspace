"""D05 runner operation ledger.

Revision ID: 20261111_0010
Revises: 20261111_0009
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261111_0010"
down_revision: str | None = "20261111_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.runner_operations (
                id text PRIMARY KEY,
                profile_id text NOT NULL,
                workspace_id text NOT NULL,
                workspace_root text NOT NULL,
                node_id text NOT NULL,
                attempt_id text NULL,
                generation integer NOT NULL,
                action_digest text NOT NULL,
                scope text NOT NULL,
                params_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                status text NOT NULL,
                result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                process_ref text NULL,
                approval_id text NULL,
                require_effective_approval boolean NOT NULL DEFAULT false,
                message text NULL,
                expires_at timestamptz NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_runner_operations_workspace_status "
            "ON app.runner_operations (workspace_id, status)"
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "20261111_0010 downgrade refused: dropping runner_operations would lose "
        "execution evidence; restore from backup instead"
    )
