"""add retraining_tickets

Revision ID: ea8ea3053c3c
Revises: 288f8bcaed40
Create Date: 2026-09-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ea8ea3053c3c"
down_revision: str | Sequence[str] | None = "288f8bcaed40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "retraining_tickets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("flagged_by_user_id", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["flagged_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_retraining_tickets_case_id"), "retraining_tickets", ["case_id"], unique=False
    )
    op.create_index(
        op.f("ix_retraining_tickets_status"), "retraining_tickets", ["status"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_retraining_tickets_status"), table_name="retraining_tickets")
    op.drop_index(op.f("ix_retraining_tickets_case_id"), table_name="retraining_tickets")
    op.drop_table("retraining_tickets")
