"""Pure chat types - no framework/IO imports. Persistence lives in app/chat/services/repository.py;
the chat models themselves are LangChain models built by app/shared/config/llm.py.
"""

from typing import Literal

from pydantic import BaseModel


class ChatTurn(BaseModel):
    """One prior turn of conversation history, as loaded from app/chat/db/models/chat.py's Message
    rows - not the ORM model itself, so the chat turn stays decoupled from persistence."""

    role: Literal["user", "assistant"]
    content: str


class ConversationNotFound(Exception):
    """Raised when a conversation_id doesn't exist, or exists but belongs to a different user -
    the two cases are deliberately indistinguishable to the caller (see app/chat/services/history.py),
    same reasoning as returning a generic 404 rather than a 403 that would confirm the id exists."""
