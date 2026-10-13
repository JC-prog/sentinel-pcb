"""add a pending relabel to inspection_drafts

A relabel can now be proposed on an inspection that is not a Case yet, so the proposal waits on the
draft the same way it waits on a Case (`pending_*`). Confirming it in a later turn creates the Case,
the correction and the retraining ticket in one commit. Existing drafts have no proposal.

Revision ID: e5a9c3b7d216
Revises: d4a1c6e8b2f7
Create Date: 2026-10-13 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5a9c3b7d216"
down_revision: str | Sequence[str] | None = "d4a1c6e8b2f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("inspection_drafts", sa.Column("pending_label", sa.String(), nullable=True))
    op.add_column("inspection_drafts", sa.Column("pending_reason", sa.String(), nullable=True))
    op.add_column(
        "inspection_drafts", sa.Column("pending_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("inspection_drafts", "pending_at")
    op.drop_column("inspection_drafts", "pending_reason")
    op.drop_column("inspection_drafts", "pending_label")
