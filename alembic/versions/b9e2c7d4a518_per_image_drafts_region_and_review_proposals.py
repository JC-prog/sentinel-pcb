"""per-image inspection drafts, region corrections, review proposals on drafts

- inspection_drafts: one per (conversation, user, image_number) instead of one per (conversation,
  user), so every image inspected in a turn can be corrected, reviewed or saved. Existing drafts
  are image 1.
- inspection_drafts.pending_resolution*: an approve/override proposed on an inspection that is not
  a Case yet; confirming it creates the Case already resolved.
- cases/inspection_drafts .pending_model, cases.corrected_model: which model a relabel corrects -
  the defect model, or pcb_region when the region was wrong. Existing corrections are of the
  defect model and are backfilled so.

Revision ID: b9e2c7d4a518
Revises: a8d4f1c6e203
Create Date: 2026-10-14 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9e2c7d4a518"
down_revision: str | Sequence[str] | None = "a8d4f1c6e203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "inspection_drafts",
        sa.Column("image_number", sa.Integer(), nullable=False, server_default="1"),
    )
    op.drop_constraint(
        "uq_inspection_drafts_conversation_user", "inspection_drafts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_inspection_drafts_conversation_user_image",
        "inspection_drafts",
        ["conversation_id", "user_id", "image_number"],
    )
    op.add_column("inspection_drafts", sa.Column("pending_model", sa.String(), nullable=True))
    op.add_column("inspection_drafts", sa.Column("pending_resolution", sa.String(), nullable=True))
    op.add_column(
        "inspection_drafts", sa.Column("pending_resolution_note", sa.String(), nullable=True)
    )
    op.add_column(
        "inspection_drafts",
        sa.Column("pending_resolution_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("cases", sa.Column("pending_model", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("corrected_model", sa.String(), nullable=True))
    op.execute(
        "UPDATE cases SET corrected_model = defect_model WHERE corrected_label IS NOT NULL"
    )
    op.execute("UPDATE cases SET pending_model = defect_model WHERE pending_label IS NOT NULL")
    op.execute(
        "UPDATE inspection_drafts SET pending_model = payload->>'defect_model' "
        "WHERE pending_label IS NOT NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("cases", "corrected_model")
    op.drop_column("cases", "pending_model")
    op.drop_column("inspection_drafts", "pending_resolution_at")
    op.drop_column("inspection_drafts", "pending_resolution_note")
    op.drop_column("inspection_drafts", "pending_resolution")
    op.drop_column("inspection_drafts", "pending_model")
    op.drop_constraint(
        "uq_inspection_drafts_conversation_user_image", "inspection_drafts", type_="unique"
    )
    # Drafts from different turns never coexist (a later turn replaces them), so keeping image 1
    # leaves one per (conversation, user) and the old constraint can be restored.
    op.execute("DELETE FROM inspection_drafts WHERE image_number <> 1")
    op.create_unique_constraint(
        "uq_inspection_drafts_conversation_user",
        "inspection_drafts",
        ["conversation_id", "user_id"],
    )
    op.drop_column("inspection_drafts", "image_number")
