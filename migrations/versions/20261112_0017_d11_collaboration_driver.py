"""D11: driver threads, waits, wakes, recovery, attempt lifecycle.

Revision ID: 20261112_0017
Revises: 20261112_0016
Create Date: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_0017"
down_revision: str | None = "20261112_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE = (
    "'scheduled'",
    "'dispatching'",
    "'running'",
    "'submitted'",
    "'awaiting_stop_verification'",
    "'unknown'",
)


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            ALTER TABLE app.runs
              ADD COLUMN IF NOT EXISTS collaboration_state text
                NOT NULL DEFAULT 'idle',
              ADD COLUMN IF NOT EXISTS collaboration_ready_at timestamptz NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE app.runs
              DROP CONSTRAINT IF EXISTS runs_collaboration_state_check
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE app.runs
              ADD CONSTRAINT runs_collaboration_state_check
              CHECK (
                collaboration_state = ANY (ARRAY[
                  'idle'::text, 'running'::text, 'waiting'::text,
                  'ready_for_integration'::text, 'blocked'::text
                ])
              )
            """
        )
    )

    op.execute(
        sa.text(
            """
            ALTER TABLE app.instance_attempts
              ADD COLUMN IF NOT EXISTS status text NULL,
              ADD COLUMN IF NOT EXISTS dispatch_delivery_id text NULL,
              ADD COLUMN IF NOT EXISTS scheduler_operation_id text NULL,
              ADD COLUMN IF NOT EXISTS started_at timestamptz NULL,
              ADD COLUMN IF NOT EXISTS stop_verified_at timestamptz NULL,
              ADD COLUMN IF NOT EXISTS submission_id text NULL,
              ADD COLUMN IF NOT EXISTS superseded_at timestamptz NULL,
              ADD COLUMN IF NOT EXISTS error_code text NULL
            """
        )
    )
    # Historical rows without proven stop evidence become unknown (occupy slot).
    op.execute(
        sa.text(
            """
            UPDATE app.instance_attempts ia
            SET status = CASE
              WHEN EXISTS (
                SELECT 1
                FROM app.instance_operations io
                WHERE io.attempt_id = ia.id
                  AND io.kind = 'stop'
                  AND io.status = 'succeeded'
              ) THEN 'stopped'
              ELSE 'unknown'
            END
            WHERE status IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE app.instance_attempts
              ALTER COLUMN status SET DEFAULT 'scheduled',
              ALTER COLUMN status SET NOT NULL
            """
        )
    )

    # Before partial unique indexes: archive duplicate actives (keep newest created_at).
    op.execute(
        sa.text(
            f"""
            WITH ranked AS (
              SELECT id,
                     row_number() OVER (
                       PARTITION BY run_id, work_package_id
                       ORDER BY created_at DESC, id DESC
                     ) AS rn
              FROM app.instance_attempts
              WHERE status IN ({", ".join(_ACTIVE)})
            )
            UPDATE app.instance_attempts ia
            SET status = 'superseded',
                superseded_at = COALESCE(ia.superseded_at, now()),
                error_code = COALESCE(ia.error_code, 'd11_migration_duplicate_active')
            FROM ranked
            WHERE ia.id = ranked.id AND ranked.rn > 1
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            WITH ranked AS (
              SELECT id,
                     row_number() OVER (
                       PARTITION BY instance_id
                       ORDER BY created_at DESC, id DESC
                     ) AS rn
              FROM app.instance_attempts
              WHERE status IN ({", ".join(_ACTIVE)})
            )
            UPDATE app.instance_attempts ia
            SET status = 'superseded',
                superseded_at = COALESCE(ia.superseded_at, now()),
                error_code = COALESCE(ia.error_code, 'd11_migration_duplicate_instance')
            FROM ranked
            WHERE ia.id = ranked.id AND ranked.rn > 1
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS instance_attempts_active_wp_uidx
              ON app.instance_attempts (run_id, work_package_id)
              WHERE status IN ({", ".join(_ACTIVE)})
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS instance_attempts_active_instance_uidx
              ON app.instance_attempts (instance_id)
              WHERE status IN ({", ".join(_ACTIVE)})
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.driver_threads (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                thread_id text NOT NULL,
                plan_version integer NOT NULL,
                assignment_version integer NOT NULL,
                gate_policy_version integer NOT NULL,
                execution_generation integer NOT NULL,
                graph_version text NOT NULL,
                state_schema_version integer NOT NULL,
                checkpoint_epoch integer NOT NULL DEFAULT 1,
                binding_hash text NOT NULL,
                status text NOT NULL,
                latest_checkpoint_id text NULL,
                last_observed_sequence bigint NOT NULL DEFAULT 0,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT driver_threads_thread_id_unique UNIQUE (thread_id),
                CONSTRAINT driver_threads_binding_unique UNIQUE (
                    run_id, plan_version, assignment_version, gate_policy_version,
                    execution_generation, graph_version, state_schema_version,
                    checkpoint_epoch
                )
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS driver_threads_active_run_uidx
              ON app.driver_threads (run_id)
              WHERE status = 'active'
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.scheduler_operations (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                thread_id text NULL,
                kind text NOT NULL,
                operation_key text NOT NULL,
                request_hash text NOT NULL,
                binding_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                status text NOT NULL,
                result_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                error_code text NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT scheduler_operations_run_key_unique
                  UNIQUE (run_id, operation_key)
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.run_waits (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                thread_id text NULL,
                wait_key text NOT NULL,
                reason text NOT NULL,
                scope_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                predicate_version integer NOT NULL,
                after_sequence bigint NOT NULL DEFAULT 0,
                status text NOT NULL,
                deadline_at timestamptz NULL,
                resolved_by_ref text NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                resolved_at timestamptz NULL,
                CONSTRAINT run_waits_run_key_unique UNIQUE (run_id, wait_key)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS run_waits_run_status_deadline_idx
              ON app.run_waits (run_id, status, deadline_at)
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.run_wake_signals (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                source_kind text NOT NULL,
                source_ref text NOT NULL,
                source_sequence bigint NULL,
                binding_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                priority integer NOT NULL DEFAULT 100,
                status text NOT NULL,
                claim_owner text NULL,
                claim_expires_at timestamptz NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT run_wake_signals_source_unique
                  UNIQUE (run_id, source_kind, source_ref)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS run_wake_signals_claim_idx
              ON app.run_wake_signals (status, priority, created_at)
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.recovery_cases (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                request_operation_id text NULL,
                context_hash text NOT NULL,
                context_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                case_version integer NOT NULL DEFAULT 1,
                status text NOT NULL,
                proposed_action text NULL,
                blocker_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
                plan_version integer NULL,
                assignment_version integer NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS recovery_cases_open_request_uidx
              ON app.recovery_cases (request_operation_id)
              WHERE request_operation_id IS NOT NULL
                AND status IN ('open', 'observing', 'awaiting_decision', 'applying', 'blocked')
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.recovery_observations (
                id text PRIMARY KEY,
                case_id text NOT NULL REFERENCES app.recovery_cases(id) ON DELETE CASCADE,
                subject_type text NOT NULL,
                subject_ref text NOT NULL,
                observed_at timestamptz NOT NULL,
                support text NOT NULL,
                result_code text NOT NULL,
                evidence_hash text NOT NULL,
                evidence_ref text NULL,
                binding_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                summary text NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.recovery_decisions (
                id text PRIMARY KEY,
                case_id text NOT NULL REFERENCES app.recovery_cases(id) ON DELETE CASCADE,
                case_version integer NOT NULL,
                decision_key text NOT NULL,
                context_hash text NOT NULL,
                decision text NOT NULL,
                actor text NOT NULL,
                reason text NULL,
                evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT recovery_decisions_case_key_unique
                  UNIQUE (case_id, decision_key)
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.platform_admission (
                environment_id text PRIMARY KEY,
                mode text NOT NULL,
                version integer NOT NULL,
                reason text NULL,
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT platform_admission_mode_check
                  CHECK (mode = ANY (ARRAY['open'::text, 'draining'::text, 'stopped'::text])),
                CONSTRAINT platform_admission_version_check CHECK (version >= 1)
            )
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "20261112_0017 downgrade refused: dropping collaboration driver history "
        "loses waits, wakes, recovery evidence, and attempt lifecycle"
    )
