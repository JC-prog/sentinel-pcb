"""Pure interfaces for chat - no framework/IO imports. Concrete implementations live in
app/chat/providers/ (ChatService) and app/chat/repository.py (persistence); app/chat/service.py's
get_chat_service() factory is the only place that constructs a ChatService.
"""

from collections.abc import AsyncGenerator
from typing import Any, Literal, Protocol

from pydantic import BaseModel


class ChatTurn(BaseModel):
    """One prior turn of conversation history, as loaded from app/db/models/chat.py's Message
    rows - not the ORM model itself, so providers stay decoupled from persistence."""

    role: Literal["user", "assistant"]
    content: str


class ToolCallRequest(BaseModel):
    """One tool invocation the model asked for - id is provider-supplied for OpenAI and
    synthesized (Ollama has none) for Ollama; see app/chat/providers/*.py's stream_with_tools."""

    id: str
    name: str
    arguments: dict[str, Any]


class ChatMessage(BaseModel):
    """A richer message than ChatTurn - only used within one chat turn's tool-calling round
    trips (app/main.py's _chat_sse), never persisted. ChatTurn/Message rows still only ever
    record the user's message and the final assistant reply, exactly as before this existed."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCallRequest] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class TextDelta(BaseModel):
    text: str


class ToolCallsReady(BaseModel):
    calls: list[ToolCallRequest]


ChatEvent = TextDelta | ToolCallsReady


class ChatService(Protocol):
    def stream_reply(
        self,
        history: list[ChatTurn],
        message: str,
        image_ids: list[str],
        system_prompt: str | None = None,
    ) -> AsyncGenerator[str, None]: ...

    def stream_with_tools(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
    ) -> AsyncGenerator[ChatEvent, None]: ...


class ConversationNotFound(Exception):
    """Raised when a conversation_id doesn't exist, or exists but belongs to a different user -
    the two cases are deliberately indistinguishable to the caller (see app/chat/history.py),
    same reasoning as returning a generic 404 rather than a 403 that would confirm the id exists."""
