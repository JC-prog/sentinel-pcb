"""Auth models shared by every module. Importing this package registers them on Base.metadata.
Each module's own models package (e.g. app/chat/db/models/) does the same for its tables - whoever
runs `create_all` (app/main.py, alembic/env.py, tests/conftest.py) must import all of them, or the
missing tables silently never get created.
"""

from app.shared.db.models.auth import RefreshToken, User, UserRole

__all__ = ["RefreshToken", "User", "UserRole"]
