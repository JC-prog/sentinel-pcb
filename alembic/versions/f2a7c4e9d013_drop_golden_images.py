"""drop the golden-image bank

The golden reference image bank (an admin upload endpoint and the `golden_images` table, keyed by
board / component / package / feature) existed so the inspect agent could align a case image against
a known-good reference. That check is gone from the inspect agent, and nothing else reads the bank,
so the table, the admin endpoint and `cases.golden_image_id` (always NULL since the check went) are
removed.

Downgrade recreates the empty structures; any rows that existed are not restored. Image files the
admin endpoint wrote under `data/golden_images/` are not touched by either direction.

Revision ID: f2a7c4e9d013
Revises: e8b3d1f5a2c7
Create Date: 2026-10-04 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a7c4e9d013"
down_revision: str | Sequence[str] | None = "e8b3d1f5a2c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("cases_golden_image_id_fkey", "cases", type_="foreignkey")
    op.drop_column("cases", "golden_image_id")
    op.drop_index(op.f("ix_golden_images_component_ref"), table_name="golden_images")
    op.drop_index(op.f("ix_golden_images_board_id"), table_name="golden_images")
    op.drop_table("golden_images")


def downgrade() -> None:
    """Downgrade schema (structure only - dropped rows are not restored)."""
    op.create_table(
        "golden_images",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("board_id", sa.String(), nullable=False),
        sa.Column("component_ref", sa.String(), nullable=False),
        sa.Column("package", sa.String(), nullable=False),
        sa.Column("feature", sa.String(), nullable=False),
        sa.Column("stored_filename", sa.String(), nullable=False),
        sa.Column("registered_by_user_id", sa.String(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["registered_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "board_id", "component_ref", "package", "feature", name="uq_golden_images_lookup_key"
        ),
    )
    op.create_index(op.f("ix_golden_images_board_id"), "golden_images", ["board_id"], unique=False)
    op.create_index(
        op.f("ix_golden_images_component_ref"), "golden_images", ["component_ref"], unique=False
    )
    op.add_column("cases", sa.Column("golden_image_id", sa.String(), nullable=True))
    op.create_foreign_key(
        "cases_golden_image_id_fkey", "cases", "golden_images", ["golden_image_id"], ["id"]
    )
