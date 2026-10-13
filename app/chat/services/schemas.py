from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

LlmProvider = Literal["openai"]


class ChatStreamRequest(BaseModel):
    conversation_id: str
    message: str
    image_ids: list[str] = []
    # Inspection XML upload ids (app.chat.uploads, via POST /api/uploads/xml) - only consumed by
    # inspect_image (app/chat/agents/inspection_agent/), optional there too. Not persisted on Message
    # like image_ids is - ephemeral to the tool call, not part of the message history.
    xml_ids: list[str] = []
    provider: LlmProvider = "openai"
    # The chat model picked in Settings (one of GET /api/chat/models). None or an unknown value uses
    # the server's default - a stale choice in someone's browser must not stop chat working.
    model: str | None = None


class ConversationSummary(BaseModel):
    """Sidebar-list shape - no messages, see ConversationDetail for that."""

    model_config = {"from_attributes": True}

    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    role: Literal["user", "assistant"]
    content: str
    image_ids: list[str]
    # The cards the reply showed (`{"name", "result"}`, as in the tool_result SSE frames).
    tool_results: list[dict[str, Any]] = []
    # Stopped or failed part-way: `content` is only what had streamed.
    interrupted: bool = False
    created_at: datetime


class ConversationRename(BaseModel):
    title: str


class ConversationDetail(ConversationSummary):
    messages: list[MessageOut]
