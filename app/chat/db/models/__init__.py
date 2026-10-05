"""Chat-owned models (conversations, inspection cases).
Importing this package registers them on the shared `Base.metadata`; they reference the shared
`users` table by foreign key only, never by importing its model.
"""

from app.chat.db.models.case import Case, CaseStatus
from app.chat.db.models.case_draft import CaseDraft
from app.chat.db.models.chat import Conversation, Message

__all__ = [
    "Case",
    "CaseDraft",
    "CaseStatus",
    "Conversation",
    "Message",
]
