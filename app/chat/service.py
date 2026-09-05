from collections.abc import AsyncGenerator
from typing import Protocol

from pydantic import SecretStr

from app.chat.providers.ollama import OllamaChatService
from app.chat.providers.openai import OpenAiChatService
from app.chat.schemas import LlmProvider


class ChatService(Protocol):
    def stream_reply(self, message: str, image_ids: list[str]) -> AsyncGenerator[str, None]: ...


def get_chat_service(provider: LlmProvider, openai_api_key: SecretStr | None) -> ChatService:
    if provider == "openai":
        if openai_api_key is None:
            raise ValueError("openai_api_key is required when provider is 'openai'")
        return OpenAiChatService(api_key=openai_api_key)
    return OllamaChatService()
