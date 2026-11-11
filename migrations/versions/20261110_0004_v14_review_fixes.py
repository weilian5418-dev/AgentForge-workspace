"""v1.4 review fixes: status reconciliation, FKs, missing persistence.

Revision ID: 20261110_0004
Revises: 20261110_0003
Create Date: 2026-11-11

Corrects 0003 issues without rewriting 0001/0002:
- Add FKs for message_deliveries and gate_decisions.
- Persist CapabilityCheck / StageSubmission / ReviewReport / IntegrationVersion.

Status note: do **not** rewrite awaiting_approval ↔ awaiting_confirmation here.
Plan confirmation vs action-auth waits are distinct; without migration-source
evidence, leave rows unchanged (see 0005 documentation / app LEGACY map).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261110_0004"
down_revision: str | None = "20261110_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Status waits are NOT rewritten in this revision. Inferring plan-confirmation vs
    # action-auth from plan.approved / proposed is incorrect (reproduced in review).
    # Keep current status when provenance is unknown.

    # --- F04: foreign keys ---
    op.execute(
        sa.text(
            """
            ALTER TABLE app.message_deliveries
            ADD CONSTRAINT message_deliveries_message_id_fkey
            FOREIGN KEY (message_id) REFERENCES app.collaboration_messages(id)
            ON DELETE CASCADE
            NOT VALID
            """
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE app.message_deliveries VALIDATE CONSTRAINT "
            "message_deliveries_message_id_fkey"
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE app.message_deliveries
            ADD CONSTRAINT message_deliveries_recipient_instance_id_fkey
            FOREIGN KEY (recipient_instance_id) REFERENCES app.agent_instances(id)
            ON DELETE CASCADE
            NOT VALID
            """
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE app.message_deliveries VALIDATE CONSTRAINT "
            "message_deliveries_recipient_instance_id_fkey"
        )
    )
    # Delivery attempt_id is a delivery-ledger identity (not task_attempts); no FK.
    op.execute(
        sa.text(
            """
            ALTER TABLE app.gate_decisions
            ADD CONSTRAINT gate_decisions_stage_id_fkey
            FOREIGN KEY (stage_id) REFERENCES app.stages(id)
            ON DELETE CASCADE
            NOT VALID
            """
        )
    )
    op.execute(
        sa.text("ALTER TABLE app.gate_decisions VALIDATE CONSTRAINT gate_decisions_stage_id_fkey")
    )

    # --- F04: missing persistence structures ---
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.capability_checks (
                id text PRIMARY KEY,
                profile_id text NOT NULL REFERENCES app.agent_profiles(id) ON DELETE CASCADE,
                product_version text NOT NULL,
                adapter_id text NOT NULL,
                adapter_version text NOT NULL,
                environment_id text NOT NULL,
                checked_at timestamptz NOT NULL,
                valid_until timestamptz NULL,
                results jsonb NOT NULL,
                evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.stage_submissions (
                id text PRIMARY KEY,
                stage_id text NOT NULL REFERENCES app.stages(id) ON DELETE CASCADE,
                attempt_refs jsonb NOT NULL,
                stop_evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                candidate_id text NOT NULL,
                manifest_hash text NOT NULL,
                submitted_at timestamptz NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.review_reports (
                id text PRIMARY KEY,
                reviewer_instance_id text NOT NULL REFERENCES app.agent_instances(id),
                stage_id text NOT NULL REFERENCES app.stages(id) ON DELETE CASCADE,
                candidate_id text NOT NULL,
                manifest_hash text NOT NULL,
                contract_hash text NOT NULL,
                findings jsonb NOT NULL DEFAULT '[]'::jsonb,
                verdict text NOT NULL,
                created_at timestamptz NOT NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.integration_versions (
                id text PRIMARY KEY,
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                input_candidate_ids jsonb NOT NULL,
                commit_or_tree text NULL,
                content_hash text NOT NULL,
                environment_ref text NOT NULL,
                validation_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                review_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "20261110_0004 downgrade refused; restore from backup. "
        "Forward-fix status/FK/persistence instead of dropping collaboration evidence."
    )
