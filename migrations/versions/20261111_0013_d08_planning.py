"""D08 planning: immutable plan specs, proposals, confirmation protocol.

Revision ID: 20261111_0013
Revises: 20261111_0012
Create Date: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261111_0013"
down_revision: str | None = "20261111_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Runs: rules-only planning may omit provider; pin baseline + idempotency.
    op.execute(
        sa.text(
            """
            ALTER TABLE app.runs
              ALTER COLUMN planning_provider_profile_id DROP NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE app.runs
              ADD COLUMN IF NOT EXISTS baseline_id text NULL,
              ADD COLUMN IF NOT EXISTS idempotency_key text NULL,
              ADD COLUMN IF NOT EXISTS current_confirmation_request_id text NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS runs_project_idempotency_uidx
              ON app.runs (project_id, idempotency_key)
              WHERE idempotency_key IS NOT NULL
            """
        )
    )

    # Plans: immutable full spec + hashes (do not overwrite old versions).
    op.execute(
        sa.text(
            """
            ALTER TABLE app.plans
              ADD COLUMN IF NOT EXISTS plan_hash text NULL,
              ADD COLUMN IF NOT EXISTS spec_json jsonb NULL,
              ADD COLUMN IF NOT EXISTS compilation_json jsonb NULL,
              ADD COLUMN IF NOT EXISTS context_id text NULL,
              ADD COLUMN IF NOT EXISTS baseline_id text NULL,
              ADD COLUMN IF NOT EXISTS input_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
              ADD COLUMN IF NOT EXISTS gate_policy_version integer NULL,
              ADD COLUMN IF NOT EXISTS supersedes_version integer NULL,
              ADD COLUMN IF NOT EXISTS hash_algo_version text NOT NULL DEFAULT 'plan-hash-v1'
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.planning_contexts (
                id text PRIMARY KEY,
                project_id text NOT NULL,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                baseline_id text NOT NULL,
                input_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
                read_scopes jsonb NOT NULL DEFAULT '[]'::jsonb,
                summary text NOT NULL,
                content_hash text NOT NULL,
                context_manifest_id text NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_planning_contexts_run ON app.planning_contexts (run_id)"
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.plan_proposals (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                context_id text NOT NULL,
                source text NOT NULL,
                model_call_id text NULL,
                draft_json jsonb NOT NULL,
                diagnostics_json jsonb NOT NULL DEFAULT '[]'::jsonb,
                status text NOT NULL DEFAULT 'active',
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT plan_proposals_source_check
                  CHECK (source = ANY (ARRAY['rules'::text, 'model'::text, 'auto'::text])),
                CONSTRAINT plan_proposals_status_check
                  CHECK (status = ANY (ARRAY[
                    'active'::text, 'compiled'::text, 'expired'::text, 'superseded'::text
                  ]))
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_plan_proposals_run "
            "ON app.plan_proposals (run_id, created_at DESC)"
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.plan_confirmation_requests (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                plan_version integer NOT NULL,
                plan_hash text NOT NULL,
                assignment_version integer NOT NULL,
                gate_policy_version integer NOT NULL,
                status_version integer NOT NULL,
                compilation_report_ref text NULL,
                capability_check_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                status text NOT NULL DEFAULT 'pending',
                failure_reason text NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                expires_at timestamptz NULL,
                CONSTRAINT plan_confirmation_requests_status_check
                  CHECK (status = ANY (ARRAY[
                    'pending'::text, 'approved'::text, 'rejected'::text,
                    'superseded'::text, 'expired'::text
                  ])),
                CONSTRAINT plan_confirmation_requests_plan_fk
                  FOREIGN KEY (run_id, plan_version)
                  REFERENCES app.plans(run_id, version)
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_plan_confirmation_requests_run "
            "ON app.plan_confirmation_requests (run_id, status)"
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.plan_decisions (
                id text PRIMARY KEY,
                request_id text NOT NULL
                  REFERENCES app.plan_confirmation_requests(id) ON DELETE CASCADE,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                actor text NOT NULL,
                decision text NOT NULL,
                note text NULL,
                decided_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT plan_decisions_decision_check
                  CHECK (decision = ANY (ARRAY['approve'::text, 'reject'::text])),
                CONSTRAINT plan_decisions_request_unique UNIQUE (request_id)
            )
            """
        )
    )

    # Assignment set evidence (versioned rows already exist).
    op.execute(
        sa.text(
            """
            ALTER TABLE app.role_assignments
              ADD COLUMN IF NOT EXISTS capability_check_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
              ADD COLUMN IF NOT EXISTS profile_config_version text NULL
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "20261111_0013 downgrade refused: dropping planning confirmation history "
        "loses immutable plan decisions"
    )
