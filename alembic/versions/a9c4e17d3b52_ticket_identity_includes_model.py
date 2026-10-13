"""retraining ticket identity includes the model

A reviewed Work-tab sample can now carry two corrections: its defect label (for a defect model) and
its part area (for the region model). The partial unique index that made queueing idempotent was on
(run_id, sample_ref); it is now on (run_id, sample_ref, model_name), so each model's ticket for a
sample is still filed once.

Revision ID: a9c4e17d3b52
Revises: c7e2b9d4f163
Create Date: 2026-10-12 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c4e17d3b52"
down_revision: str | Sequence[str] | None = "c7e2b9d4f163"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index("uq_retraining_tickets_run_sample", table_name="retraining_tickets")
    op.create_index(
        "uq_retraining_tickets_run_sample",
        "retraining_tickets",
        ["run_id", "sample_ref", "model_name"],
        unique=True,
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_retraining_tickets_run_sample", table_name="retraining_tickets")
    op.create_index(
        "uq_retraining_tickets_run_sample",
        "retraining_tickets",
        ["run_id", "sample_ref"],
        unique=True,
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )
