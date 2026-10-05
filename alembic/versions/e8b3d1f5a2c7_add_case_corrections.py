"""add case corrections (relabel) and pending relabel proposals

A QA/Admin reviewer can say the model's defect label on a Case was wrong and give the right one
(the relabel agent). The correction is recorded on the Case itself - corrected_label and who/when/
why - and a RetrainingTicket carries it to retraining. A relabel is a two-turn action (propose,
then confirm), so the proposal waits on the Case in the pending_* columns until it is confirmed,
replaced or discarded.

Revision ID: e8b3d1f5a2c7
Revises: d7e2a4b9c015
Create Date: 2026-10-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8b3d1f5a2c7"
down_revision: str | Sequence[str] | None = "d7e2a4b9c015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("cases", sa.Column("corrected_label", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("corrected_by_user_id", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cases", sa.Column("correction_reason", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("pending_label", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("pending_reason", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("pending_by_user_id", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("pending_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_cases_corrected_by_user_id_users", "cases", "users", ["corrected_by_user_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_cases_pending_by_user_id_users", "cases", "users", ["pending_by_user_id"], ["id"]
    )
    op.create_index("ix_cases_corrected_at", "cases", ["corrected_at"])


def downgrade() -> None:
    """Downgrade schema. Drops every recorded correction; the RetrainingTickets they created keep
    their own copy of the corrected label."""
    op.drop_index("ix_cases_corrected_at", table_name="cases")
    op.drop_constraint("fk_cases_pending_by_user_id_users", "cases", type_="foreignkey")
    op.drop_constraint("fk_cases_corrected_by_user_id_users", "cases", type_="foreignkey")
    for column in (
        "pending_at",
        "pending_by_user_id",
        "pending_reason",
        "pending_label",
        "correction_reason",
        "corrected_at",
        "corrected_by_user_id",
        "corrected_label",
    ):
        op.drop_column("cases", column)
