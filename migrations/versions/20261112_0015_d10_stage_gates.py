"""D10: stage candidate sets, review cycles, rework, release records.

Revision ID: 20261112_0015
Revises: 20261112_0014
Create Date: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_0015"
down_revision: str | None = "20261112_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            ALTER TABLE app.stages
              ADD COLUMN IF NOT EXISTS waiting_reason text NOT NULL DEFAULT 'none',
              ADD COLUMN IF NOT EXISTS concurrency_version integer NOT NULL DEFAULT 1,
              ADD COLUMN IF NOT EXISTS assignment_version integer NULL,
              ADD COLUMN IF NOT EXISTS gate_policy_version integer NULL,
              ADD COLUMN IF NOT EXISTS current_review_cycle_id text NULL,
              ADD COLUMN IF NOT EXISTS current_candidate_set_id text NULL,
              ADD COLUMN IF NOT EXISTS required_work_packages jsonb NOT NULL DEFAULT '[]'::jsonb,
              ADD COLUMN IF NOT EXISTS dependencies jsonb NOT NULL DEFAULT '[]'::jsonb,
              ADD COLUMN IF NOT EXISTS write_access_closed boolean NOT NULL DEFAULT false
            """
        )
    )

    op.execute(
        sa.text(
            """
            ALTER TABLE app.stage_submissions
              ADD COLUMN IF NOT EXISTS submitter_ref text NULL,
              ADD COLUMN IF NOT EXISTS work_package_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
              ADD COLUMN IF NOT EXISTS output_location_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
              ADD COLUMN IF NOT EXISTS candidate_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
              ADD COLUMN IF NOT EXISTS note text NULL,
              ADD COLUMN IF NOT EXISTS operation_id text NULL,
              ADD COLUMN IF NOT EXISTS plan_version integer NULL,
              ADD COLUMN IF NOT EXISTS assignment_version integer NULL,
              ADD COLUMN IF NOT EXISTS generation integer NULL,
              ADD COLUMN IF NOT EXISTS idempotency_key text NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE app.stage_submissions
              ALTER COLUMN candidate_id DROP NOT NULL,
              ALTER COLUMN manifest_hash DROP NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS stage_submissions_idempotency_uidx
              ON app.stage_submissions (stage_id, idempotency_key)
              WHERE idempotency_key IS NOT NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.candidate_sets (
                id text PRIMARY KEY,
                stage_id text NOT NULL REFERENCES app.stages(id) ON DELETE CASCADE,
                submission_id text NULL,
                members jsonb NOT NULL DEFAULT '[]'::jsonb,
                required_work_packages_complete boolean NOT NULL DEFAULT false,
                upstream_input_versions jsonb NOT NULL DEFAULT '[]'::jsonb,
                baseline_or_integration_ref text NULL,
                stop_evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                contract_hash text NOT NULL,
                environment_fingerprint text NOT NULL,
                manifest_hash text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS candidate_sets_stage_idx "
            "ON app.candidate_sets (stage_id, created_at)"
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.review_cycles (
                id text PRIMARY KEY,
                stage_id text NOT NULL REFERENCES app.stages(id) ON DELETE CASCADE,
                submission_id text NOT NULL,
                candidate_set_id text NOT NULL REFERENCES app.candidate_sets(id),
                candidate_set_hash text NOT NULL,
                contract_hash text NOT NULL,
                gate_policy_version integer NOT NULL,
                environment_fingerprint text NOT NULL,
                status text NOT NULL,
                repair_round integer NOT NULL DEFAULT 0,
                opened_at timestamptz NOT NULL DEFAULT now(),
                closed_at timestamptz NULL,
                CONSTRAINT review_cycles_submission_unique UNIQUE (submission_id)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS review_cycles_fingerprint_open_uidx
              ON app.review_cycles (
                stage_id, candidate_set_hash, contract_hash,
                gate_policy_version, environment_fingerprint
              )
              WHERE status NOT IN ('superseded', 'cancelled', 'decided')
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.review_requests (
                id text PRIMARY KEY,
                review_cycle_id text NOT NULL REFERENCES app.review_cycles(id) ON DELETE CASCADE,
                stage_id text NOT NULL,
                candidate_set_id text NOT NULL,
                review_role_id text NOT NULL,
                legal_responder_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                scope text NOT NULL,
                status text NOT NULL DEFAULT 'pending',
                deadline_at timestamptz NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            ALTER TABLE app.review_reports
              ADD COLUMN IF NOT EXISTS review_request_id text NULL,
              ADD COLUMN IF NOT EXISTS review_cycle_id text NULL,
              ADD COLUMN IF NOT EXISTS candidate_set_id text NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE app.review_reports
              ALTER COLUMN candidate_id DROP NOT NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.stage_user_decisions (
                id text PRIMARY KEY,
                request_id text NOT NULL,
                stage_id text NOT NULL REFERENCES app.stages(id) ON DELETE CASCADE,
                review_cycle_id text NOT NULL,
                candidate_set_hash text NOT NULL,
                contract_hash text NOT NULL,
                gate_policy_version integer NOT NULL,
                decision text NOT NULL,
                note text NULL,
                decided_by text NOT NULL,
                decided_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT stage_user_decisions_request_unique UNIQUE (request_id)
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            ALTER TABLE app.gate_decisions
              ADD COLUMN IF NOT EXISTS review_cycle_id text NULL,
              ADD COLUMN IF NOT EXISTS candidate_set_id text NULL,
              ADD COLUMN IF NOT EXISTS user_decision_ref text NULL,
              ADD COLUMN IF NOT EXISTS allowed_consume_work_package_ids jsonb
                NOT NULL DEFAULT '[]'::jsonb
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE app.gate_decisions
              ALTER COLUMN candidate_id DROP NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS gate_decisions_cycle_result_uidx
              ON app.gate_decisions (review_cycle_id, result)
              WHERE review_cycle_id IS NOT NULL AND result = 'released'
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.rework_orders (
                id text PRIMARY KEY,
                stage_id text NOT NULL REFERENCES app.stages(id) ON DELETE CASCADE,
                source_review_cycle_id text NOT NULL,
                source_candidate_set_id text NOT NULL,
                target_work_package_id text NOT NULL,
                target_role_id text NOT NULL,
                problem_digest text NOT NULL,
                problems jsonb NOT NULL DEFAULT '[]'::jsonb,
                allowed_modify_scopes jsonb NOT NULL DEFAULT '[]'::jsonb,
                checks_to_rerun jsonb NOT NULL DEFAULT '[]'::jsonb,
                remaining_rounds integer NOT NULL,
                involves_plan_or_interface_change boolean NOT NULL DEFAULT false,
                status text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT rework_orders_cycle_digest_unique
                  UNIQUE (source_review_cycle_id, problem_digest)
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.release_records (
                id text PRIMARY KEY,
                stage_id text NOT NULL REFERENCES app.stages(id) ON DELETE CASCADE,
                review_cycle_id text NOT NULL,
                gate_decision_id text NOT NULL REFERENCES app.gate_decisions(id),
                scope_kind text NOT NULL,
                work_package_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
                candidate_versions jsonb NOT NULL DEFAULT '[]'::jsonb,
                dependency_satisfied_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                shared_interface_still_blocked boolean NOT NULL DEFAULT false,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.evidence_fingerprints (
                id text PRIMARY KEY,
                evidence_ref text NOT NULL,
                content_hash text NOT NULL,
                contract_hash text NOT NULL,
                check_definition_hash text NOT NULL,
                environment_fingerprint text NOT NULL,
                input_versions_hash text NOT NULL,
                reuse_reason text NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT evidence_fingerprints_unique
                  UNIQUE (
                    content_hash, contract_hash, check_definition_hash,
                    environment_fingerprint, input_versions_hash
                  )
            )
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "20261112_0015 downgrade refused: dropping stage gate history "
        "loses immutable release and review evidence"
    )
