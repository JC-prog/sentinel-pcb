"""Chat-owned models (conversations, inspection cases, golden images).
Importing this package registers them on the shared `Base.metadata`; they reference the shared
`users` table by foreign key only, never by importing its model.
"""

from app.chat.db.models.case import Case, CaseStatus
from app.chat.db.models.chat import Conversation, Message
from app.chat.db.models.golden_image import GoldenImage

__all__ = [
    "Case",
    "CaseStatus",
    "Conversation",
    "GoldenImage",
    "Message",
]
