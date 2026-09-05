from pydantic import SecretStr

from app.chat.providers.ollama import OllamaChatService
from app.chat.providers.openai import OpenAiChatService
from app.chat.schemas import LlmProvider
from app.core.chat import ChatService

__all__ = ["ChatService", "get_chat_service"]


def get_chat_service(provider: LlmProvider, openai_api_key: SecretStr | None) -> ChatService:
    if provider == "openai":
        if openai_api_key is None:
            raise ValueError("openai_api_key is required when provider is 'openai'")
        return OpenAiChatService(api_key=openai_api_key)
    return OllamaChatService()
