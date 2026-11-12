"""D09: instance sessions, operations, delivery attempts, protocol, events.

Revision ID: 20261112_0014
Revises: 20261111_0013
Create Date: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_0014"
down_revision: str | None = "20261111_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Agent instances: split lifecycle/activity; keep legacy status; history-safe session.
    op.execute(
        sa.text(
            """
            ALTER TABLE app.agent_instances
              ADD COLUMN IF NOT EXISTS assignment_version integer NULL,
              ADD COLUMN IF NOT EXISTS lifecycle text NULL,
              ADD COLUMN IF NOT EXISTS activity text NULL,
              ADD COLUMN IF NOT EXISTS native_session_id text NULL,
              ADD COLUMN IF NOT EXISTS current_work_package_id text NULL,
              ADD COLUMN IF NOT EXISTS generation integer NULL,
              ADD COLUMN IF NOT EXISTS permission_scope_ref text NULL,
              ADD COLUMN IF NOT EXISTS budget_ref text NULL,
              ADD COLUMN IF NOT EXISTS plan_version integer NULL,
              ADD COLUMN IF NOT EXISTS plan_hash text NULL,
              ADD COLUMN IF NOT EXISTS gate_policy_version integer NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE app.agent_instances
            SET lifecycle = CASE status
                  WHEN 'created' THEN 'created'
                  WHEN 'starting' THEN 'starting'
                  WHEN 'running' THEN 'active'
                  WHEN 'waiting_input' THEN 'active'
                  WHEN 'waiting_approval' THEN 'active'
                  WHEN 'stop_requested' THEN 'stop_requested'
                  WHEN 'stopped' THEN 'stopped'
                  WHEN 'unknown' THEN 'unknown'
                  WHEN 'reconciling' THEN 'needs_reconciliation'
                  WHEN 'blocked' THEN 'active'
                  WHEN 'failed' THEN 'failed'
                  WHEN 'closed' THEN 'closed'
                  ELSE 'unknown'
                END,
                activity = CASE status
                  WHEN 'running' THEN 'busy'
                  WHEN 'waiting_input' THEN 'waiting_input'
                  WHEN 'waiting_approval' THEN 'waiting_approval'
                  WHEN 'stop_requested' THEN 'busy'
                  ELSE 'idle'
                END
            WHERE lifecycle IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE app.agent_instances
              ALTER COLUMN lifecycle SET DEFAULT 'created',
              ALTER COLUMN activity SET DEFAULT 'idle'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE app.agent_instances
            SET lifecycle = COALESCE(lifecycle, 'created'),
                activity = COALESCE(activity, 'idle')
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS agent_instances_run_assignment_role_uidx
              ON app.agent_instances (run_id, assignment_id, role_id)
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS agent_instances_lifecycle_idx
              ON app.agent_instances (lifecycle, activity)
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.native_sessions (
                id text PRIMARY KEY,
                instance_id text NOT NULL
                  REFERENCES app.agent_instances(id) ON DELETE CASCADE,
                product_id text NOT NULL,
                profile_id text NOT NULL,
                config_version text NOT NULL,
                environment_id text NOT NULL,
                session_ref text NULL,
                create_operation_id text NOT NULL,
                binding_status text NOT NULL,
                workspace_id text NULL,
                evidence_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                closed_at timestamptz NULL,
                CONSTRAINT native_sessions_binding_status_check
                  CHECK (binding_status = ANY (ARRAY[
                    'pending'::text, 'active'::text, 'needs_reconciliation'::text,
                    'closed'::text, 'superseded'::text
                  ]))
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS native_sessions_active_per_instance_uidx
              ON app.native_sessions (instance_id)
              WHERE binding_status = 'active'
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS native_sessions_instance_idx "
            "ON app.native_sessions (instance_id, created_at)"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS native_sessions_create_op_uidx
              ON app.native_sessions (create_operation_id)
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.instance_operations (
                id text PRIMARY KEY,
                instance_id text NOT NULL
                  REFERENCES app.agent_instances(id) ON DELETE CASCADE,
                kind text NOT NULL,
                status text NOT NULL,
                idempotency_key text NULL,
                native_ref text NULL,
                attempt_id text NULL,
                claim_owner text NULL,
                claim_token text NULL,
                claim_expires_at timestamptz NULL,
                payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                error_code text NULL,
                prepared_at timestamptz NOT NULL DEFAULT now(),
                completed_at timestamptz NULL,
                CONSTRAINT instance_operations_kind_check
                  CHECK (kind = ANY (ARRAY[
                    'create_session'::text, 'submit_task'::text, 'deliver_message'::text,
                    'request_stop'::text, 'resume'::text, 'close'::text, 'reconcile'::text
                  ])),
                CONSTRAINT instance_operations_status_check
                  CHECK (status = ANY (ARRAY[
                    'prepared'::text, 'claimed'::text, 'native_invoked'::text,
                    'succeeded'::text, 'failed'::text, 'unknown'::text,
                    'needs_reconciliation'::text
                  ]))
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS instance_operations_idempotency_uidx
              ON app.instance_operations (instance_id, kind, idempotency_key)
              WHERE idempotency_key IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS instance_operations_claim_idx
              ON app.instance_operations (status, claim_expires_at)
              WHERE status IN ('prepared', 'claimed')
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.instance_attempts (
                id text PRIMARY KEY,
                instance_id text NOT NULL
                  REFERENCES app.agent_instances(id) ON DELETE CASCADE,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                work_package_id text NOT NULL,
                stage_id text NULL,
                generation integer NOT NULL,
                workspace_id text NOT NULL,
                native_session_id text NULL
                  REFERENCES app.native_sessions(id) ON DELETE SET NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT instance_attempts_generation_check CHECK (generation >= 1)
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS instance_attempts_instance_idx "
            "ON app.instance_attempts (instance_id, created_at)"
        )
    )

    # Messages / deliveries: transmission vs reply; recipient refs; request id.
    op.execute(
        sa.text(
            """
            ALTER TABLE app.collaboration_messages
              ADD COLUMN IF NOT EXISTS project_id text NULL,
              ADD COLUMN IF NOT EXISTS recipient_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
              ADD COLUMN IF NOT EXISTS artifact_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
              ADD COLUMN IF NOT EXISTS reply_required boolean NOT NULL DEFAULT false,
              ADD COLUMN IF NOT EXISTS reply_to text NULL,
              ADD COLUMN IF NOT EXISTS request_id text NULL,
              ADD COLUMN IF NOT EXISTS correlation_id text NULL,
              ADD COLUMN IF NOT EXISTS expires_at timestamptz NULL,
              ADD COLUMN IF NOT EXISTS derived_from_gate_decision_id text NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE app.message_deliveries
              ADD COLUMN IF NOT EXISTS transmission_status text NULL,
              ADD COLUMN IF NOT EXISTS reply_status text NULL,
              ADD COLUMN IF NOT EXISTS delivery_mode text NULL,
              ADD COLUMN IF NOT EXISTS received_at timestamptz NULL,
              ADD COLUMN IF NOT EXISTS responded_at timestamptz NULL,
              ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz NULL,
              ADD COLUMN IF NOT EXISTS claim_owner text NULL,
              ADD COLUMN IF NOT EXISTS claim_token text NULL,
              ADD COLUMN IF NOT EXISTS claim_expires_at timestamptz NULL,
              ADD COLUMN IF NOT EXISTS assignment_version integer NULL,
              ADD COLUMN IF NOT EXISTS plan_version integer NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE app.message_deliveries
            SET transmission_status = CASE status
                  WHEN 'pending' THEN 'pending'
                  WHEN 'dispatching' THEN 'claimed'
                  WHEN 'sent' THEN 'sent'
                  WHEN 'received' THEN 'received'
                  WHEN 'awaiting_reply' THEN 'received'
                  WHEN 'responded' THEN 'received'
                  WHEN 'failed' THEN 'failed'
                  WHEN 'expired' THEN 'expired'
                  WHEN 'unknown' THEN 'unknown'
                  ELSE 'unknown'
                END,
                reply_status = CASE status
                  WHEN 'awaiting_reply' THEN 'awaiting_reply'
                  WHEN 'responded' THEN 'responded'
                  ELSE 'not_required'
                END
            WHERE transmission_status IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS message_deliveries_claim_idx
              ON app.message_deliveries (transmission_status, claim_expires_at)
              WHERE transmission_status IN ('pending', 'claimed')
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.delivery_attempts (
                id text PRIMARY KEY,
                delivery_id text NOT NULL
                  REFERENCES app.message_deliveries(id) ON DELETE CASCADE,
                operation_id text NOT NULL,
                transmission_status text NOT NULL,
                native_receipt_ref text NULL,
                error_code text NULL,
                started_at timestamptz NOT NULL DEFAULT now(),
                finished_at timestamptz NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS delivery_attempts_delivery_idx "
            "ON app.delivery_attempts (delivery_id, started_at)"
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.protocol_requests (
                id text PRIMARY KEY,
                request_type text NOT NULL,
                allowed_response_types jsonb NOT NULL DEFAULT '[]'::jsonb,
                instance_id text NOT NULL
                  REFERENCES app.agent_instances(id) ON DELETE CASCADE,
                native_session_id text NULL,
                attempt_id text NULL,
                generation integer NULL,
                plan_version integer NULL,
                assignment_version integer NULL,
                stage_id text NULL,
                status text NOT NULL,
                initiated_by text NOT NULL,
                legal_responder_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                matched_response_ref text NULL,
                conflict_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                deadline_at timestamptz NULL,
                CONSTRAINT protocol_requests_status_check
                  CHECK (status = ANY (ARRAY[
                    'pending'::text, 'matched'::text, 'timed_out'::text,
                    'conflict'::text, 'cancelled'::text, 'failed'::text
                  ]))
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS protocol_requests_instance_idx "
            "ON app.protocol_requests (instance_id, status)"
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.native_event_receipts (
                id text PRIMARY KEY,
                instance_id text NOT NULL
                  REFERENCES app.agent_instances(id) ON DELETE CASCADE,
                native_session_id text NULL,
                source text NOT NULL,
                dedupe_key text NOT NULL,
                cursor text NULL,
                event_type text NOT NULL,
                normalized_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                authentic boolean NOT NULL DEFAULT false,
                received_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT native_event_receipts_dedupe_uidx UNIQUE (instance_id, source, dedupe_key)
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS native_event_receipts_cursor_idx "
            "ON app.native_event_receipts (instance_id, source, cursor)"
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.execution_requests (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                idempotency_key text NOT NULL,
                plan_hash text NOT NULL,
                plan_version integer NOT NULL,
                assignment_version integer NOT NULL,
                gate_policy_version integer NOT NULL,
                status text NOT NULL,
                actor text NOT NULL,
                result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT execution_requests_run_idempotency_uidx
                  UNIQUE (run_id, idempotency_key)
            )
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "20261112_0014 downgrade refused: dropping instance/session/delivery "
        "history loses reconciliation evidence"
    )
