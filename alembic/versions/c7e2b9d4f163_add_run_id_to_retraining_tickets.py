"""add run_id to retraining tickets

A Work-tab sample id (S000001) restarts with every run, so a ticket that names only the sample can
collide with another run's - and could be filed twice. Tickets now also record the run they came
from, and a partial unique index on (run_id, sample_ref) makes queueing a run's correction
idempotent. Existing tickets and chat's (which point at a Case) have no run and are exempt.

Revision ID: c7e2b9d4f163
Revises: b5c1f8a3d742
Create Date: 2026-10-05 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e2b9d4f163"
down_revision: str | Sequence[str] | None = "b5c1f8a3d742"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("retraining_tickets", sa.Column("run_id", sa.String(), nullable=True))
    op.create_index(
        "uq_retraining_tickets_run_sample",
        "retraining_tickets",
        ["run_id", "sample_ref"],
        unique=True,
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_retraining_tickets_run_sample", table_name="retraining_tickets")
    op.drop_column("retraining_tickets", "run_id")
