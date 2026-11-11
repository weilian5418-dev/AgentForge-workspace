"""Document status provenance: no speculative wait-state rewrite.

Revision ID: 20261110_0005
Revises: 20261110_0004
Create Date: 2026-11-11

A prior draft of 0004 rewrote awaiting_confirmation → awaiting_approval using
"no approved plan" as evidence. That confuses legitimate plan waits (proposed
plans) with action-auth waits, and leaves action waits that already had an
approved plan stuck as awaiting_confirmation.

This revision does **not** invent another heuristic. Existing rows keep their
status. New code must treat:
- awaiting_confirmation = plan confirmation wait
- awaiting_approval = action/native auth wait (legacy)
without silently mapping between them. Ambiguous historical rows need manual
or evidence-backed reconciliation, not automatic rewrite.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261110_0005"
down_revision: str | None = "20261110_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No status UPDATEs here. Isolation + audit ledger for historically ambiguous
    # wait rows is implemented in 20261110_0006 (forward migration).
    op.execute("SELECT 1")


def downgrade() -> None:
    raise RuntimeError(
        "20261110_0005 downgrade refused; restore from backup. "
        "No schema objects to reverse; do not invent status rewrites."
    )
