"""add messages.feedback

A user's verdict on an assistant reply ("up" or "down"), so whether the assistant is helping can be
measured at all. Existing rows have none.

Revision ID: a8d4f1c6e203
Revises: f6b2d8a4c931
Create Date: 2026-10-13 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8d4f1c6e203"
down_revision: str | Sequence[str] | None = "f6b2d8a4c931"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("messages", sa.Column("feedback", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "feedback")
