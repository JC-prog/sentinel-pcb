"""add messages.tool_results and messages.interrupted

tool_results keeps the cards a reply showed so they survive a reload or another device;
interrupted marks a reply that was stopped (or failed) part-way, whose content is only what had
streamed. Existing rows get an empty list / false.

Revision ID: d4a1c6e8b2f7
Revises: b3d8f2a6c914
Create Date: 2026-10-13 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a1c6e8b2f7"
down_revision: str | Sequence[str] | None = "b3d8f2a6c914"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "messages",
        sa.Column(
            "tool_results",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "messages",
        sa.Column("interrupted", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "interrupted")
    op.drop_column("messages", "tool_results")
