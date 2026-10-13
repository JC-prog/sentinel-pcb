"""add inspection drafts (an inspection waiting to become a Case)

inspect_image no longer saves a Case on its own: the user is asked whether they want one, and only a
later chat turn that confirms it creates the Case. What the inspection found waits in this table, one
row per (conversation, user), until then.

Revision ID: b5c1f8a3d742
Revises: a9d4e6b2c718
Create Date: 2026-10-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5c1f8a3d742"
down_revision: str | Sequence[str] | None = "a9d4e6b2c718"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "inspection_drafts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id", "user_id", name="uq_inspection_drafts_conversation_user"
        ),
    )


def downgrade() -> None:
    """Downgrade schema. Drops any inspections still waiting for the user's answer."""
    op.drop_table("inspection_drafts")
