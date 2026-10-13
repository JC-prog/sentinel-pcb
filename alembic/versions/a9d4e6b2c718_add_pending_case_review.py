"""add a pending case review (approve / override) to cases

A reviewer's approve-or-override of a REVIEW_REQUIRED case is a two-turn action in chat (the review
agent proposes it, a later turn confirms it), so the proposal waits on the Case in the
pending_resolution* columns until it is confirmed or replaced. Confirming it sets the existing
status / resolved_by_user_id / resolved_at / resolution_note columns.

Revision ID: a9d4e6b2c718
Revises: f2a7c4e9d013
Create Date: 2026-10-04 12:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9d4e6b2c718"
down_revision: str | Sequence[str] | None = "f2a7c4e9d013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("cases", sa.Column("pending_resolution", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("pending_resolution_note", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("pending_resolution_by_user_id", sa.String(), nullable=True))
    op.add_column(
        "cases", sa.Column("pending_resolution_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_cases_pending_resolution_by_user_id_users",
        "cases",
        "users",
        ["pending_resolution_by_user_id"],
        ["id"],
    )


def downgrade() -> None:
    """Downgrade schema. Drops any proposals still waiting for confirmation."""
    op.drop_constraint("fk_cases_pending_resolution_by_user_id_users", "cases", type_="foreignkey")
    for column in (
        "pending_resolution_at",
        "pending_resolution_by_user_id",
        "pending_resolution_note",
        "pending_resolution",
    ):
        op.drop_column("cases", column)
