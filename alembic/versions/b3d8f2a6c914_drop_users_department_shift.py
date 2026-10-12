"""drop users.department_shift

A department or shift is not needed to register, so the column (and the registration field) are
gone. Existing values are discarded; downgrade re-adds the column as nullable.

Revision ID: b3d8f2a6c914
Revises: a9c4e17d3b52
Create Date: 2026-10-12 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d8f2a6c914"
down_revision: str | Sequence[str] | None = "a9c4e17d3b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("users", "department_shift")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("users", sa.Column("department_shift", sa.String(), nullable=True))
