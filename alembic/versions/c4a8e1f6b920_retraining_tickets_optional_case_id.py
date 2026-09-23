"""make retraining_tickets.case_id optional, add sample_ref

A RetrainingTicket has always required a chat Case to point at. The Work tab's bulk orchestrator
has no Case row for a flagged dataset sample, so case_id becomes optional and a new sample_ref
column (the sample's sample_id) stands in for it - a CHECK constraint enforces that a ticket
always has at least one of the two.

Revision ID: c4a8e1f6b920
Revises: b1f3c9d2a7e4
Create Date: 2026-09-22 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a8e1f6b920"
down_revision: str | Sequence[str] | None = "b1f3c9d2a7e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "retraining_tickets", "case_id", existing_type=sa.String(), nullable=True
    )
    op.add_column("retraining_tickets", sa.Column("sample_ref", sa.String(), nullable=True))
    op.create_check_constraint(
        "ck_retraining_tickets_case_or_sample",
        "retraining_tickets",
        "case_id IS NOT NULL OR sample_ref IS NOT NULL",
    )


def downgrade() -> None:
    """Downgrade schema.

    Fails if any workflow-origin ticket (case_id IS NULL) exists - restoring NOT NULL on case_id
    would leave such a row invalid. Resolve those rows (or accept losing them) before downgrading.
    """
    op.drop_constraint("ck_retraining_tickets_case_or_sample", "retraining_tickets", type_="check")
    op.drop_column("retraining_tickets", "sample_ref")
    op.alter_column(
        "retraining_tickets", "case_id", existing_type=sa.String(), nullable=False
    )
