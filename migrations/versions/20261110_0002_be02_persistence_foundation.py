"""BE-02 persistence foundation: constraints, sequence bigint, outbox/idempotency columns.

Revision ID: 20261110_0002
Revises: 20261110_0001
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261110_0002"
down_revision: str | None = "20261110_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Event/run sequences: prefer BIGINT for high-frequency allocation.
    op.execute(sa.text("ALTER TABLE app.events ALTER COLUMN sequence TYPE BIGINT"))
    op.execute(sa.text("ALTER TABLE app.runs ALTER COLUMN last_sequence TYPE BIGINT"))

    op.execute(
        sa.text(
            "ALTER TABLE app.events "
            "ADD COLUMN IF NOT EXISTS schema_version text NOT NULL DEFAULT '1'"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE app.events "
            "ADD CONSTRAINT events_schema_version_check "
            "CHECK (schema_version = '1') NOT VALID"
        )
    )
    op.execute(sa.text("ALTER TABLE app.events VALIDATE CONSTRAINT events_schema_version_check"))

    # Cross-run consistency for attempts: composite FK via (id, run_id).
    op.execute(
        sa.text(
            "ALTER TABLE app.work_packages "
            "ADD CONSTRAINT work_packages_id_run_id_key UNIQUE (id, run_id)"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE app.task_attempts DROP CONSTRAINT IF EXISTS task_attempts_work_package_id_fkey"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE app.task_attempts "
            "ADD CONSTRAINT task_attempts_work_package_run_fk "
            "FOREIGN KEY (work_package_id, run_id) "
            "REFERENCES app.work_packages (id, run_id) ON DELETE CASCADE"
        )
    )

    op.execute(sa.text("ALTER TABLE app.outbox ADD COLUMN IF NOT EXISTS last_error text"))
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS outbox_claimed_expired_idx "
            "ON app.outbox (claim_expires_at) "
            "WHERE delivery_status = 'claimed'"
        )
    )

    op.execute(
        sa.text(
            "ALTER TABLE app.idempotency_requests "
            "ADD COLUMN IF NOT EXISTS state text NOT NULL DEFAULT 'completed'"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE app.idempotency_requests "
            "ADD COLUMN IF NOT EXISTS response_status_code integer"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE app.idempotency_requests ADD COLUMN IF NOT EXISTS processing_owner text"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE app.idempotency_requests ADD COLUMN IF NOT EXISTS processing_token text"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE app.idempotency_requests "
            "ADD COLUMN IF NOT EXISTS processing_expires_at timestamptz"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE app.idempotency_requests "
            "ADD CONSTRAINT idempotency_requests_state_check "
            "CHECK (state = ANY (ARRAY['processing'::text, 'completed'::text])) NOT VALID"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE app.idempotency_requests VALIDATE CONSTRAINT idempotency_requests_state_check"
        )
    )
    op.execute(
        sa.text(
            "UPDATE app.idempotency_requests SET response_status_code = 200 "
            "WHERE response_status_code IS NULL AND state = 'completed'"
        )
    )

    # Tighten historical audit table: services read-only.
    op.execute(
        sa.text(
            "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON app.schema_migrations FROM agentforge_api"
        )
    )
    op.execute(
        sa.text(
            "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON app.schema_migrations FROM agentforge_driver"
        )
    )
    op.execute(
        sa.text("GRANT SELECT ON app.schema_migrations TO agentforge_api, agentforge_driver")
    )


def downgrade() -> None:
    raise RuntimeError(
        "BE-02 downgrade refused; restore from backup if rolling back persistence foundation"
    )
