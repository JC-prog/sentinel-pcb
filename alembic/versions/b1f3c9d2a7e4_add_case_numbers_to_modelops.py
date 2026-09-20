"""add case numbers to retraining tickets and drift reports

The human-readable case number ("CASE-000123") lives in chat's cases table, which the shared
model-operations code and the Models tab can't read - so it is copied onto the rows that need to
show it.

Revision ID: b1f3c9d2a7e4
Revises: 74e67cfd3e83
Create Date: 2026-09-21 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1f3c9d2a7e4"
down_revision: str | Sequence[str] | None = "74e67cfd3e83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("retraining_tickets", sa.Column("case_number", sa.String(), nullable=True))
    op.add_column(
        "drift_reports",
        sa.Column(
            "case_numbers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("drift_reports", "case_numbers")
    op.drop_column("retraining_tickets", "case_number")
