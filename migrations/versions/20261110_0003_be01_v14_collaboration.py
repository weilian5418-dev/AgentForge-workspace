"""BE-01/02 v1.4: planning profile rename, statuses, collaboration objects.

Revision ID: 20261110_0003
Revises: 20261110_0002
Create Date: 2026-11-10

Does not modify previously applied migrations. Legacy status mapping:
- routing → planning only.

awaiting_approval must NOT be bulk-rewritten to awaiting_confirmation (plan vs action
auth are distinct). An earlier draft of this revision rewrote those rows; corrective
logic lives in 20261110_0004. Downgrade refuses destructive drops — restore from backup.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261110_0003"
down_revision: str | None = "20261110_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- runs: rename planning profile + expand status ---
    op.execute(
        sa.text(
            "ALTER TABLE app.runs RENAME COLUMN provider_profile_id TO planning_provider_profile_id"
        )
    )
    op.execute(
        sa.text("ALTER TABLE app.runs ADD COLUMN IF NOT EXISTS assignment_version integer NULL")
    )
    op.execute(
        sa.text("ALTER TABLE app.runs ADD COLUMN IF NOT EXISTS gate_policy_version integer NULL")
    )
    op.execute(sa.text("ALTER TABLE app.runs DROP CONSTRAINT IF EXISTS runs_status_check"))
    op.execute(
        sa.text(
            "ALTER TABLE app.runs ADD CONSTRAINT runs_status_check CHECK ("
            "status = ANY (ARRAY["
            "'draft'::text, 'preflight'::text, 'routing'::text, 'planning'::text, "
            "'awaiting_approval'::text, 'awaiting_confirmation'::text, 'preparing'::text, "
            "'running'::text, 'awaiting_acceptance'::text, "
            "'completed'::text, 'pause_requested'::text, 'paused'::text, 'blocked'::text, "
            "'cancel_requested'::text, 'canceled'::text, 'failed'::text]))"
        )
    )
    # routing → planning only. Do not rewrite awaiting_approval (action-auth wait).
    op.execute(sa.text("UPDATE app.runs SET status = 'planning' WHERE status = 'routing'"))

    # --- work_packages / task_attempts ---
    op.execute(sa.text("ALTER TABLE app.work_packages ADD COLUMN IF NOT EXISTS stage_id text NULL"))
    op.execute(
        sa.text("ALTER TABLE app.work_packages ADD COLUMN IF NOT EXISTS owner_role_id text NULL")
    )
    op.execute(
        sa.text(
            "ALTER TABLE app.task_attempts ADD COLUMN IF NOT EXISTS agent_instance_id text NULL"
        )
    )
    op.execute(
        sa.text("ALTER TABLE app.task_attempts ADD COLUMN IF NOT EXISTS session_ref text NULL")
    )

    # --- collaboration tables ---
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.agent_products (
                id text PRIMARY KEY,
                name text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.agent_installations (
                id text PRIMARY KEY,
                product_id text NOT NULL,
                product_version text NOT NULL,
                environment_id text NOT NULL,
                location_ref text NOT NULL,
                discovered_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS agent_installations_product_idx "
            "ON app.agent_installations (product_id)"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.agent_profiles (
                id text PRIMARY KEY,
                installation_id text NOT NULL,
                adapter_id text NOT NULL,
                adapter_version text NOT NULL,
                config_version text NOT NULL,
                config_hash text NOT NULL,
                auth_status text NOT NULL,
                access_level text NOT NULL,
                last_validation_id text NULL,
                secret_ref text NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.role_assignments (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                plan_version integer NOT NULL,
                assignment_version integer NOT NULL,
                role_id text NOT NULL,
                agent_profile_id text NOT NULL,
                confirmed_at timestamptz NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE (run_id, assignment_version, role_id)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.agent_instances (
                id text PRIMARY KEY,
                assignment_id text NOT NULL,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                role_id text NOT NULL,
                agent_profile_id text NOT NULL,
                session_ref text NULL,
                workspace_id text NULL,
                stage_id text NULL,
                status text NOT NULL,
                execution_environment text NOT NULL,
                current_attempt_id text NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.stages (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                plan_version integer NOT NULL,
                gate_policy jsonb NOT NULL,
                status text NOT NULL,
                repair_round integer NOT NULL DEFAULT 0,
                current_candidate_id text NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.gate_decisions (
                id text PRIMARY KEY,
                stage_id text NOT NULL,
                candidate_id text NOT NULL,
                manifest_hash text NOT NULL,
                contract_hash text NOT NULL,
                gate_policy_version integer NOT NULL,
                validation_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                review_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                result text NOT NULL,
                reason text NOT NULL,
                decided_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.collaboration_messages (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                stage_id text NULL,
                work_package_id text NULL,
                message_type text NOT NULL,
                sender_ref text NOT NULL,
                payload jsonb NOT NULL,
                plan_version integer NULL,
                assignment_version integer NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.message_deliveries (
                id text PRIMARY KEY,
                message_id text NOT NULL,
                recipient_instance_id text NOT NULL,
                attempt_id text NOT NULL,
                status text NOT NULL,
                native_receipt_ref text NULL,
                retry_count integer NOT NULL DEFAULT 0,
                error_code text NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE (message_id, recipient_instance_id, attempt_id)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.approval_mappings (
                id text PRIMARY KEY,
                run_id text NULL,
                instance_id text NULL,
                role_id text NULL,
                stage_id text NULL,
                attempt_id text NULL,
                action_digest text NOT NULL,
                action text NOT NULL,
                native_request_id text NULL,
                status text NOT NULL,
                user_decision text NULL,
                native_confirmed boolean NOT NULL DEFAULT false,
                mapping_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                expires_at timestamptz NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "BE-01/02 v1.4 (0003) downgrade refused: dropping collaboration tables would "
        "destroy approvals, gate decisions, and message receipts without recoverable "
        "semantics. Restore from backup or apply a forward fix; do not alembic downgrade."
    )
