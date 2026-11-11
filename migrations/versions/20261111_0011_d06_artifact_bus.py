"""D06 artifact bus: content objects, candidates, reports, publications.

Revision ID: 20261111_0011
Revises: 20261111_0010
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261111_0011"
down_revision: str | None = "20261111_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.content_objects (
                content_hash text PRIMARY KEY,
                size_bytes integer NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                gc_marked_at timestamptz NULL,
                gc_protect_until timestamptz NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.artifact_candidates (
                id text PRIMARY KEY,
                project_id text NOT NULL,
                workspace_id text NOT NULL,
                relative_path text NOT NULL,
                kind text NOT NULL,
                content_hash text NOT NULL,
                manifest_hash text NOT NULL,
                contract_id text NOT NULL,
                contract_version integer NOT NULL,
                contract_hash text NOT NULL,
                environment_hash text NOT NULL,
                state text NOT NULL,
                operation_id text NOT NULL,
                attempt_id text NULL,
                generation integer NOT NULL,
                stop_evidence_json jsonb NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_artifact_candidates_project_state "
            "ON app.artifact_candidates (project_id, state)"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.artifact_validation_reports (
                id text PRIMARY KEY,
                candidate_id text NOT NULL,
                validator_id text NOT NULL,
                validator_version text NOT NULL,
                status text NOT NULL,
                content_hash text NOT NULL,
                contract_hash text NOT NULL,
                environment_hash text NOT NULL,
                evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                message text NOT NULL,
                source text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_artifact_validation_reports_candidate "
            "ON app.artifact_validation_reports (candidate_id)"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.artifact_publications (
                id text PRIMARY KEY,
                candidate_id text NOT NULL,
                artifact_id text NOT NULL,
                version integer NOT NULL,
                content_hash text NOT NULL,
                manifest_hash text NOT NULL,
                idempotency_key text NOT NULL UNIQUE,
                stage_released boolean NOT NULL DEFAULT false,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_artifact_publications_artifact "
            "ON app.artifact_publications (artifact_id, version)"
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "20261111_0011 downgrade refused: dropping artifact bus tables would lose "
        "immutable publication evidence; restore from backup"
    )
