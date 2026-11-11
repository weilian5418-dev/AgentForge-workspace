"""Baseline for existing app schema tables.

Revision ID: 20261110_0001
Revises:
Create Date: 2026-11-10

This revision documents the 10 app tables already present in the reconciled database.
upgrade() creates them only when missing so greenfield installs work; existing databases
with data are stamped or upgraded as a no-op create path. downgrade is refused to avoid
destroying live data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261110_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BASELINE_TABLES = (
    "projects",
    "runs",
    "plans",
    "work_packages",
    "task_attempts",
    "events",
    "outbox",
    "run_leases",
    "idempotency_requests",
    "schema_migrations",
)


def upgrade() -> None:
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS app"))
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names(schema="app"))
    if set(_BASELINE_TABLES).issubset(existing):
        return

    if "projects" not in existing:
        op.create_table(
            "projects",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("directory_grant_id", sa.Text(), nullable=False),
            sa.Column("display_name", sa.Text(), nullable=True),
            sa.Column("root_path", sa.Text(), nullable=False),
            sa.Column("workspace_root", sa.Text(), nullable=False),
            sa.Column(
                "trust_policy",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
            sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.CheckConstraint("revision >= 1", name="projects_revision_check"),
            sa.PrimaryKeyConstraint("id", name="projects_pkey"),
            schema="app",
        )

    if "runs" not in existing:
        op.create_table(
            "runs",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("project_id", sa.Text(), nullable=False),
            sa.Column("goal", sa.Text(), nullable=False),
            sa.Column("mode", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("status_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
            sa.Column("plan_version", sa.Integer(), nullable=True),
            sa.Column(
                "active_generation", sa.Integer(), server_default=sa.text("1"), nullable=False
            ),
            sa.Column("last_sequence", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("execution_profile", sa.Text(), nullable=False),
            sa.Column("failure_policy", sa.Text(), nullable=False),
            sa.Column("budget", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("provider_profile_id", sa.Text(), nullable=False),
            sa.Column(
                "input_refs",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'[]'::jsonb"),
                nullable=False,
            ),
            sa.Column("outputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.CheckConstraint("active_generation >= 1", name="runs_active_generation_check"),
            sa.CheckConstraint(
                "execution_profile = ANY (ARRAY['container'::text, 'host_authorized'::text])",
                name="runs_execution_profile_check",
            ),
            sa.CheckConstraint(
                "failure_policy = ANY (ARRAY['dependency-aware'::text, 'layer-atomic'::text])",
                name="runs_failure_policy_check",
            ),
            sa.CheckConstraint("char_length(btrim(goal)) > 0", name="runs_goal_check"),
            sa.CheckConstraint("last_sequence >= 0", name="runs_last_sequence_check"),
            sa.CheckConstraint(
                "mode = ANY (ARRAY['auto'::text, 'single'::text, 'team'::text])",
                name="runs_mode_check",
            ),
            sa.CheckConstraint(
                "plan_version IS NULL OR plan_version >= 1", name="runs_plan_version_check"
            ),
            sa.CheckConstraint(
                "status = ANY (ARRAY["
                "'draft'::text, 'preflight'::text, 'routing'::text, 'planning'::text, "
                "'awaiting_approval'::text, 'running'::text, 'awaiting_acceptance'::text, "
                "'completed'::text, 'pause_requested'::text, 'paused'::text, 'blocked'::text, "
                "'cancel_requested'::text, 'canceled'::text, 'failed'::text])",
                name="runs_status_check",
            ),
            sa.CheckConstraint("status_version >= 1", name="runs_status_version_check"),
            sa.ForeignKeyConstraint(
                ["project_id"], ["app.projects.id"], name="runs_project_id_fkey"
            ),
            sa.PrimaryKeyConstraint("id", name="runs_pkey"),
            schema="app",
        )
        op.create_index("runs_project_id_idx", "runs", ["project_id"], schema="app")
        op.create_index("runs_status_idx", "runs", ["status"], schema="app")

    if "plans" not in existing:
        op.create_table(
            "plans",
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("contract_hash", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("summary", sa.Text(), server_default=sa.text("''::text"), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.CheckConstraint(
                "status = ANY (ARRAY['proposed'::text, 'approved'::text, 'rejected'::text, 'superseded'::text])",
                name="plans_status_check",
            ),
            sa.CheckConstraint("version >= 1", name="plans_version_check"),
            sa.ForeignKeyConstraint(
                ["run_id"],
                ["app.runs.id"],
                name="plans_run_id_fkey",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("run_id", "version", name="plans_pkey"),
            schema="app",
        )

    if "work_packages" not in existing:
        op.create_table(
            "work_packages",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("plan_version", sa.Integer(), nullable=False),
            sa.Column("owner_role", sa.Text(), nullable=False),
            sa.Column("objective", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("repair_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
            sa.Column(
                "write_scopes",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'[]'::jsonb"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.CheckConstraint("plan_version >= 1", name="work_packages_plan_version_check"),
            sa.CheckConstraint("repair_count >= 0", name="work_packages_repair_count_check"),
            sa.CheckConstraint(
                "status = ANY (ARRAY["
                "'pending'::text, 'ready'::text, 'running'::text, 'validating'::text, "
                "'published'::text, 'repairing'::text, 'failed'::text, 'blocked'::text, "
                "'blocked_dependency'::text])",
                name="work_packages_status_check",
            ),
            sa.ForeignKeyConstraint(
                ["run_id", "plan_version"],
                ["app.plans.run_id", "app.plans.version"],
                name="work_packages_plan_fk",
            ),
            sa.PrimaryKeyConstraint("id", name="work_packages_pkey"),
            schema="app",
        )
        op.create_index(
            "work_packages_run_plan_idx",
            "work_packages",
            ["run_id", "plan_version"],
            schema="app",
        )

    if "task_attempts" not in existing:
        op.create_table(
            "task_attempts",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("work_package_id", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("run_generation", sa.Integer(), nullable=False),
            sa.Column("lease_token", sa.Text(), nullable=False),
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
            sa.Column("worker_ref", sa.Text(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("retry_reason", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.CheckConstraint("run_generation >= 1", name="task_attempts_run_generation_check"),
            sa.CheckConstraint(
                "status = ANY (ARRAY["
                "'leased'::text, 'running'::text, 'succeeded'::text, 'failed'::text, "
                "'rejected'::text, 'awaiting_stop_verification'::text])",
                name="task_attempts_status_check",
            ),
            sa.ForeignKeyConstraint(
                ["run_id"],
                ["app.runs.id"],
                name="task_attempts_run_id_fkey",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["work_package_id"],
                ["app.work_packages.id"],
                name="task_attempts_work_package_id_fkey",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="task_attempts_pkey"),
            schema="app",
        )
        op.create_index(
            "task_attempts_work_package_idx",
            "task_attempts",
            ["work_package_id"],
            schema="app",
        )
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX task_attempts_active_unique "
                "ON app.task_attempts USING btree (work_package_id) "
                "WHERE (status = ANY (ARRAY['leased'::text, 'running'::text, "
                "'awaiting_stop_verification'::text]))"
            )
        )

    if "events" not in existing:
        op.create_table(
            "events",
            sa.Column("event_id", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("type", sa.Text(), nullable=False),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.CheckConstraint("sequence >= 1", name="events_sequence_check"),
            sa.ForeignKeyConstraint(
                ["run_id"],
                ["app.runs.id"],
                name="events_run_id_fkey",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("event_id", name="events_pkey"),
            sa.UniqueConstraint("run_id", "sequence", name="events_run_id_sequence_key"),
            schema="app",
        )
        op.create_index("events_run_sequence_idx", "events", ["run_id", "sequence"], schema="app")

    if "outbox" not in existing:
        op.create_table(
            "outbox",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=True),
            sa.Column("event_id", sa.Text(), nullable=True),
            sa.Column("intent_type", sa.Text(), nullable=False),
            sa.Column("target", sa.Text(), nullable=False),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("idempotency_key", sa.Text(), nullable=False),
            sa.Column("delivery_status", sa.Text(), nullable=False),
            sa.Column("claim_owner", sa.Text(), nullable=True),
            sa.Column("claim_token", sa.Text(), nullable=True),
            sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.CheckConstraint("attempt_count >= 0", name="outbox_attempt_count_check"),
            sa.CheckConstraint(
                "delivery_status = ANY (ARRAY["
                "'pending'::text, 'claimed'::text, 'dispatched'::text, 'failed'::text, 'dead'::text])",
                name="outbox_delivery_status_check",
            ),
            sa.ForeignKeyConstraint(
                ["event_id"],
                ["app.events.event_id"],
                name="outbox_event_id_fkey",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["run_id"],
                ["app.runs.id"],
                name="outbox_run_id_fkey",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="outbox_pkey"),
            sa.UniqueConstraint("idempotency_key", name="outbox_idempotency_key_key"),
            schema="app",
        )
        op.execute(
            sa.text(
                "CREATE INDEX outbox_pending_idx ON app.outbox "
                "USING btree (delivery_status, next_retry_at) "
                "WHERE (delivery_status = ANY (ARRAY['pending'::text, 'failed'::text]))"
            )
        )

    if "run_leases" not in existing:
        op.create_table(
            "run_leases",
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("owner", sa.Text(), nullable=False),
            sa.Column("generation", sa.Integer(), nullable=False),
            sa.Column("token", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.CheckConstraint("generation >= 1", name="run_leases_generation_check"),
            sa.ForeignKeyConstraint(
                ["run_id"],
                ["app.runs.id"],
                name="run_leases_run_id_fkey",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("run_id", name="run_leases_pkey"),
            schema="app",
        )

    if "idempotency_requests" not in existing:
        op.create_table(
            "idempotency_requests",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("identity_scope", sa.Text(), nullable=False),
            sa.Column("operation_type", sa.Text(), nullable=False),
            sa.Column("request_key", sa.Text(), nullable=False),
            sa.Column("request_hash", sa.Text(), nullable=False),
            sa.Column("response_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id", name="idempotency_requests_pkey"),
            sa.UniqueConstraint(
                "identity_scope",
                "operation_type",
                "request_key",
                name="idempotency_requests_identity_scope_operation_type_request__key",
            ),
            schema="app",
        )

    if "schema_migrations" not in existing:
        op.create_table(
            "schema_migrations",
            sa.Column("version", sa.Text(), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("checksum", sa.Text(), nullable=False),
            sa.Column(
                "applied_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("version", name="schema_migrations_pkey"),
            schema="app",
        )


def downgrade() -> None:
    raise RuntimeError(
        "Baseline downgrade is refused; restore from backup instead of dropping app tables"
    )
