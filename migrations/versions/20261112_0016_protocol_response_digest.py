"""Bind protocol duplicate responses to canonical content.

Revision ID: 20261112_0016
Revises: 20261112_0015
"""

import sqlalchemy as sa
from alembic import op

revision = "20261112_0016"
down_revision = "20261112_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "protocol_requests",
        sa.Column("matched_response_digest", sa.Text(), nullable=True),
        schema="app",
    )


def downgrade() -> None:
    # Losing the dedupe hash would allow conflicting historical replies to appear identical.
    raise RuntimeError(
        "Use a verified backup or forward repair; protocol audit digests must be retained"
    )
