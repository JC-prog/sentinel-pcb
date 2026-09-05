"""Re-exports every model so `from app.db.models import X` keeps working, AND so importing this
package (which app/db/__init__.py always does) registers every model on Base.metadata before
init_models()/create_all runs. When a new per-domain models module is added, it MUST be imported
here too, or its tables silently never get created - create_all only knows about classes that
have actually been imported somewhere.
"""

from app.db.models.auth import RefreshToken, User, UserRole
from app.db.models.chat import Conversation, Message

__all__ = ["Conversation", "Message", "RefreshToken", "User", "UserRole"]
