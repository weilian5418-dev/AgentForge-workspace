"""Isolate ambiguous wait statuses left by historical 0003/0004 rewrites.

Revision ID: 20261110_0006
Revises: 20261110_0005
Create Date: 2026-11-11

Does not guess the correct wait type. Snapshots every run currently in
awaiting_approval or awaiting_confirmation into a reconciliation ledger,
moves those runs to blocked, and always records a batch audit row (even when
the candidate set is empty). Operators resolve rows before unblocking.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261110_0006"
down_revision: str | None = "20261110_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ID = "recon_batch_20261110_0006"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.run_status_reconciliation_batches (
                id text PRIMARY KEY,
                checked_at timestamptz NOT NULL DEFAULT now(),
                candidate_count integer NOT NULL,
                note text NOT NULL,
                source_revision text NOT NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS app.run_status_reconciliations (
                id text PRIMARY KEY,
                batch_id text NOT NULL
                    REFERENCES app.run_status_reconciliation_batches(id),
                run_id text NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
                observed_status text NOT NULL,
                previous_status_version integer NOT NULL,
                isolation_status text NOT NULL DEFAULT 'blocked',
                resolution_status text NOT NULL DEFAULT 'pending',
                evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                resolved_at timestamptz NULL,
                resolved_note text NULL,
                CONSTRAINT run_status_reconciliations_resolution_check CHECK (
                    resolution_status = ANY (ARRAY[
                        'pending'::text, 'resolved'::text, 'dismissed'::text
                    ])
                )
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS run_status_reconciliations_pending_idx "
            "ON app.run_status_reconciliations (resolution_status, run_id)"
        )
    )

    # Always record a batch (candidate_count may be 0).
    op.execute(
        sa.text(
            f"""
            INSERT INTO app.run_status_reconciliation_batches (
                id, candidate_count, note, source_revision
            )
            VALUES (
                '{_BATCH_ID}',
                (
                    SELECT COUNT(*)::integer FROM app.runs
                    WHERE status IN ('awaiting_approval', 'awaiting_confirmation')
                ),
                'Historical wait-status audit after 0003/0004; '
                || 'candidates isolated to blocked until operator resolution',
                '20261110_0006'
            )
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            INSERT INTO app.run_status_reconciliations (
                id,
                batch_id,
                run_id,
                observed_status,
                previous_status_version,
                isolation_status,
                resolution_status,
                evidence
            )
            SELECT
                'recon_' || r.id,
                '{_BATCH_ID}',
                r.id,
                r.status,
                r.status_version,
                'blocked',
                'pending',
                jsonb_build_object(
                    'planStatuses',
                    COALESCE(
                        (
                            SELECT jsonb_agg(
                                jsonb_build_object(
                                    'version', p.version,
                                    'status', p.status,
                                    'contractHash', p.contract_hash
                                )
                                ORDER BY p.version
                            )
                            FROM app.plans p
                            WHERE p.run_id = r.id
                        ),
                        '[]'::jsonb
                    ),
                    'planVersion', r.plan_version,
                    'assignmentVersion', r.assignment_version,
                    'note',
                    'Ambiguous after historical wait-status migrations; '
                    || 'do not interpret observed_status as authoritative'
                )
            FROM app.runs r
            WHERE r.status IN ('awaiting_approval', 'awaiting_confirmation')
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            UPDATE app.runs AS r
            SET
                status = 'blocked',
                status_version = r.status_version + 1,
                updated_at = now()
            WHERE r.status IN ('awaiting_approval', 'awaiting_confirmation')
              AND EXISTS (
                SELECT 1 FROM app.run_status_reconciliations x
                WHERE x.run_id = r.id
                  AND x.batch_id = '{_BATCH_ID}'
                  AND x.resolution_status = 'pending'
              )
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "20261110_0006 downgrade refused; restoring blocked runs without operator "
        "resolution would reintroduce ambiguous wait semantics. Restore from backup."
    )
