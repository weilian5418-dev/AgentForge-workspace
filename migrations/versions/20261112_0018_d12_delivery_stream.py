"""D12: replay windows, integration builds, delivery, acceptance.

Revision ID: 20261112_0018
Revises: 20261112_0017
Create Date: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_0018"
down_revision: str | None = "20261112_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.event_replay_windows (
                run_id text PRIMARY KEY
                  REFERENCES app.runs(id) ON DELETE CASCADE,
                replay_floor_sequence bigint NOT NULL DEFAULT 0,
                last_archived_sequence bigint NOT NULL DEFAULT 0,
                retention_version integer NOT NULL DEFAULT 1,
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT event_replay_windows_floor_check
                  CHECK (replay_floor_sequence >= 0),
                CONSTRAINT event_replay_windows_archived_check
                  CHECK (last_archived_sequence >= 0),
                CONSTRAINT event_replay_windows_order_check
                  CHECK (last_archived_sequence <= replay_floor_sequence),
                CONSTRAINT event_replay_windows_retention_check
                  CHECK (retention_version >= 1)
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.integration_selections (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                handoff_ref text NOT NULL,
                handoff_hash text NOT NULL,
                baseline_ref text NULL,
                baseline_tree_hash text NOT NULL,
                source_bindings jsonb NOT NULL DEFAULT '[]'::jsonb,
                merge_policy_version text NOT NULL,
                selection_hash text NOT NULL,
                request_key text NOT NULL,
                created_by text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT integration_selections_run_key_unique
                  UNIQUE (run_id, request_key)
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.integration_builds (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                selection_id text NOT NULL
                  REFERENCES app.integration_selections(id) ON DELETE RESTRICT,
                operation_key text NOT NULL,
                request_hash text NOT NULL,
                status text NOT NULL,
                workspace_ref text NULL,
                claim_token text NULL,
                result_version_id text NULL,
                error_code text NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT integration_builds_run_key_unique
                  UNIQUE (run_id, operation_key),
                CONSTRAINT integration_builds_status_check
                  CHECK (status = ANY (ARRAY[
                    'accepted'::text, 'running'::text, 'succeeded'::text,
                    'conflicted'::text, 'failed'::text, 'unknown'::text,
                    'needs_reconciliation'::text
                  ]))
            )
            """
        )
    )

    # Extend integration_versions with immutable identity fields (nullable for legacy rows).
    op.execute(
        sa.text(
            """
            ALTER TABLE app.integration_versions
              ADD COLUMN IF NOT EXISTS selection_id text NULL,
              ADD COLUMN IF NOT EXISTS selection_hash text NULL,
              ADD COLUMN IF NOT EXISTS git_integration_id text NULL,
              ADD COLUMN IF NOT EXISTS version_number integer NULL,
              ADD COLUMN IF NOT EXISTS tree_manifest_hash text NULL,
              ADD COLUMN IF NOT EXISTS baseline_hash text NULL,
              ADD COLUMN IF NOT EXISTS contract_hash text NULL,
              ADD COLUMN IF NOT EXISTS input_versions_hash text NULL,
              ADD COLUMN IF NOT EXISTS build_environment_ref text NULL,
              ADD COLUMN IF NOT EXISTS parent_version_id text NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE app.integration_versions
               SET version_number = 1
             WHERE version_number IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE app.integration_versions
              ALTER COLUMN version_number SET DEFAULT 1
            """
        )
    )
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                 WHERE conname = 'integration_versions_run_number_unique'
              ) THEN
                ALTER TABLE app.integration_versions
                  ADD CONSTRAINT integration_versions_run_number_unique
                  UNIQUE (run_id, version_number);
              END IF;
            END $$
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.integration_validation_cycles (
                id text PRIMARY KEY,
                version_id text NOT NULL
                  REFERENCES app.integration_versions(id) ON DELETE CASCADE,
                policy_hash text NOT NULL,
                environment_fingerprint text NOT NULL,
                status text NOT NULL,
                required_checks jsonb NOT NULL DEFAULT '[]'::jsonb,
                required_review_roles jsonb NOT NULL DEFAULT '[]'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT integration_validation_cycles_status_check
                  CHECK (status = ANY (ARRAY[
                    'open'::text, 'checking'::text, 'awaiting_review'::text,
                    'passed'::text, 'failed'::text, 'blocked'::text, 'superseded'::text
                  ]))
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.integration_check_runs (
                id text PRIMARY KEY,
                cycle_id text NOT NULL
                  REFERENCES app.integration_validation_cycles(id) ON DELETE CASCADE,
                check_id text NOT NULL,
                check_version text NOT NULL,
                config_hash text NOT NULL,
                attempt_no integer NOT NULL DEFAULT 1,
                status text NOT NULL,
                operation_ref text NULL,
                started_at timestamptz NULL,
                finished_at timestamptz NULL,
                CONSTRAINT integration_check_runs_attempt_unique
                  UNIQUE (cycle_id, check_id, check_version, config_hash, attempt_no),
                CONSTRAINT integration_check_runs_attempt_check
                  CHECK (attempt_no >= 1)
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.integration_check_reports (
                id text PRIMARY KEY,
                check_run_id text NOT NULL
                  REFERENCES app.integration_check_runs(id) ON DELETE CASCADE,
                version_id text NOT NULL
                  REFERENCES app.integration_versions(id) ON DELETE CASCADE,
                tree_hash text NOT NULL,
                policy_hash text NOT NULL,
                environment_hash text NOT NULL,
                outcome text NOT NULL,
                structured_result jsonb NOT NULL DEFAULT '{}'::jsonb,
                evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                report_hash text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT integration_check_reports_outcome_check
                  CHECK (outcome = ANY (ARRAY[
                    'passed'::text, 'failed'::text, 'blocked'::text, 'not_run'::text,
                    'unknown'::text, 'not_applicable'::text, 'skipped'::text, 'timeout'::text
                  ]))
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.integration_review_requests (
                id text PRIMARY KEY,
                cycle_id text NOT NULL
                  REFERENCES app.integration_validation_cycles(id) ON DELETE CASCADE,
                version_id text NOT NULL
                  REFERENCES app.integration_versions(id) ON DELETE CASCADE,
                tree_hash text NOT NULL,
                selection_hash text NOT NULL,
                contract_hash text NOT NULL,
                policy_hash text NOT NULL,
                reviewer_binding text NOT NULL,
                status text NOT NULL,
                expires_at timestamptz NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.integration_review_reports (
                id text PRIMARY KEY,
                request_id text NOT NULL
                  REFERENCES app.integration_review_requests(id) ON DELETE CASCADE,
                cycle_id text NOT NULL
                  REFERENCES app.integration_validation_cycles(id) ON DELETE CASCADE,
                version_id text NOT NULL
                  REFERENCES app.integration_versions(id) ON DELETE CASCADE,
                tree_hash text NOT NULL,
                verdict text NOT NULL,
                reviewer_identity_ref text NOT NULL,
                response_key text NOT NULL,
                evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT integration_review_reports_request_key_unique
                  UNIQUE (request_id, response_key)
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.delivery_manifests (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                version_id text NOT NULL
                  REFERENCES app.integration_versions(id) ON DELETE RESTRICT,
                cycle_id text NOT NULL
                  REFERENCES app.integration_validation_cycles(id) ON DELETE RESTRICT,
                revision integer NOT NULL,
                manifest_hash text NOT NULL,
                payload_ref text NOT NULL,
                technical_status text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT delivery_manifests_run_revision_unique
                  UNIQUE (run_id, revision),
                CONSTRAINT delivery_manifests_hash_unique
                  UNIQUE (manifest_hash),
                CONSTRAINT delivery_manifests_revision_check
                  CHECK (revision >= 1)
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.run_deliveries (
                run_id text PRIMARY KEY
                  REFERENCES app.runs(id) ON DELETE CASCADE,
                current_version_id text NULL
                  REFERENCES app.integration_versions(id) ON DELETE SET NULL,
                current_manifest_id text NULL
                  REFERENCES app.delivery_manifests(id) ON DELETE SET NULL,
                delivery_revision integer NOT NULL DEFAULT 1,
                technical_status text NOT NULL DEFAULT 'not_started',
                acceptance_status text NOT NULL DEFAULT 'none',
                invalidated_reason text NULL,
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT run_deliveries_revision_check
                  CHECK (delivery_revision >= 1)
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.acceptance_decisions (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                manifest_id text NOT NULL
                  REFERENCES app.delivery_manifests(id) ON DELETE RESTRICT,
                manifest_hash text NOT NULL,
                version_id text NOT NULL
                  REFERENCES app.integration_versions(id) ON DELETE RESTRICT,
                delivery_revision integer NOT NULL,
                actor_ref text NOT NULL,
                decision text NOT NULL,
                accepted_member_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
                rejected_member_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
                request_hash text NOT NULL,
                request_key text NOT NULL,
                note text NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT acceptance_decisions_actor_key_unique
                  UNIQUE (run_id, actor_ref, request_key),
                CONSTRAINT acceptance_decisions_decision_check
                  CHECK (decision = ANY (ARRAY[
                    'accept'::text, 'reject'::text, 'partial_accept'::text
                  ]))
            )
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "20261112_0018 downgrade refused: dropping delivery/integration history "
        "loses acceptance and validation evidence"
    )
