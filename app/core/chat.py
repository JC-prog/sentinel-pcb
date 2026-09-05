"""Pure interfaces for chat - no framework/IO imports. Concrete implementations live in
app/chat/providers/ (ChatService) and app/chat/repository.py (persistence); app/chat/service.py's
get_chat_service() factory is the only place that constructs a ChatService.
"""

from collections.abc import AsyncGenerator
from typing import Literal, Protocol

from pydantic import BaseModel


class ChatTurn(BaseModel):
    """One prior turn of conversation history, as loaded from app/db/models/chat.py's Message
    rows - not the ORM model itself, so providers stay decoupled from persistence."""

    role: Literal["user", "assistant"]
    content: str


class ChatService(Protocol):
    def stream_reply(
        self,
        history: list[ChatTurn],
        message: str,
        image_ids: list[str],
        system_prompt: str | None = None,
    ) -> AsyncGenerator[str, None]: ...


class ConversationNotFound(Exception):
    """Raised when a conversation_id doesn't exist, or exists but belongs to a different user -
    the two cases are deliberately indistinguishable to the caller (see app/chat/history.py),
    same reasoning as returning a generic 404 rather than a 403 that would confirm the id exists."""
