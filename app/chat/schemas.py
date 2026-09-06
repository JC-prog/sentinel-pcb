from datetime import datetime
from typing import Literal

from pydantic import BaseModel

LlmProvider = Literal["ollama", "openai"]


class ChatStreamRequest(BaseModel):
    conversation_id: str
    message: str
    image_ids: list[str] = []
    provider: LlmProvider = "ollama"


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
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageOut]
