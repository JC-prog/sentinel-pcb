"""rename users name to username

users.name becomes users.username, now the field used to log in (app/auth/service.py's
authenticate_user) instead of email - see app/db/models/auth.py. Existing values carry over via
the rename itself (no data loss), but the new unique index means this will fail if any two rows
already share the same name - unlike the baseline migration, this one's upgrade() actually runs
against real databases, so that's a real caveat for any deployment with duplicate names, not just
a theoretical one.

Revision ID: 748124a3cba1
Revises: 8fc38363ff3f
Create Date: 2026-09-06 17:57:09.956726

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "748124a3cba1"
down_revision: str | Sequence[str] | None = "8fc38363ff3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("users", "name", new_column_name="username")
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.alter_column("users", "username", new_column_name="name")
