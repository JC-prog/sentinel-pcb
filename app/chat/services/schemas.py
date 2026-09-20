from datetime import datetime
from typing import Literal

from pydantic import BaseModel

LlmProvider = Literal["ollama", "openai"]


class ChatStreamRequest(BaseModel):
    conversation_id: str
    message: str
    image_ids: list[str] = []
    # Inspection XML upload ids (app.chat.uploads, via POST /api/uploads/xml) - only consumed by
    # create_case (app/chat/agents/adc_inspection_agent/), optional there too. Not persisted on Message
    # like image_ids is - ephemeral to the tool call, not part of the message history.
    xml_ids: list[str] = []
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
