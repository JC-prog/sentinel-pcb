from datetime import datetime
from typing import Literal

from pydantic import BaseModel, SecretStr

LlmProvider = Literal["ollama", "openai"]


class ChatStreamRequest(BaseModel):
    conversation_id: str
    message: str
    image_ids: list[str] = []
    provider: LlmProvider = "ollama"
    # Bring-your-own-key: never persisted server-side, only used for this request. SecretStr
    # keeps it out of any accidental repr/log of the request object.
    openai_api_key: SecretStr | None = None


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
