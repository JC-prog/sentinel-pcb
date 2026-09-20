from app.chat.core.chat import ChatService
from app.chat.services.providers.ollama import OllamaChatService
from app.chat.services.providers.openai import OpenAiChatService
from app.chat.services.schemas import LlmProvider

__all__ = ["ChatService", "get_chat_service"]


def get_chat_service(provider: LlmProvider) -> ChatService:
    if provider == "openai":
        return OpenAiChatService()
    return OllamaChatService()
