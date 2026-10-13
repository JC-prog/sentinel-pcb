"""add inspection_log

One row per chat image inspection, kept whether or not anyone acts on it, so chat has a denominator:
how many were inspected, and how many a person confirmed were right. No image is stored.

Revision ID: f6b2d8a4c931
Revises: e5a9c3b7d216
Create Date: 2026-10-13 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6b2d8a4c931"
down_revision: str | Sequence[str] | None = "e5a9c3b7d216"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "inspection_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("board_id", sa.String(), nullable=True),
        sa.Column("component_ref", sa.String(), nullable=True),
        sa.Column("image_id", sa.String(), nullable=True),
        sa.Column("region", sa.String(), nullable=True),
        sa.Column("region_confidence", sa.Float(), nullable=True),
        sa.Column("region_model_version", sa.String(), nullable=True),
        sa.Column("defect_model", sa.String(), nullable=True),
        sa.Column("defect_model_version", sa.String(), nullable=True),
        sa.Column("defect_label", sa.String(), nullable=True),
        sa.Column("defect_confidence", sa.Float(), nullable=True),
        sa.Column("verdict", sa.String(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verified_by_user_id", sa.String(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["verified_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inspection_log_user_id", "inspection_log", ["user_id"])
    op.create_index("ix_inspection_log_created_at", "inspection_log", ["created_at"])
    op.create_index(
        "ix_inspection_log_defect_model_created_at",
        "inspection_log",
        ["defect_model", "created_at"],
    )
    op.create_index(
        "ix_inspection_log_conversation_user", "inspection_log", ["conversation_id", "user_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("inspection_log")
