"""add workflow_review_decisions

Revision ID: d7e2a4b9c015
Revises: c4a8e1f6b920
Create Date: 2026-10-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e2a4b9c015"
down_revision: str | Sequence[str] | None = "c4a8e1f6b920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "workflow_review_decisions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("sample_id", sa.String(), nullable=False),
        sa.Column("selected_source", sa.String(), nullable=False),
        sa.Column("final_result", sa.String(), nullable=False),
        sa.Column("machine_result", sa.String(), nullable=True),
        sa.Column("ai_result", sa.String(), nullable=True),
        sa.Column("ai_diagnosis", sa.Text(), nullable=True),
        sa.Column("operator_notes", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", sa.String(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sample_id", name="uq_workflow_review_decisions_run_sample"),
    )
    op.create_index(
        op.f("ix_workflow_review_decisions_run_id"),
        "workflow_review_decisions",
        ["run_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_workflow_review_decisions_run_id"), table_name="workflow_review_decisions"
    )
    op.drop_table("workflow_review_decisions")
