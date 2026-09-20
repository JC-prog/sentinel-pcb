"""Shared persistence plumbing: the declarative `Base`, the async engine/session, and the auth
models (`User`, `RefreshToken`) every module needs. Module-specific tables live with their module
(app/chat/db/) and register themselves on the same `Base.metadata` - see `init_models()` and
app/main.py, which imports every module's models before `create_all` runs.
"""

from app.shared.db.base import Base
from app.shared.db.models import RefreshToken, User, UserRole
from app.shared.db.session import engine, get_session, init_models

__all__ = [
    "Base",
    "RefreshToken",
    "User",
    "UserRole",
    "engine",
    "get_session",
    "init_models",
]
